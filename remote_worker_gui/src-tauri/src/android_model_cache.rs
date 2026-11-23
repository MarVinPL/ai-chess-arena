//! Private GGUF cache primitives for the planned embedded Android llama.cpp.
//! No Termux access, no model discovery and no automated job dispatch.
//! Only a trusted model registry may supply its exact SHA-256, expected size
//! and download source. This module never derives trust from a server alias.
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

pub(crate) const MAX_MODEL_BYTES: u64 = 16 * 1024 * 1024 * 1024;
const MIN_MODEL_BYTES: u64 = 4;

pub(crate) fn valid_digest(digest: &str) -> bool {
    digest.len() == 64 && digest.bytes().all(|b|
        b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The caller must pass an Android app-private directory, never a directory
/// chosen by the WebView or a model response. Names are content-addressed.
pub(crate) fn model_path(root: &Path, digest: &str) -> Result<PathBuf, String> {
    if !valid_digest(digest) { return Err("Nieprawidłowy SHA-256 modelu".into()); }
    Ok(root.join(format!("{digest}.gguf")))
}

fn valid_size(size: u64) -> bool {
    (MIN_MODEL_BYTES..=MAX_MODEL_BYTES).contains(&size)
}

/// Verify GGUF signature, exact byte size and actual streamed content digest.
/// A matching llama-server alias alone cannot pass this check.
pub(crate) fn verify_model<R: Read>(input: &mut R, size: u64, digest: &str)
    -> io::Result<bool> {
    if !valid_size(size) || !valid_digest(digest) { return Ok(false); }
    let mut hash = Sha256::new();
    let mut count = 0u64;
    let mut prefix = [0u8; 4];
    let mut prefix_read = 0usize;
    let mut buf = [0u8; 64 * 1024];
    loop {
        let cap = buf.len().min((size - count).saturating_add(1) as usize);
        let n = input.read(&mut buf[..cap])?;
        if n == 0 { break; }
        count += n as u64;
        if count > size { return Ok(false); }
        let needed = prefix.len() - prefix_read;
        let take = needed.min(n);
        prefix[prefix_read..prefix_read+take].copy_from_slice(&buf[..take]);
        prefix_read += take;
        hash.update(&buf[..n]);
    }
    Ok(count == size && prefix == *b"GGUF" &&
        format!("{:x}", hash.finalize()) == digest)
}

fn regular_file(path: &Path) -> Result<Option<fs::Metadata>, String> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_file() => Ok(Some(meta)),
        Ok(_) => Err("Niedozwolony typ pliku GGUF (np. symlink)".into()),
        Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err("Nie można sprawdzić modelu GGUF".into()),
    }
}

/// An entry is eligible for eviction only after the trusted coordinator
/// reports no active lease, session, checkpoint or installation referring to
/// it. The UI must never set these flags based on untrusted model metadata.
#[derive(Debug, Clone)]
pub(crate) struct CacheEntry {
    pub(crate) digest: String,
    pub(crate) size_bytes: u64,
    pub(crate) last_used_ms: u64,
    pub(crate) in_use: bool,
    pub(crate) preserve: bool,
}

pub(crate) const DEFAULT_FREE_RESERVE_BYTES: u64 = 10 * 1024 * 1024 * 1024;

/// Planning is pure: no files are deleted from this function. The downloader
/// must re-check actual free bytes after each verified deletion, and again
/// immediately before committing the transfer. The trusted coordinator is
/// responsible for refreshing active leases under its own lock.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct StoragePlan {
    pub(crate) delete_digests: Vec<String>,
    pub(crate) reclaimable_bytes: u64,
    pub(crate) still_missing_bytes: u64,
}

