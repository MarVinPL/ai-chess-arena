//! Android Worker: private, bounded APK staging only.
//! This module NEVER invokes an installer or claims APK authenticity.
//! Before installation, compare APK signing certificate, package ID,
//! versionCode and coordinator-confirmed idle in a separate native gate.

use crate::android_update_asset::{verify_apk_stream, ApkCandidate};
use reqwest::{redirect, Client, Url};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

static DOWNLOAD_IN_PROGRESS: AtomicBool = AtomicBool::new(false);

struct DownloadSlot;
impl DownloadSlot {
    fn acquire() -> Result<Self, String> {
        DOWNLOAD_IN_PROGRESS.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "Inne pobieranie aktualizacji już trwa".to_owned())?;
        Ok(Self)
    }
}
impl Drop for DownloadSlot {
    fn drop(&mut self) {
        DOWNLOAD_IN_PROGRESS.store(false, Ordering::Release);
    }
}
use tauri::{Emitter, Manager};

fn official_asset_redirect(url: &Url) -> bool {
    url.scheme() == "https"
        && url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && matches!(url.host_str(),
            Some("release-assets.githubusercontent.com" | "objects.githubusercontent.com"))
}

fn download_client() -> Result<Client, String> {
    Client::builder()
        .redirect(redirect::Policy::custom(|attempt| {
            if attempt.previous().len() > 3 || !official_asset_redirect(attempt.url()) {
                attempt.stop()
            } else {
                attempt.follow()
            }
        }))
        .connect_timeout(Duration::from_secs(12))
        .timeout(Duration::from_secs(180))
        .build()
        .map_err(|_| "Nie udało się utworzyć klienta pobierania".to_owned())
}

