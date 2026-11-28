//! App-local GGUF file usage tracking for a future embedded llama.cpp.
//!
//! A model session retains a private file descriptor and a counted, RAII
//! guard. All admissions and retirement checks share one mutex, so a new
//! managed session cannot race a local retirement decision.
//!
//! NOT a proof about independent Termux/other processes or memory mappings
//! created outside this API. Real embedded engine must own the guard until
//! its worker threads and mappings are fully destroyed. The coordinator must
//! separately supply a valid exclusive DB maintenance reservation.
use crate::android_model_cache::{model_path, verify_model};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{Seek, SeekFrom};
use std::path::Path;
use std::sync::{Mutex, OnceLock};
use std::sync::atomic::{AtomicBool, Ordering};

static OPEN_MODELS: OnceLock<Mutex<HashMap<String, usize>>> = OnceLock::new();
static MAINTENANCE_ACTIVE: AtomicBool = AtomicBool::new(false);

fn sessions() -> &'static Mutex<HashMap<String, usize>> {
    OPEN_MODELS.get_or_init(|| Mutex::new(HashMap::new()))
}

pub(crate) struct ModelSession {
    digest: String,
    // File descriptor retained for the lifetime of the guarded engine.
    pub(crate) file: File,
}
impl Drop for ModelSession {
    fn drop(&mut self) {
        if let Ok(mut active) = sessions().lock() {
            if let Some(uses) = active.get_mut(&self.digest) {
                if *uses > 1 {
                    *uses -= 1;
                } else {
                    active.remove(&self.digest);
                }
            }
        }
    }
}

/// Start a tracked session ONLY for an already verified private GGUF file.
/// Failed verification never reserves or activates a model.
#[allow(dead_code)]
pub(crate) fn open_verified_session(
    root: &Path, digest: &str, expected_size: u64,
) -> Result<ModelSession, String> {
    let mut active = sessions().lock()
        .map_err(|_| "Nieustalona lista aktywnych modeli".to_owned())?;
    if MAINTENANCE_ACTIVE.load(Ordering::SeqCst) {
        return Err("Trwa wyłączna konserwacja GGUF".into());
    }
    let target = model_path(root, digest)?;
    let root_meta = fs::symlink_metadata(root)
        .map_err(|_| "Brak prywatnego katalogu GGUF".to_owned())?;
    if !root_meta.file_type().is_dir() {
        return Err("Nieprawidłowy katalog GGUF".into());
    }
    let metadata = fs::symlink_metadata(&target)
        .map_err(|_| "Brak GGUF do uruchomienia".to_owned())?;
    if !metadata.file_type().is_file() || metadata.len() != expected_size {
        return Err("Model GGUF jest nieprawidłowy".into());
    }
    let mut file = File::open(target)
        .map_err(|_| "Nie udało się otworzyć GGUF".to_owned())?;
    if !verify_model(&mut file, expected_size, digest)
        .map_err(|_| "Nie udało się sprawdzić GGUF".to_owned())? {
        return Err("GGUF nie przeszedł weryfikacji integralności".into());
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| "Nie można ustawić początku pliku GGUF".to_owned())?;
    let count = active.entry(digest.to_owned()).or_insert(0);
    *count = count.checked_add(1)
        .ok_or("Przekroczono licznik sesji GGUF")?;
    drop(active);
    Ok(ModelSession { digest: digest.into(), file })
}

/// Exclusive process-local update window. Blocks new managed GGUF model
/// openings and refuses to begin if any counted native session is live.
/// Keep this value across ALL await points up to the PackageInstaller handoff.
/// This does not substitute for Arena's authenticated STOP/ACK or persistent
/// coordinator hold and cannot control external Termux processes.
#[must_use = "Keep the update gate alive until the installer handoff finishes"]
pub(crate) struct ExclusiveUpdateGate;
impl Drop for ExclusiveUpdateGate {
    fn drop(&mut self) {
        MAINTENANCE_ACTIVE.store(false, Ordering::SeqCst);
    }
}