pub(crate) fn plan_storage_reclamation(
    free_bytes: u64,
    incoming_bytes: u64,
    reserve_bytes: u64,
    entries: &[CacheEntry],
) -> Result<StoragePlan, String> {
    if !valid_size(incoming_bytes) {
        return Err("Nieprawidłowy rozmiar pobieranego modelu".into());
    }
    // Download is written to a .partial file, then renamed in-place.
    // The caller must include any simultaneous temporary-file budget
    // in reserve_bytes and serialize transfers across Worker sessions.
    let required = incoming_bytes.checked_add(reserve_bytes)
        .ok_or("Zbyt duża rezerwa miejsca")?;
    let shortage = required.saturating_sub(free_bytes);
    if shortage == 0 {
        return Ok(StoragePlan {
            delete_digests: Vec::new(), reclaimable_bytes: 0,
            still_missing_bytes: 0,
        });
    }
    // Ensure the plan never double-counts the same cached model. Invalid
    // metadata is a hard failure, not a reason to delete arbitrary files.
    let mut seen = std::collections::HashSet::new();
    for entry in entries {
        if !valid_digest(&entry.digest) || !valid_size(entry.size_bytes)
            || !seen.insert(&entry.digest) {
            return Err("Nieprawidłowy indeks cache GGUF".into());
        }
    }
    let mut eligible: Vec<_> = entries.iter()
        .filter(|entry| !entry.in_use && !entry.preserve)
        .collect();
    eligible.sort_by(|a,b| a.last_used_ms.cmp(&b.last_used_ms)
        .then_with(|| a.digest.cmp(&b.digest)));
    let mut reclaimed = 0u64;
    let mut delete_digests = Vec::new();
    for entry in eligible {
        if reclaimed >= shortage { break; }
        reclaimed = reclaimed.saturating_add(entry.size_bytes);
        delete_digests.push(entry.digest.clone());
    }
    Ok(StoragePlan {
        delete_digests,
        reclaimable_bytes: reclaimed,
        still_missing_bytes: shortage.saturating_sub(reclaimed),
    })
}

/// Write a previously approved transfer stream into an app-private cache.
/// A failed transfer never publishes a truncated or corrupt GGUF.
/// Callers serialize downloads and must reserve enough space before calling.
pub(crate) fn stage_verified_model<R: Read>(
    root: &Path, digest: &str, size: u64, source: &mut R,
) -> Result<PathBuf, String> {
    if !valid_digest(digest) || !valid_size(size) {
        return Err("Nieprawidłowe metadane modelu".into());
    }
    let root_meta = fs::symlink_metadata(root)
        .map_err(|_| "Prywatny katalog modeli nie istnieje")?;
    if !root_meta.file_type().is_dir() {
        return Err("Nieprawidłowy katalog modeli".into());
    }
    let target = model_path(root, digest)?;
    if let Some(meta) = regular_file(&target)? {
        if meta.len() == size {
            let mut existing = File::open(&target)
                .map_err(|_| "Nie można odczytać istniejącego modelu")?;
            if verify_model(&mut existing, size, digest)
                .map_err(|_| "Błąd weryfikacji istniejącego GGUF")? {
                return Ok(target);
            }
        }
        return Err("Istniejący model jest niezgodny; wymaga jawnego oczyszczenia cache".into());
    }
    let partial = root.join(format!("{digest}.gguf.partial"));
    let mut file = OpenOptions::new().write(true).create_new(true)
        .open(&partial)
        .map_err(|_| "Niedokończone pobieranie wymaga uzgodnienia")?;
    let result = (|| {
        let mut received = 0u64;
        let mut buf = [0u8; 64 * 1024];
        while received < size {
            let cap = buf.len().min((size - received) as usize);
            let n = source.read(&mut buf[..cap])
                .map_err(|_| "Przerwano pobieranie GGUF")?;
            if n == 0 { return Err("Pobrany model jest niekompletny".into()); }
            file.write_all(&buf[..n]).map_err(|_| "Nie można zapisać GGUF")?;
            received += n as u64;
        }
        let mut extra = [0u8; 1];
        if source.read(&mut extra).map_err(|_| "Błąd odczytu końca GGUF")? != 0 {
            return Err("Pobrany model przekracza zadeklarowany rozmiar".into());
        }
        file.flush().map_err(|_| "Błąd zapisu GGUF")?;
        file.sync_all().map_err(|_| "Nie można utrwalić GGUF")?;
        drop(file);
        let mut check = File::open(&partial).map_err(|_| "Brak staged GGUF")?;
        if !verify_model(&mut check, size, digest).map_err(|_| "Błąd SHA-256 GGUF")? {
            return Err("GGUF nie przeszedł weryfikacji integralności".into());
        }
        drop(check);
        if regular_file(&target)?.is_some() {
            return Err("Model jest już zapisany; brak zgody na nadpisanie".into());
        }
        // Rename is atomic only within the same private filesystem.
        fs::rename(&partial, &target).map_err(|_| "Nie można zatwierdzić GGUF")?;
        Ok(target.clone())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&partial);
    }
    result
}