/// A plain callback keeps the transfer testable without an Android context.
pub(crate) async fn download_apk_to_private_dir<F: FnMut(u64, u64)>(
    client: &Client,
    candidate: &ApkCandidate,
    target: &Path,
    mut progress: F,
) -> Result<(), String> {
    // Only fully validated metadata candidates may reach this function.
    // The caller must obtain them via select_apk_candidate from GitHub, never
    // accept an arbitrary URL/path sent by JavaScript.
    let url = Url::parse(&candidate.download_url)
        .map_err(|_| "Nieprawidłowy adres APK")?;
    if url.scheme() != "https" || url.host_str() != Some("github.com")
        || url.port().is_some() || url.username() != ""
        || url.password().is_some() || url.query().is_some()
        || url.fragment().is_some() || !url.path().starts_with(
            "/MarVinPL/ai-chess-arena/releases/download/worker-v") {
        return Err("Nieoficjalny adres APK".into());
    }
    if candidate.size < 1024 || candidate.size > 200 * 1024 * 1024 {
        return Err("Nieprawidłowy rozmiar APK".into());
    }

    let mut response = client.get(url)
        .header(reqwest::header::USER_AGENT, "ai-chess-arena-worker-apk-staging")
        .send().await.map_err(|_| "Nie udało się pobrać APK")?;
    if !response.status().is_success() || !official_asset_redirect(response.url()) {
        return Err("Nieprawidłowa odpowiedź serwera APK".into());
    }
    if response.content_length().map(|n| n != candidate.size).unwrap_or(false) {
        return Err("Rozmiar pobranego APK nie zgadza się z wydaniem".into());
    }

    // create_new prevents overwriting cached files or following a file symlink.
    let temporary = target.with_extension("apk.partial");
    let mut file = OpenOptions::new().write(true).create_new(true)
        .open(&temporary).map_err(|_| "Nie można utworzyć prywatnego pliku APK")?;
    let result = async {
        let mut received = 0u64;
        while let Some(chunk) = response.chunk().await
            .map_err(|_| "Przerwano pobieranie APK")? {
            received = received.checked_add(chunk.len() as u64)
                .ok_or("Przekroczono limit APK")?;
            if received > candidate.size { return Err("APK przekracza dozwolony rozmiar".into()); }
            file.write_all(&chunk).map_err(|_| "Błąd zapisu APK")?;
            progress(received, candidate.size);
        }
        if received != candidate.size {
            return Err("Pobrany APK jest niekompletny".into());
        }
        file.flush().map_err(|_| "Błąd zapisu APK")?;
        file.sync_all().map_err(|_| "Błąd zapisu APK")?;
        drop(file);
        let mut staged = fs::File::open(&temporary)
            .map_err(|_| "Nie można ponownie odczytać APK")?;
        if !verify_apk_stream(&mut staged, candidate)
            .map_err(|_| "Błąd weryfikacji SHA-256")? {
            return Err("Nieprawidłowa suma SHA-256 APK".into());
        }
        drop(staged);
        if target.exists() {
            return Err("W prywatnym katalogu jest już ten plik aktualizacji".into());
        }
        fs::rename(&temporary, target).map_err(|_| "Nie można zachować APK")?;
        Ok(())
    }.await;
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub(crate) async fn stage_candidate(
    app: &tauri::AppHandle,
    candidate: ApkCandidate,
    tag: &str,
) -> Result<(), String> {
    let _download_slot = DownloadSlot::acquire()?;
    let root = app.path().app_cache_dir()
        .map_err(|_| "Brak prywatnego katalogu aktualizacji")?
        .join("worker-apk-updates");
    fs::create_dir_all(&root).map_err(|_| "Nie można przygotować katalogu APK")?;
    let target = root.join(format!("{tag}.apk"));
    // A previously verified download is reusable; corrupted or stale cache
    // is removed before retry. A symlink is never accepted as a staged APK.
    if let Ok(metadata) = fs::symlink_metadata(&target) {
        let existing_valid = metadata.file_type().is_file()
            && fs::File::open(&target)
                .ok().and_then(|mut f| verify_apk_stream(&mut f, &candidate).ok())
                == Some(true);
        if existing_valid { return Ok(()); }
        if !metadata.file_type().is_file() {
            return Err("Nieprawidłowy typ pliku APK w pamięci podręcznej".into());
        }
        fs::remove_file(&target).map_err(|_| "Nie można usunąć uszkodzonego APK")?;
    }
    // Only one transfer can run within this process; a leftover .partial
    // after a previous process crash can be safely removed before retry.
    let partial = target.with_extension("apk.partial");
    if partial.exists() {
        fs::remove_file(&partial).map_err(|_| "Nie można wyczyścić przerwanego pobierania")?;
    }
    let client = download_client()?;
    download_apk_to_private_dir(&client, &candidate, &target, |received, total| {
        let _ = app.emit("worker-apk-download-progress",
            serde_json::json!({"received":received, "total":total}));
    }).await?;
    let _ = app.emit("worker-apk-download-progress",
        serde_json::json!({"received":candidate.size,"total":candidate.size,"verified":true}));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_one_staging_transfer_can_hold_the_lock() {
        let first = DownloadSlot::acquire().unwrap();
        assert!(DownloadSlot::acquire().is_err());
        drop(first);
        assert!(DownloadSlot::acquire().is_ok());
    }

    #[test]
    fn redirect_restrictions_reject_insecure_or_non_github_destinations() {
        for raw in [
            "http://release-assets.githubusercontent.com/a",
            "https://evil.test/a",
            "https://github.com/MarVinPL/ai-chess-arena/a",
            "https://release-assets.githubusercontent.com.evil.test/a",
            "https://user@objects.githubusercontent.com/a",
            "https://objects.githubusercontent.com:8443/a",
        ] {
            assert!(!official_asset_redirect(&Url::parse(raw).unwrap()), "{raw}");
        }
        assert!(official_asset_redirect(&Url::parse(
            "https://release-assets.githubusercontent.com/a?token=x").unwrap()));
    }
}
