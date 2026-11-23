//! Bounded, verified GGUF transfer from Arena-approved pinned HF metadata.
//! This is an INTERNAL transport primitive, not a Tauri/JS command. It is
//! deliberately not wired to dispatch until the model registry and live
//! execution lease lifecycle are implemented.
use crate::android_model_cache::{model_path, verify_model};
use crate::android_model_source::{validate_model_source, ApprovedGguf};
use reqwest::{redirect, Client, Url};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

static ACTIVE_TRANSFER: AtomicBool = AtomicBool::new(false);
struct TransferGuard;
impl TransferGuard {
    fn new() -> Result<Self, String> {
        ACTIVE_TRANSFER.compare_exchange(false, true,
            Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "Inne pobieranie GGUF już trwa".to_string())?;
        Ok(Self)
    }
}
impl Drop for TransferGuard {
    fn drop(&mut self) { ACTIVE_TRANSFER.store(false, Ordering::Release); }
}

/// Reconcile a crash-left partial download for one exact approved model.
/// The trusted caller must independently verify that the coordinator has no
/// active lease, that no model load is using the file, and that the previous
/// execution is no longer running. Not callable by WebView; no broad cleanup.
pub(crate) fn reconcile_partial_after_restart(
    root: &Path,
    approved: &ApprovedGguf,
    active_reference: bool,
    previous_execution_stopped_verified: bool,
) -> Result<bool, String> {
    let _lock = TransferGuard::new()?;
    validate_model_source(approved)?;
    if active_reference || !previous_execution_stopped_verified {
        return Err("Niepotwierdzony STOP lub aktywna sesja — cache zablokowany".into());
    }
    let root_meta = fs::symlink_metadata(root)
        .map_err(|_| "Brak prywatnego katalogu GGUF")?;
    if !root_meta.file_type().is_dir() {
        return Err("Nieprawidłowy katalog GGUF".into());
    }
    let partial = root.join(format!("{}.gguf.partial", approved.sha256));
    match fs::symlink_metadata(&partial) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Ok(meta) if meta.file_type().is_file() => {
            // File in use? The coordinator must prove no active writer/reader;
            // this routine does not derive a trusted STOP from a process ID.
            fs::remove_file(&partial)
                .map_err(|_| "Nie można uzgodnić częściowego pobierania")?;
            Ok(true)
        }
        Ok(_) => Err("Odmowa usunięcia symlinku/katalogu w cache".into()),
        Err(_) => Err("Nie można odczytać częściowego modelu".into()),
    }
}

fn allowed_redirect(url: &Url) -> bool {
    url.scheme() == "https" && url.port().is_none()
        && url.username().is_empty() && url.password().is_none()
        && matches!(url.host_str(), Some(
            "cdn-lfs.hf.co" | "cas-bridge.xethub.hf.co" |
            "transfer.xethub.hf.co" | "huggingface.co"))
}
fn client() -> Result<Client, String> {
    Client::builder()
        .redirect(redirect::Policy::custom(|attempt| {
            if attempt.previous().len() >= 3 || !allowed_redirect(attempt.url()) {
                attempt.stop()
            } else {
                attempt.follow()
            }
        }))
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(60 * 60 * 2))
        .build()
        .map_err(|_| "Nie można skonfigurować bezpiecznego transferu GGUF".into())
}