/// Called by a trusted coordinator ONLY after all model sessions close and
/// the Arena no longer has any active lease referencing this digest.
/// Never recursively remove directories or follow symlinks.
pub(crate) fn retire_model(root: &Path, digest: &str,
    active_reference: bool, preserve_cache: bool) -> Result<bool, String> {
    if active_reference || preserve_cache { return Ok(false); }
    let root_meta = fs::symlink_metadata(root)
        .map_err(|_| "Brak prywatnego katalogu GGUF")?;
    if !root_meta.file_type().is_dir() {
        return Err("Nieprawidłowy katalog cache GGUF".into());
    }
    let target = model_path(root, digest)?;
    if regular_file(&target)?.is_none() { return Ok(false); }
    fs::remove_file(target).map_err(|_| "Nie można usunąć nieużywanego modelu")?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn fixture() -> (PathBuf, String, Vec<u8>) {
        static TEST_NONCE: std::sync::atomic::AtomicU64 =
            std::sync::atomic::AtomicU64::new(0);
        let serial = TEST_NONCE.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let root = std::env::temp_dir().join(format!("arena-model-cache-{}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos(),
            serial));
        fs::create_dir(&root).unwrap();
        let bytes = b"GGUFtest-file-model".to_vec();
        let digest = format!("{:x}", Sha256::digest(&bytes));
        (root, digest, bytes)
    }

    fn entry(n: u8, gib: u64, age: u64, in_use: bool, preserve: bool) -> CacheEntry {
        CacheEntry {
            digest: format!("{n:064x}"),
            size_bytes: gib * 1024 * 1024 * 1024,
            last_used_ms: age,
            in_use,
            preserve,
        }
    }

    #[test]
    fn lru_reclaims_only_unused_unpinned_models_until_reserve_is_met() {
        let gib = 1024 * 1024 * 1024;
        let entries = [
            entry(1, 4, 1, true, false),  // active inference: NEVER delete
            entry(2, 6, 2, false, true),  // user-pinned: NEVER delete
            entry(3, 3, 3, false, false),
            entry(4, 5, 4, false, false),
            entry(5, 9, 5, false, false),
        ];
        // Need 8 GiB GGUF + 10 GiB reserve, with 11 GiB free.
        let plan = plan_storage_reclamation(11*gib, 8*gib,
            DEFAULT_FREE_RESERVE_BYTES, &entries).unwrap();
        assert_eq!(plan.delete_digests,
            vec![entries[2].digest.clone(), entries[3].digest.clone()]);
        assert_eq!(plan.reclaimable_bytes, 8*gib);
        assert_eq!(plan.still_missing_bytes, 0);
        assert_eq!(plan_storage_reclamation(20*gib, 8*gib,
            DEFAULT_FREE_RESERVE_BYTES, &entries).unwrap().delete_digests,
            Vec::<String>::new());
    }

    #[test]
    fn insufficient_storage_refuses_active_and_pinned_eviction() {
        let gib = 1024 * 1024 * 1024;
        let entries = [entry(1, 5, 1, true, false),
            entry(2, 4, 2, false, true), entry(3, 1, 3, false, false)];
        let plan = plan_storage_reclamation(1*gib, 8*gib,
            DEFAULT_FREE_RESERVE_BYTES, &entries).unwrap();
        assert_eq!(plan.delete_digests, vec![entries[2].digest.clone()]);
        assert_eq!(plan.still_missing_bytes, 16*gib);
    }

    #[test]
    fn malformed_duplicate_and_overflow_metadata_never_evicted() {
        let gib = 1024 * 1024 * 1024;
        let good = entry(1, 4, 0, false, false);
        let mut bad = good.clone();
        bad.digest = "../escape".into();
        assert!(plan_storage_reclamation(0, gib, 10*gib, &[bad]).is_err());
        assert!(plan_storage_reclamation(0, gib, 10*gib,
            &[good.clone(), good]).is_err());
        assert!(plan_storage_reclamation(0, gib, u64::MAX, &[]).is_err());
        assert!(plan_storage_reclamation(0, gib, u64::MAX - 1, &[]).is_err());
    }

    #[test]
    fn verified_model_is_reusable_and_removed_only_when_idle() {
        let (root, digest, data) = fixture();
        let target = stage_verified_model(&root, &digest, data.len() as u64,
            &mut Cursor::new(&data)).unwrap();
        assert_eq!(target, root.join(format!("{digest}.gguf")));
        assert_eq!(stage_verified_model(&root, &digest, data.len() as u64,
            &mut Cursor::new(&data)).unwrap(), target);
        assert!(!retire_model(&root, &digest, true, false).unwrap());
        assert!(!retire_model(&root, &digest, false, true).unwrap());
        assert!(target.exists());
        assert!(retire_model(&root, &digest, false, false).unwrap());
        assert!(!retire_model(&root, &digest, false, false).unwrap());
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn corrupt_truncated_and_oversized_stream_never_published() {
        let (root, digest, data) = fixture();
        for bad in [&data[..data.len()-1], b"NOTAGGUFtest-file-model".as_slice()] {
            assert!(stage_verified_model(&root, &digest, data.len() as u64,
                &mut Cursor::new(bad)).is_err());
            assert!(!model_path(&root, &digest).unwrap().exists());
            assert!(!root.join(format!("{digest}.gguf.partial")).exists());
        }
        let mut big = data.clone();
        big.push(b'!');
        assert!(stage_verified_model(&root, &digest, data.len() as u64,
            &mut Cursor::new(big)).is_err());
        assert!(!model_path(&root, &digest).unwrap().exists());
        fs::remove_dir(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_model_cannot_be_loaded_or_deleted() {
        let (root, digest, data) = fixture();
        let foreign = root.join("personal-notes.txt");
        fs::write(&foreign, &data).unwrap();
        let target = model_path(&root, &digest).unwrap();
        std::os::unix::fs::symlink(&foreign, &target).unwrap();
        assert!(stage_verified_model(&root, &digest, data.len() as u64,
            &mut Cursor::new(&data)).is_err());
        assert!(retire_model(&root, &digest, false, false).is_err());
        assert_eq!(fs::read(&foreign).unwrap(), data);
        fs::remove_file(&target).unwrap();
        fs::remove_file(foreign).unwrap();
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn invalid_digest_paths_and_foreign_files_never_deleted() {
        let (root, digest, data) = fixture();
        let foreign = root.join("personal-notes.txt");
        fs::write(&foreign, b"keep").unwrap();
        for bad in ["../../escape", "A".repeat(64).as_str(), "bad"] {
            assert!(model_path(&root, bad).is_err());
            assert!(retire_model(&root, bad, false, false).is_err());
        }
        stage_verified_model(&root, &digest, data.len() as u64,
            &mut Cursor::new(data)).unwrap();
        assert!(retire_model(&root, &digest, false, false).unwrap());
        assert_eq!(fs::read(&foreign).unwrap(), b"keep");
        fs::remove_file(foreign).unwrap();
        fs::remove_dir(root).unwrap();
    }
}
