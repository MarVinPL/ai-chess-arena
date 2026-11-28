//! Durable uncertainty fence for the two Android update records.
//! No secret is written outside the system credential store.
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;
#[cfg(target_os = "android")]
use std::sync::Mutex;

#[cfg(target_os = "android")]
static GATE: Mutex<()> = Mutex::new(());
const ERROR: &str = "Dziennik aktualizacji wymaga uzgodnienia";

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum State {
    Uninitialized,
    Empty,
    Present,
    Uncertain,
}

fn state(dir: &Path) -> Result<State, String> {
    let dir_meta = fs::symlink_metadata(dir).map_err(|_| ERROR)?;
    if !dir_meta.is_dir() || dir_meta.permissions().mode() & 0o077 != 0 {
        return Err(ERROR.into());
    }
    match fs::symlink_metadata(dir.join("state.stage")) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
        _ => return Err(ERROR.into()),
    }
    let path = dir.join("state.v1");
    let m = match fs::symlink_metadata(&path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(State::Uninitialized),
        Err(_) => return Err(ERROR.into()),
    };
    if !m.is_file() || m.permissions().mode() & 0o077 != 0 || m.len() != 1 {
        return Err(ERROR.into());
    }
    let mut value = Vec::new();
    File::open(path)
        .map_err(|_| ERROR)?
        .take(2)
        .read_to_end(&mut value)
        .map_err(|_| ERROR)?;
    match value.as_slice() {
        b"E" => Ok(State::Empty),
        b"P" => Ok(State::Present),
        b"U" => Ok(State::Uncertain),
        _ => Err(ERROR.into()),
    }
}

fn set_state(dir: &Path, next: State) -> Result<(), String> {
    if next == State::Uninitialized {
        return Err(ERROR.into());
    }
    let stage = dir.join("state.stage");
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&stage)
        .map_err(|_| ERROR)?;
    f.write_all(match next {
        State::Empty => b"E",
        State::Present => b"P",
        State::Uncertain => b"U",
        State::Uninitialized => return Err(ERROR.into()),
    })
    .map_err(|_| ERROR)?;
    f.sync_all().map_err(|_| ERROR)?;
    fs::rename(stage, dir.join("state.v1")).map_err(|_| ERROR)?;
    File::open(dir)
        .and_then(|f| f.sync_all())
        .map_err(|_| ERROR.into())
}

trait Store {
    fn read(&self) -> Result<Option<String>, String>;
    fn insert(&self, raw: &str) -> Result<(), String>;
    fn delete(&self, raw: &str) -> Result<(), String>;
}

fn checked_read(dir: &Path, store: &impl Store) -> Result<Option<String>, String> {
    let phase = state(dir)?;
    if matches!(phase, State::Uncertain | State::Uninitialized) {
        return Err(ERROR.into());
    }
    let value = store.read()?;
    match (phase, value.is_some()) {
        (State::Empty, false) | (State::Present, true) => Ok(value),
        _ => Err(ERROR.into()),
    }
}

// A baseline is allowed only with independently verified identity and
// reconciliation evidence. Never repair an uncertain or existing record.
#[derive(Clone, Copy)]
struct BaselineEvidence {
    authenticated_idle: bool,
    installer_reconciled: bool,
    identity_bound: bool,
}

fn initialize_empty(
    dir: &Path,
    store: &impl Store,
    evidence: BaselineEvidence,
) -> Result<(), String> {
    if !evidence.authenticated_idle || !evidence.installer_reconciled || !evidence.identity_bound {
        return Err(ERROR.into());
    }
    match state(dir)? {
        State::Uninitialized => {
            if store.read()?.is_some() {
                return Err(ERROR.into());
            }
            set_state(dir, State::Empty)
        }
        State::Empty => {
            if checked_read(dir, store)?.is_none() {
                Ok(())
            } else {
                Err(ERROR.into())
            }
        }
        _ => Err(ERROR.into()),
    }
}

fn checked_insert(dir: &Path, store: &impl Store, raw: &str) -> Result<(), String> {
    if checked_read(dir, store)?.is_some() {
        return Err(ERROR.into());
    }
    set_state(dir, State::Uncertain)?;
    store.insert(raw)?;
    if store.read()?.as_deref() != Some(raw) {
        return Err(ERROR.into());
    }
    set_state(dir, State::Present)
}