/// Only a trusted caller can provide approved metadata and a private root.
/// A closure reads usable bytes on the same volume as the private cache.
/// Downloads are serialized, and no automatic eviction occurs here.
pub(crate) async fn download_approved_model<F, P>(
    root: &Path,
    approved: &ApprovedGguf,
    reserve_bytes: u64,
    mut free_space: F,
    mut progress: P,
) -> Result<PathBuf, String>
where
    F: FnMut() -> Result<u64, String>,
    P: FnMut(u64, u64),
{
    let _lock = TransferGuard::new()?;
    let url = validate_model_source(approved)?;
    let metadata = fs::symlink_metadata(root)
        .map_err(|_| "Prywatny katalog GGUF nie istnieje")?;
    if !metadata.file_type().is_dir() {
        return Err("Prywatny katalog GGUF jest nieprawidłowy".into());
    }
    let target = model_path(root, &approved.sha256)?;
    match fs::symlink_metadata(&target) {
        Ok(meta) if meta.file_type().is_file() => {
            if meta.len() == approved.size_bytes {
                let mut existing = File::open(&target)
                    .map_err(|_| "Nie można odczytać cache GGUF")?;
                if verify_model(&mut existing, approved.size_bytes, &approved.sha256)
                    .map_err(|_| "Błąd sprawdzania cache GGUF")? {
                    return Ok(target);
                }
            }
            return Err("Nieprawidłowy model w cache — wymagane uzgodnienie".into());
        }
        Ok(_) => return Err("Niedozwolony typ pliku w cache GGUF".into()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err("Nie można odczytać cache GGUF".into()),
    }
    let required = approved.size_bytes.checked_add(reserve_bytes)
        .ok_or("Przekroczono pojemność pamięci GGUF")?;
    if free_space()? < required {
        return Err("insufficient_storage: Brak miejsca na model i rezerwę".into());
    }
    // Never overwrite a partial file left by a crash, or follow symlinks.
    // A separate trusted recovery workflow may clear only a stale .partial.
    let partial = root.join(format!("{}.gguf.partial", approved.sha256));
    let mut file = OpenOptions::new().write(true).create_new(true)
        .open(&partial).map_err(|_| "Niedokończony transfer wymaga uzgodnienia")?;
    let result = async {
        let mut response = client()?.get(url)
            .header(reqwest::header::USER_AGENT, "ai-chess-arena-native-gguf")
            .send().await.map_err(|_| "Nie udało się pobrać GGUF")?;
        if !response.status().is_success() || !allowed_redirect(response.url()) {
            return Err("Serwer GGUF zwrócił niezaufaną odpowiedź".into());
        }
        if response.content_length().map(|n| n != approved.size_bytes).unwrap_or(false) {
            return Err("Niezgodny rozmiar odpowiedzi GGUF".into());
        }
        let mut received = 0u64;
        while let Some(chunk) = response.chunk().await
            .map_err(|_| "Przerwany transfer modelu")? {
            received = received.checked_add(chunk.len() as u64)
                .ok_or("Przekroczono rozmiar modelu")?;
            if received > approved.size_bytes {
                return Err("GGUF przekroczył zatwierdzony rozmiar".into());
            }
            let remaining = approved.size_bytes - received;
            let needed = remaining.checked_add(reserve_bytes)
                .ok_or("Przekroczona rezerwa pamięci")?;
            if free_space()? < needed {
                return Err("insufficient_storage: Brak miejsca w trakcie pobierania".into());
            }
            file.write_all(&chunk).map_err(|_| "Błąd zapisu GGUF")?;
            progress(received, approved.size_bytes);
        }
        if received != approved.size_bytes {
            return Err("GGUF pobrany niekompletnie".into());
        }
        file.flush().map_err(|_| "Błąd flush GGUF")?;
        file.sync_all().map_err(|_| "Błąd sync GGUF")?;
        drop(file);
        let mut check = File::open(&partial)
            .map_err(|_| "Nie można ponownie odczytać GGUF")?;
        if !verify_model(&mut check, approved.size_bytes, &approved.sha256)
            .map_err(|_| "Błąd weryfikacji GGUF")? {
            return Err("Niezgodny SHA-256 pobranego GGUF".into());
        }
        drop(check);
        if free_space()? < reserve_bytes {
            return Err("insufficient_storage: Rezerwa miejsca została naruszona".into());
        }
        match fs::symlink_metadata(&target) {
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
            _ => return Err("Model docelowy już istnieje lub jest niedostępny".into()),
        }
        fs::rename(&partial, &target).map_err(|_| "Błąd publikacji GGUF")?;
        Ok(target.clone())
    }.await;
    if result.is_err() {
        let _ = fs::remove_file(&partial);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    // Test cases exercise the same process-wide transfer guard; serialize
    // only those fixtures, without weakening production exclusion.
    static TEST_MUTEX: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn stale_partial_requires_verified_stop_and_never_removes_other_files() {
        let _serial = TEST_MUTEX.lock().unwrap();
        use sha2::{Digest, Sha256};
        let serial = format!("{}-{:?}", std::process::id(),
            std::thread::current().id());
        let root = std::env::temp_dir().join(format!(
            "arena-download-reconcile-{serial}-{}", 
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir(&root).unwrap();
        let data = b"GGUFcached-partial";
        let approved = ApprovedGguf {
            sha256: format!("{:x}", Sha256::digest(data)),
            size_bytes: data.len() as u64,
            source_url: format!("https://huggingface.co/owner/repo/resolve/{}/model.gguf",
                "1".repeat(40)),
        };
        let partial = root.join(format!("{}.gguf.partial", approved.sha256));
        let foreign = root.join("user-document.txt");
        fs::write(&partial, data).unwrap();
        fs::write(&foreign, b"important").unwrap();
        assert!(reconcile_partial_after_restart(
            &root, &approved, true, true).is_err());
        assert!(reconcile_partial_after_restart(
            &root, &approved, false, false).is_err());
        assert!(partial.exists());
        assert!(reconcile_partial_after_restart(
            &root, &approved, false, true).unwrap());
        assert!(!partial.exists());
        assert!(!reconcile_partial_after_restart(
            &root, &approved, false, true).unwrap());
        assert_eq!(fs::read(&foreign).unwrap(), b"important");
        fs::remove_file(foreign).unwrap();
        fs::remove_dir(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn crash_recovery_never_follows_partial_symlink() {
        let _serial = TEST_MUTEX.lock().unwrap();
        let root = std::env::temp_dir().join(format!(
            "arena-partial-symlink-{}-{}", std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir(&root).unwrap();
        let approved = ApprovedGguf {
            sha256: "a".repeat(64), size_bytes: 4096,
            source_url: format!("https://huggingface.co/owner/repo/resolve/{}/model.gguf",
                "1".repeat(40)),
        };
        let foreign = root.join("user-document.txt");
        fs::write(&foreign, b"private").unwrap();
        let partial = root.join(format!("{}.gguf.partial", approved.sha256));
        std::os::unix::fs::symlink(&foreign, &partial).unwrap();
        assert!(reconcile_partial_after_restart(&root, &approved, false, true).is_err());
        assert_eq!(fs::read(&foreign).unwrap(), b"private");
        fs::remove_file(partial).unwrap();
        fs::remove_file(foreign).unwrap();
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn redirects_are_only_to_known_https_model_storage() {
        for host in [
            "cdn-lfs.hf.co", "cas-bridge.xethub.hf.co",
            "transfer.xethub.hf.co", "huggingface.co",
        ] {
            assert!(allowed_redirect(&Url::parse(
                &format!("https://{host}/file?token=opaque")).unwrap()));
        }
        for raw in [
            "http://cdn-lfs.hf.co/path", "https://attacker.invalid/path",
            "https://cdn-lfs.hf.co.evil.invalid/path",
            "https://user@cas-bridge.xethub.hf.co/file",
            "https://huggingface.co:8443/path",
        ] {
            assert!(!allowed_redirect(&Url::parse(raw).unwrap()), "{raw}");
        }
    }
    #[test]
    fn in_process_download_lock_is_exclusive() {
        let _serial = TEST_MUTEX.lock().unwrap();
        let first = TransferGuard::new().unwrap();
        assert!(TransferGuard::new().is_err());
        drop(first);
        assert!(TransferGuard::new().is_ok());
    }
}