pub(crate) fn begin_exclusive_update() -> Result<ExclusiveUpdateGate, String> {
    MAINTENANCE_ACTIVE.compare_exchange(
        false, true, Ordering::SeqCst, Ordering::SeqCst,
    ).map_err(|_| "Inna konserwacja lub instalacja GGUF już trwa".to_owned())?;
    let gate = ExclusiveUpdateGate;
    let active = sessions().lock()
        .map_err(|_| "Nieustalony stan aktywnych plików GGUF".to_owned())?;
    if !active.is_empty() {
        return Err("Silnik nadal ma otwarte pliki GGUF".into());
    }
    drop(active);
    Ok(gate)
}

/// Synchronous use for cache diagnostics and future exclusive cleanup.
/// Does not authorize deletion or prove that an external model stopped.
#[allow(dead_code)]
pub(crate) fn with_no_open_sessions<T>(
    examine: impl FnOnce() -> Result<T, String>,
) -> Result<T, String> {
    let _gate = begin_exclusive_update()?;
    examine()
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::io::Write;
    use std::sync::{Arc, Barrier};
    use std::thread;
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn fixture() -> (std::path::PathBuf, String, u64) {
        static N: std::sync::atomic::AtomicUsize =
            std::sync::atomic::AtomicUsize::new(0);
        let n = N.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let dir = std::env::temp_dir().join(
            format!("arena-model-runtime-{}-{}", std::process::id(), n));
        fs::create_dir_all(&dir).unwrap();
        let data = b"GGUFtest-model";
        let digest = format!("{:x}", Sha256::digest(data));
        let mut file = File::create(dir.join(format!("{digest}.gguf"))).unwrap();
        file.write_all(data).unwrap();
        (dir, digest, data.len() as u64)
    }

    #[test]
    fn open_file_prevents_maintenance_until_guard_is_dropped() {
        let _exclusive_test = TEST_LOCK.lock().unwrap();
        let (root, digest, size) = fixture();
        let mut session = open_verified_session(&root, &digest, size).unwrap();
        assert!(session.file.metadata().is_ok());
        assert_eq!(session.file.stream_position().unwrap(), 0);
        assert!(with_no_open_sessions(|| Ok(())).is_err());
        drop(session);
        assert!(with_no_open_sessions(|| Ok(())).is_ok());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn open_rejects_tampering_or_symlink() {
        let _exclusive_test = TEST_LOCK.lock().unwrap();
        let (root, digest, size) = fixture();
        assert!(open_verified_session(&root, &digest, size+1).is_err());
        fs::write(root.join(format!("{digest}.gguf")), b"GGUFwrong-bytes").unwrap();
        assert!(open_verified_session(&root, &digest, size).is_err());
        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;
            let model = root.join(format!("{digest}.gguf"));
            fs::remove_file(&model).unwrap();
            symlink("/dev/null", &model).unwrap();
            assert!(open_verified_session(&root, &digest, size).is_err());
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn update_gate_rejects_live_model_then_blocks_new_opens_until_drop() {
        let _exclusive_test = TEST_LOCK.lock().unwrap();
        let (root, digest, size) = fixture();
        let live = open_verified_session(&root, &digest, size).unwrap();
        assert!(begin_exclusive_update().is_err());
        drop(live);
        let update = begin_exclusive_update().unwrap();
        assert!(begin_exclusive_update().is_err());
        assert!(open_verified_session(&root, &digest, size).is_err());
        drop(update);
        assert!(open_verified_session(&root, &digest, size).is_ok());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn exclusive_maintenance_prevents_concurrent_managed_opens() {
        let _exclusive_test = TEST_LOCK.lock().unwrap();
        let (root, digest, size) = fixture();
        let barrier = Arc::new(Barrier::new(2));
        let barrier_for_thread = barrier.clone();
        let root2 = root.clone();
        let digest2 = digest.clone();
        let worker = thread::spawn(move || {
            with_no_open_sessions(|| {
                barrier_for_thread.wait();
                // No one may open while maintenance is locked.
                assert!(open_verified_session(&root2, &digest2, size).is_err());
                Ok(())
            })
        });
        barrier.wait();
        worker.join().unwrap().unwrap();
        assert!(open_verified_session(&root, &digest, size).is_ok());
        fs::remove_dir_all(root).unwrap();
    }
}