fn checked_delete(dir: &Path, store: &impl Store, raw: &str) -> Result<(), String> {
    if checked_read(dir, store)?.as_deref() != Some(raw) {
        return Err(ERROR.into());
    }
    set_state(dir, State::Uncertain)?;
    store.delete(raw)?;
    if store.read()?.is_some() {
        return Err(ERROR.into());
    }
    set_state(dir, State::Empty)
}

#[cfg(target_os = "android")]
mod android {
    use super::*;
    use crate::credential_vault::{self, Slot};
    struct Native(Slot);
    impl Store for Native {
        fn read(&self) -> Result<Option<String>, String> {
            credential_vault::journals::read(self.0).map_err(|_| ERROR.into())
        }
        fn insert(&self, raw: &str) -> Result<(), String> {
            credential_vault::journals::insert(self.0, raw).map_err(|_| ERROR.into())
        }
        fn delete(&self, raw: &str) -> Result<(), String> {
            credential_vault::journals::delete_expected(self.0, raw).map_err(|_| ERROR.into())
        }
    }
    fn dir(slot: Slot) -> Result<std::path::PathBuf, String> {
        let name = match slot {
            Slot::PendingHold => "credential-pending-hold",
            Slot::PendingInstall => "credential-pending-install",
        };
        use std::os::unix::fs::DirBuilderExt;
        let root = crate::android_worker_token_store::journal_root()?;
        let path = root.join(name);
        match fs::DirBuilder::new().mode(0o700).create(&path) {
            Ok(()) => File::open(&root)
                .and_then(|f| f.sync_all())
                .map_err(|_| ERROR)?,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err(ERROR.into()),
        }
        Ok(path)
    }
    pub(crate) fn read(slot: Slot) -> Result<Option<String>, String> {
        let _guard = GATE.lock().map_err(|_| ERROR)?;
        checked_read(&dir(slot)?, &Native(slot))
    }
    pub(crate) fn insert(slot: Slot, raw: &str) -> Result<(), String> {
        let _guard = GATE.lock().map_err(|_| ERROR)?;
        checked_insert(&dir(slot)?, &Native(slot), raw)
    }
    pub(crate) fn delete_expected(slot: Slot, raw: &str) -> Result<(), String> {
        let _guard = GATE.lock().map_err(|_| ERROR)?;
        checked_delete(&dir(slot)?, &Native(slot), raw)
    }
}
#[cfg(target_os = "android")]
pub(crate) use android::{delete_expected, insert, read};

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::sync::atomic::{AtomicU64, Ordering};
    struct Temp(std::path::PathBuf);
    impl Temp {
        fn new() -> Self {
            static N: AtomicU64 = AtomicU64::new(0);
            let path = std::env::temp_dir().join(format!(
                "arena-journal-{}-{}",
                std::process::id(),
                N.fetch_add(1, Ordering::Relaxed)
            ));
            use std::os::unix::fs::DirBuilderExt;
            fs::DirBuilder::new().mode(0o700).create(&path).unwrap();
            set_state(&path, State::Empty).unwrap();
            Self(path)
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    #[derive(Default)]
    struct Fake {
        value: RefCell<Option<String>>,
        fail: Cell<bool>,
    }
    impl Store for Fake {
        fn read(&self) -> Result<Option<String>, String> {
            Ok(self.value.borrow().clone())
        }
        fn insert(&self, raw: &str) -> Result<(), String> {
            *self.value.borrow_mut() = Some(raw.into());
            if self.fail.get() {
                Err(ERROR.into())
            } else {
                Ok(())
            }
        }
        fn delete(&self, _: &str) -> Result<(), String> {
            *self.value.borrow_mut() = None;
            if self.fail.get() {
                Err(ERROR.into())
            } else {
                Ok(())
            }
        }
    }
    #[test]
    fn roundtrip_and_conflict() {
        let d = Temp::new();
        let s = Fake::default();
        assert_eq!(checked_read(&d.0, &s), Ok(None));
        checked_insert(&d.0, &s, "synthetic").unwrap();
        assert!(checked_insert(&d.0, &s, "different").is_err());
        assert!(checked_delete(&d.0, &s, "different").is_err());
        assert_eq!(checked_read(&d.0, &s), Ok(Some("synthetic".into())));
        checked_delete(&d.0, &s, "synthetic").unwrap();
        assert_eq!(checked_read(&d.0, &s), Ok(None));
    }
    #[test]
    fn failed_insert_survives_new_store_instance() {
        let d = Temp::new();
        let s = Fake::default();
        s.fail.set(true);
        assert!(checked_insert(&d.0, &s, "synthetic").is_err());
        assert_eq!(state(&d.0), Ok(State::Uncertain));
        let restarted = Fake::default();
        assert!(checked_read(&d.0, &restarted).is_err());
    }
    #[test]
    fn failed_delete_and_missing_present_block() {
        let d = Temp::new();
        let s = Fake::default();
        checked_insert(&d.0, &s, "synthetic").unwrap();
        assert!(checked_read(&d.0, &Fake::default()).is_err());
        s.fail.set(true);
        assert!(checked_delete(&d.0, &s, "synthetic").is_err());
        assert!(checked_read(&d.0, &Fake::default()).is_err());
    }
    #[test]
    fn unexpected_record_and_partial_stage_block() {
        let d = Temp::new();
        let s = Fake::default();
        *s.value.borrow_mut() = Some("unexpected".into());
        assert!(checked_read(&d.0, &s).is_err());
        fs::write(d.0.join("state.stage"), b"partial").unwrap();
        assert!(checked_read(&d.0, &Fake::default()).is_err());
    }

    #[test]
    fn missing_marker_never_implies_no_legacy_hold() {
        let d = Temp::new();
        let s = Fake::default();
        fs::remove_file(d.0.join("state.v1")).unwrap();
        assert_eq!(state(&d.0), Ok(State::Uninitialized));
        assert!(checked_read(&d.0, &s).is_err());
        assert!(checked_insert(&d.0, &s, "synthetic").is_err());
        assert!(s.value.borrow().is_none());
    }
    #[test]
    fn crash_boundaries_remain_blocked() {
        let d = Temp::new();
        let s = Fake::default();
        set_state(&d.0, State::Uncertain).unwrap();
        assert!(checked_read(&d.0, &s).is_err()); // before vault mutation
        s.insert("synthetic").unwrap();
        assert!(checked_read(&d.0, &s).is_err()); // after mutation, before marker
        set_state(&d.0, State::Present).unwrap();
        assert_eq!(checked_read(&d.0, &s), Ok(Some("synthetic".into())));
        set_state(&d.0, State::Uncertain).unwrap();
        s.delete("synthetic").unwrap();
        assert!(checked_read(&d.0, &s).is_err()); // after delete, before commit
    }

    #[test]
    fn baseline_requires_all_independent_evidence() {
        let d = Temp::new();
        let s = Fake::default();
        fs::remove_file(d.0.join("state.v1")).unwrap();
        for mask in 0..7 {
            let e = BaselineEvidence {
                authenticated_idle: mask & 1 != 0,
                installer_reconciled: mask & 2 != 0,
                identity_bound: mask & 4 != 0,
            };
            assert!(initialize_empty(&d.0, &s, e).is_err());
            assert_eq!(state(&d.0), Ok(State::Uninitialized));
        }
        let e = BaselineEvidence {
            authenticated_idle: true,
            installer_reconciled: true,
            identity_bound: true,
        };
        initialize_empty(&d.0, &s, e).unwrap();
        initialize_empty(&d.0, &s, e).unwrap();
        assert_eq!(checked_read(&d.0, &s), Ok(None));
    }
    #[test]
    fn baseline_never_clears_uncertain_or_existing_records() {
        let d = Temp::new();
        let s = Fake::default();
        let e = BaselineEvidence {
            authenticated_idle: true,
            installer_reconciled: true,
            identity_bound: true,
        };
        fs::remove_file(d.0.join("state.v1")).unwrap();
        s.insert("synthetic").unwrap();
        assert!(initialize_empty(&d.0, &s, e).is_err());
        assert_eq!(s.read().unwrap().as_deref(), Some("synthetic"));
        set_state(&d.0, State::Uncertain).unwrap();
        assert!(initialize_empty(&d.0, &s, e).is_err());
        assert_eq!(state(&d.0), Ok(State::Uncertain));
        set_state(&d.0, State::Present).unwrap();
        assert!(initialize_empty(&d.0, &s, e).is_err());
    }
}
