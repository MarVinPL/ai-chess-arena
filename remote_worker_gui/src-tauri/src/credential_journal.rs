//! Durable uncertainty fence for the two Android update records.
//! No secret is written outside the system credential store.
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;
#[cfg(target_os = "android")]
use std::sync::Mutex;

#[cfg(target_os = "android")]
static GATE: Mutex<bool> = Mutex::new(false);
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
    File::open(dir)
        .and_then(|f| f.sync_all())
        .map_err(|_| ERROR)?;
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

const PAIR_MARKER: &str = "credential-pair.v1";
const PAIR_INTENT: &str = "credential-pair.intent";
const PAIR_STAGE: &str = "credential-pair.stage";
const PAIR_ID_LEN: usize = 96;
const NONCE_LEN: usize = 32;
const INTENT_LEN: usize = PAIR_ID_LEN + 64;

fn pair_id(root: &Path) -> Result<Option<String>, String> {
    match fs::symlink_metadata(root.join(PAIR_STAGE)) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
        _ => return Err(ERROR.into()),
    }
    let id = read_pair_record(&root.join(PAIR_MARKER))?;
    if id.is_some() {
        // Re-establish durability if the previous process died after rename.
        sync_dir(root)?;
    }
    Ok(id)
}

fn read_pair_record(path: &Path) -> Result<Option<String>, String> {
    read_hex_record(path, PAIR_ID_LEN)
}

fn read_intent(root: &Path) -> Result<Option<String>, String> {
    read_hex_record(&root.join(PAIR_INTENT), INTENT_LEN)
}

fn read_hex_record(path: &Path, length: usize) -> Result<Option<String>, String> {
    match fs::symlink_metadata(&path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Ok(meta) if meta.is_file() && meta.len() == length as u64
            && meta.permissions().mode() & 0o077 == 0 => (),
        _ => return Err(ERROR.into()),
    }
    let mut bytes = Vec::new();
    File::open(path).map_err(|_| ERROR)?.take((length + 1) as u64)
        .read_to_end(&mut bytes).map_err(|_| ERROR)?;
    if bytes.len() != length || !bytes.iter().all(|&b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return Err(ERROR.into());
    }
    String::from_utf8(bytes).map(Some).map_err(|_| ERROR.into())
}

fn valid_pair_id(bytes: &[u8]) -> bool {
    bytes.len() == PAIR_ID_LEN
        && bytes.iter().all(|&b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn sync_dir(dir: &Path) -> Result<(), String> {
    File::open(dir).and_then(|file| file.sync_all()).map_err(|_| ERROR.into())
}

fn available_pair(root: &Path) -> Result<String, String> {
    if read_intent(root)?.is_some() {
        return Err(ERROR.into());
    }
    pair_id(root)?.ok_or_else(|| ERROR.into())
}

fn write_pair_record(path: &Path, identity: &str) -> Result<(), String> {
    let mut record = OpenOptions::new().write(true).create_new(true).mode(0o600)
        .open(path).map_err(|_| ERROR)?;
    record.write_all(identity.as_bytes()).map_err(|_| ERROR)?;
    record.sync_all().map_err(|_| ERROR.into())
}

fn initialize_pair(
    root: &Path, hold_dir: &Path, install_dir: &Path,
    hold: &impl Store, install: &impl Store, identity: &str, evidence: BaselineEvidence,
) -> Result<(), String> {
    initialize_pair_with_sync(root, hold_dir, install_dir, hold, install, identity, evidence, sync_dir)
}

fn initialize_pair_with_sync(
    root: &Path, hold_dir: &Path, install_dir: &Path, hold: &impl Store, install: &impl Store,
    identity: &str, evidence: BaselineEvidence, sync_root: impl Fn(&Path) -> Result<(), String>,
) -> Result<(), String> {
    if !evidence.authenticated_idle || !evidence.installer_reconciled
        || !evidence.identity_bound || !valid_pair_id(identity.as_bytes())
    {
        return Err(ERROR.into());
    }
    let stage = root.join(PAIR_STAGE);
    if let Some(staged) = read_pair_record(&stage)? {
        if staged != identity || read_intent(root)?.as_deref().map(|record| &record[..PAIR_ID_LEN]) != Some(identity)
            || read_pair_record(&root.join(PAIR_MARKER))?.is_some()
            || !matches!(state(hold_dir)?, State::Uninitialized | State::Empty)
            || !matches!(state(install_dir)?, State::Uninitialized | State::Empty)
            || hold.read()?.is_some() || install.read()?.is_some()
        {
            return Err(ERROR.into());
        }
        File::open(&stage).and_then(|file| file.sync_all()).map_err(|_| ERROR)?;
        sync_root(root)?;
    } else {
        if let Some(committed) = pair_id(root)? {
            return if committed == identity && checked_read(hold_dir, hold)?.is_none()
                && checked_read(install_dir, install)?.is_none() { Ok(()) } else { Err(ERROR.into()) };
        }
        if state(hold_dir)? != State::Uninitialized || state(install_dir)? != State::Uninitialized
            || hold.read()?.is_some() || install.read()?.is_some()
        {
            return Err(ERROR.into());
        }
        write_pair_record(&stage, identity)?;
        sync_root(root)?;
    }
    initialize_empty(hold_dir, hold, evidence)?;
    initialize_empty(install_dir, install, evidence)?;
    if checked_read(hold_dir, hold)?.is_some() || checked_read(install_dir, install)?.is_some() {
        return Err(ERROR.into());
    }
    fs::rename(stage, root.join(PAIR_MARKER)).map_err(|_| ERROR)?;
    sync_root(root)
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

// The STOP slot is write-once in #325. Only a joined STOP may create it.
// Absence is not recovery evidence. Never adopt orphan ciphertext.
fn stop_insert(dir: &Path, store: &impl Store, raw: &str) -> Result<(), String> {
    match state(dir)? {
        State::Present => {
            if checked_read(dir, store)?.as_deref() != Some(raw) {
                return Err(ERROR.into());
            }
            return sync_dir(dir);
        }
        State::Uninitialized => {
            if store.read()?.is_some() { return Err(ERROR.into()); }
        }
        _ => return Err(ERROR.into()),
    }
    set_state(dir, State::Uncertain)?;
    store.insert(raw)?;
    if store.read()?.as_deref() != Some(raw) { return Err(ERROR.into()); }
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
    fn slot_path(root: &Path, slot: Slot) -> std::path::PathBuf {
        root.join(match slot {
            Slot::PendingHold => "credential-pending-hold",
            Slot::PendingInstall => "credential-pending-install",
            Slot::NativeStop => "native-stop-outbox-v2",
        })
    }

    fn dir(root: &Path, slot: Slot) -> Result<std::path::PathBuf, String> {
        use std::os::unix::fs::DirBuilderExt;
        let path = slot_path(root, slot);
        match fs::DirBuilder::new().mode(0o700).create(&path) {
            Ok(()) => (),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err(ERROR.into()),
        }
        sync_dir(root)?;
        Ok(path)
    }
    pub(crate) fn check_registration_storage() -> Result<(), String> {
        let guard = GATE.lock().map_err(|_| ERROR)?;
        let root = crate::android_worker_token_store::journal_root()?;
        if *guard || read_intent(&root)?.is_some() || pair_id(&root)?.is_some() {
            return Err(ERROR.into());
        }
        for slot in [Slot::PendingHold, Slot::PendingInstall] {
            let path = slot_path(&root, slot);
            match fs::symlink_metadata(&path) {
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => (),
                Ok(_) if state(&path)? == State::Uninitialized => (),
                _ => return Err(ERROR.into()),
            }
            if Native(slot).read()?.is_some() {
                return Err(ERROR.into());
            }
        }
        Ok(())
    }

    fn authorization_marker(
        server: &str, worker_id: &str, token: &str, nonce: &str,
    ) -> Result<String, String> {
        use sha2::{Digest, Sha256};

        let origin = reqwest::Url::parse(server).map_err(|_| ERROR)?.origin().ascii_serialization();
        // Canonical HTTPS origin, Worker identity, token digest and coordinator
        // nonce form one durable binding. No machine token goes to disk.
        Ok(format!("{:x}", Sha256::digest(
            [origin.as_bytes(), worker_id.as_bytes(), &Sha256::digest(token.as_bytes()), nonce.as_bytes()]
                .join(&0),
        )))
    }

    pub(crate) struct JournalBaseline(u64);

    impl JournalBaseline {
        pub(crate) fn inspect() -> Result<Self, String> {
            let evidence = crate::android_update_install_evidence::collect(None)?;
            let version = evidence.identity().long_version_code;
            if !evidence.accepts_attested_baseline(version) {
                return Err(ERROR.into());
            }
            Ok(Self(version))
        }

        pub(crate) fn version(&self) -> u64 { self.0 }
    }

    pub(crate) fn needs_initialization() -> Result<bool, String> {
        let guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard { return Err(ERROR.into()); }
        let root = crate::android_worker_token_store::journal_root()?;
        Ok(read_intent(&root)?.is_some() || pair_id(&root)?.is_none())
    }

    // Snapshot and persist the original nonce before contacting the coordinator.
    // A restart must use the same native baseline version and identity.
    pub(crate) fn prepare_reconciliation(
        server: &str, worker_id: &str, expectation: &JournalBaseline,
    ) -> Result<(String, String, bool), String> {
        let _credentials = crate::credential_change_gate()?;
        let token = crate::token_entry()?.get_password()?;
        let guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard { return Err(ERROR.into()); }
        let root = crate::android_worker_token_store::journal_root()?;
        let path = root.join(PAIR_INTENT);
        if let Some(intent) = read_intent(&root)? {
            let (nonce, digest) = intent[..PAIR_ID_LEN].split_at(NONCE_LEN);
            if digest != authorization_marker(server, worker_id, &token, nonce)?
                || intent[PAIR_ID_LEN..] != baseline_digest(expectation)
            {
                return Err(ERROR.into());
            }
            File::open(&path).and_then(|file| file.sync_all()).map_err(|_| ERROR)?;
            sync_dir(&root)?;
            // A committed pair retries only the coordinator completion call.
            let stage = read_pair_record(&root.join(PAIR_STAGE))?;
            if let Some(committed) = read_pair_record(&root.join(PAIR_MARKER))? {
                if stage.is_some() || committed != intent[..PAIR_ID_LEN] {
                    return Err(ERROR.into());
                }
                return Ok((token, nonce.into(), true));
            }
            if stage.as_ref().is_some_and(|staged| staged != &intent[..PAIR_ID_LEN]) {
                return Err(ERROR.into());
            }
            verify_installer(expectation)?;
            return Ok((token, nonce.into(), false));
        }
        if pair_id(&root)?.is_some() { return Err(ERROR.into()); }
        for slot in [Slot::PendingHold, Slot::PendingInstall] {
            if state(&dir(&root, slot)?)? != State::Uninitialized || Native(slot).read()?.is_some() {
                return Err(ERROR.into());
            }
        }
        verify_installer(expectation)?;
        let nonce = crate::android_update_coordinator::random_nonce()?;
        let digest = authorization_marker(server, worker_id, &token, &nonce)?;
        write_pair_record(&path, &format!("{nonce}{digest}{}", baseline_digest(expectation)))?;
        sync_dir(&root)?;
        Ok((token, nonce, false))
    }

    fn baseline_digest(expectation: &JournalBaseline) -> String {
        use sha2::{Digest, Sha256};
        let mut digest = Sha256::new();
        digest.update(b"attested-baseline-v1");
        digest.update(expectation.version().to_be_bytes());
        format!("{:x}", digest.finalize())
    }

    fn verify_installer(expectation: &JournalBaseline) -> Result<(), String> {
        if JournalBaseline::inspect()?.version() != expectation.version() {
            return Err(ERROR.into());
        }
        Ok(())
    }

    pub(crate) fn finish_reconciliation(server: &str, worker_id: &str, token: &str) -> Result<(), String> {
        let _credentials = crate::credential_change_gate()?;
        if crate::token_entry()?.get_password()? != token { return Err(ERROR.into()); }
        let guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard { return Err(ERROR.into()); }
        let root = crate::android_worker_token_store::journal_root()?;
        let path = root.join(PAIR_INTENT);
        let identity = pair_id(&root)?.ok_or(ERROR)?;
        let (nonce, digest) = identity.split_at(NONCE_LEN);
        if digest != authorization_marker(server, worker_id, token, nonce)? {
            return Err(ERROR.into());
        }
        if let Some(intent) = read_intent(&root)? {
            if intent[..PAIR_ID_LEN] != identity { return Err(ERROR.into()); }
            fs::remove_file(path).map_err(|_| ERROR)?;
        }
        sync_dir(&root)
    }

    pub(crate) fn initialize_authorized_pair(
        server: &str,
        worker_id: &str,
        authorization: &crate::android_update_coordinator::JournalInitAuthorization,
        baseline: &JournalBaseline,
    ) -> Result<(), String> {
        use crate::android_update_coordinator;

        let _credentials = crate::credential_change_gate()?;
        let token = crate::token_entry()?.get_password()?;
        let mut guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard {
            return Err(ERROR.into());
        }
        if authorization.baseline_version() != Some(baseline.version())
            || !authorization.matches(server, worker_id, &token, android_update_coordinator::system_unix_ms()?)
        {
            return Err(ERROR.into());
        }
        // Require a matching terminal callback, trusted signer and exact
        // version from fresh native observations. Empty session inventory
        // is insufficient. The supplied expectation is native reconciliation data.
        verify_installer(baseline)?;
        if !authorization.matches(server, worker_id, &token, android_update_coordinator::system_unix_ms()?)
        {
            return Err(ERROR.into());
        }

        let digest = authorization_marker(server, worker_id, &token, authorization.nonce())?;
        let identity = format!("{}{digest}", authorization.nonce());
        let root = crate::android_worker_token_store::journal_root()?;
        let expected_intent = format!("{identity}{}", baseline_digest(baseline));
        if read_intent(&root)?.as_deref() != Some(expected_intent.as_str()) {
            return Err(ERROR.into());
        }
        let hold_dir = dir(&root, Slot::PendingHold)?;
        let install_dir = dir(&root, Slot::PendingInstall)?;
        let result = initialize_pair(
            &root, &hold_dir, &install_dir, &Native(Slot::PendingHold),
            &Native(Slot::PendingInstall), &identity,
            BaselineEvidence {
                authenticated_idle: true,
                installer_reconciled: true,
                identity_bound: true,
            },
        );
        if result.is_err() {
            *guard = true;
        }
        result
    }

    pub(crate) struct JournalInitCompletion {
        pub(crate) origin: String,
        pub(crate) worker_id: String,
        pub(crate) nonce: String,
        pub(crate) pair_marker: String,
    }

    pub(crate) fn completion(
        server: &str, worker_id: &str, machine_token: &str,
    ) -> Result<JournalInitCompletion, String> {
        let _credentials = crate::credential_change_gate()?;
        if crate::token_entry()?.get_password()? != machine_token {
            return Err(ERROR.into());
        }
        let guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard {
            return Err(ERROR.into());
        }
        let root = crate::android_worker_token_store::journal_root()?;
        let identity = pair_id(&root)?.ok_or(ERROR)?;
        let (nonce, recorded_digest) = identity.split_at(NONCE_LEN);
        let pair_marker = authorization_marker(server, worker_id, machine_token, nonce)?;
        if recorded_digest != pair_marker
            || checked_read(&dir(&root, Slot::PendingHold)?, &Native(Slot::PendingHold))?.is_some()
            || checked_read(&dir(&root, Slot::PendingInstall)?, &Native(Slot::PendingInstall))?.is_some()
        {
            return Err(ERROR.into());
        }
        let origin = reqwest::Url::parse(server).map_err(|_| ERROR)?.origin().ascii_serialization();
        Ok(JournalInitCompletion { origin, worker_id: worker_id.into(), nonce: nonce.into(), pair_marker })
    }

    pub(crate) fn native_stop_read() -> Result<Option<String>, String> {
        let guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard { return Err(ERROR.into()); }
        let root = crate::android_worker_token_store::journal_root()?;
        let path = dir(&root, Slot::NativeStop)?;
        let value = checked_read(&path, &Native(Slot::NativeStop))?;
        sync_dir(&path)?;
        Ok(value)
    }

    pub(crate) fn native_stop_insert(raw: &str) -> Result<(), String> {
        let mut guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard { return Err(ERROR.into()); }
        let root = crate::android_worker_token_store::journal_root()?;
        let path = dir(&root, Slot::NativeStop)?;
        let result = stop_insert(&path, &Native(Slot::NativeStop), raw);
        if result.is_err() { *guard = true; }
        result
    }

    fn require_pair() -> Result<std::path::PathBuf, String> {
        let root = crate::android_worker_token_store::journal_root()?;
        available_pair(&root)?;
        Ok(root)
    }

    pub(crate) fn read(slot: Slot) -> Result<Option<String>, String> {
        let guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard {
            return Err(ERROR.into());
        }
        checked_read(&dir(&require_pair()?, slot)?, &Native(slot))
    }
    pub(crate) fn insert(slot: Slot, raw: &str) -> Result<(), String> {
        let mut guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard {
            return Err(ERROR.into());
        }
        let path = dir(&require_pair()?, slot)?;
        // A failed final directory sync can leave a visible but not durable marker.
        // Fence the process on any failed mutation until restart/reconciliation.
        let result = checked_insert(&path, &Native(slot), raw);
        if result.is_err() {
            *guard = true;
        }
        result
    }
    pub(crate) fn delete_expected(slot: Slot, raw: &str) -> Result<(), String> {
        let mut guard = GATE.lock().map_err(|_| ERROR)?;
        if *guard {
            return Err(ERROR.into());
        }
        let path = dir(&require_pair()?, slot)?;
        // A failed final directory sync can leave a visible but not durable marker.
        // Fence the process on any failed mutation until restart/reconciliation.
        let result = checked_delete(&path, &Native(slot), raw);
        if result.is_err() {
            *guard = true;
        }
        result
    }
}
#[cfg(target_os = "android")]
pub(crate) use android::{
    check_registration_storage, completion, delete_expected, finish_reconciliation,
    native_stop_read, native_stop_insert,
    initialize_authorized_pair, insert, needs_initialization, prepare_reconciliation, read, JournalBaseline,
};

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
    fn native_stop_write_once_and_missing_ciphertext() {
        let d = Temp::new();
        fs::remove_file(d.0.join("state.v1")).unwrap();
        let store = Fake::default();
        assert!(checked_read(&d.0, &store).is_err());
        stop_insert(&d.0, &store, "synthetic-stop").unwrap();
        stop_insert(&d.0, &store, "synthetic-stop").unwrap();
        assert!(stop_insert(&d.0, &store, "different").is_err());
        assert_eq!(checked_read(&d.0, &store), Ok(Some("synthetic-stop".into())));
        assert_eq!(fs::read(d.0.join("state.v1")).unwrap(), b"P");
        *store.value.borrow_mut() = None;
        assert!(checked_read(&d.0, &store).is_err());
        assert!(stop_insert(&d.0, &store, "different").is_err());
    }

    #[test]
    fn native_stop_interrupted_commit_and_orphan_fail_closed() {
        let d = Temp::new();
        fs::remove_file(d.0.join("state.v1")).unwrap();
        let store = Fake::default();
        *store.value.borrow_mut() = Some("orphan".into());
        assert!(stop_insert(&d.0, &store, "new").is_err());
        *store.value.borrow_mut() = None;
        store.fail.set(true);
        assert!(stop_insert(&d.0, &store, "first").is_err());
        assert_eq!(state(&d.0), Ok(State::Uncertain));
        store.fail.set(false);
        assert!(checked_read(&d.0, &store).is_err());
        assert!(stop_insert(&d.0, &store, "first").is_err());
        assert_eq!(store.read(), Ok(Some("first".into())));
    }

    // File-backed synthetic vault: exercises actual process death and durable
    // markers, not Android Keystore cryptography or SharedPreferences internals.
    struct CrashStore<'a> { root: &'a Path, boundary: &'a str }
    impl Store for CrashStore<'_> {
        fn read(&self) -> Result<Option<String>, String> {
            match fs::read_to_string(self.root.join("fake-vault")) {
                Ok(raw) => Ok(Some(raw)),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
                Err(_) => Err(ERROR.into()),
            }
        }
        fn insert(&self, raw: &str) -> Result<(), String> {
            if self.boundary == "before-vault" { std::process::exit(73); }
            let mut f = OpenOptions::new().create_new(true).write(true).mode(0o600)
                .open(self.root.join("fake-vault")).map_err(|_| ERROR)?;
            f.write_all(raw.as_bytes()).map_err(|_| ERROR)?;
            f.sync_all().map_err(|_| ERROR)?;
            sync_dir(self.root)?;
            if self.boundary == "after-vault" { std::process::exit(73); }
            Ok(())
        }
        fn delete(&self, _: &str) -> Result<(), String> { Err(ERROR.into()) }
    }
    #[test]
    fn native_stop_crash_child() {
        let Some(root) = std::env::var_os("ARENA_STOP_JOURNAL_CHILD") else { return; };
        let root = std::path::PathBuf::from(root);
        let boundary = std::env::var("ARENA_STOP_JOURNAL_BOUNDARY").unwrap();
        let store = CrashStore { root: &root, boundary: &boundary };
        stop_insert(&root, &store, "synthetic-only").unwrap();
        std::process::exit(73);
    }
    #[test]
    fn native_stop_process_exit_at_vault_boundaries() {
        for boundary in ["before-vault", "after-vault", "committed"] {
            let d = Temp::new();
            fs::remove_file(d.0.join("state.v1")).unwrap();
            let name = format!("{}::native_stop_crash_child",
                module_path!().split_once("::").unwrap().1);
            let status = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", &name])
                .env("ARENA_STOP_JOURNAL_CHILD", &d.0)
                .env("ARENA_STOP_JOURNAL_BOUNDARY", boundary)
                .output().unwrap().status;
            assert_eq!(status.code(), Some(73));
            let store = CrashStore { root: &d.0, boundary: "" };
            if boundary == "committed" {
                assert_eq!(checked_read(&d.0, &store), Ok(Some("synthetic-only".into())));
                stop_insert(&d.0, &store, "synthetic-only").unwrap();
            } else {
                assert_eq!(state(&d.0), Ok(State::Uncertain));
                assert!(checked_read(&d.0, &store).is_err());
                assert!(stop_insert(&d.0, &store, "synthetic-only").is_err());
            }
            assert!(stop_insert(&d.0, &store, "other").is_err());
        }
    }
    #[test]
    fn native_stop_partial_marker_blocks_even_with_vault_value() {
        let d = Temp::new();
        fs::remove_file(d.0.join("state.v1")).unwrap();
        let store = Fake::default();
        stop_insert(&d.0, &store, "synthetic").unwrap();
        OpenOptions::new().write(true).create_new(true).mode(0o600)
            .open(d.0.join("state.stage")).unwrap().sync_all().unwrap();
        assert!(checked_read(&d.0, &store).is_err());
        assert!(stop_insert(&d.0, &store, "synthetic").is_err());
    }

    const IDENTITY: &str = concat!("0123456789abcdef0123456789abcdef",
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef");
    const PROVEN: BaselineEvidence = BaselineEvidence {
        authenticated_idle: true, installer_reconciled: true, identity_bound: true,
    };

    fn empty_paths(root: &Temp) -> (std::path::PathBuf, std::path::PathBuf) {
        use std::os::unix::fs::DirBuilderExt;
        let hold = root.0.join("hold");
        let install = root.0.join("install");
        fs::DirBuilder::new().mode(0o700).create(&hold).unwrap();
        fs::DirBuilder::new().mode(0o700).create(&install).unwrap();
        (hold, install)
    }

    #[test]
    fn pair_requires_all_evidence_then_commits_both_and_retries_same_identity() {
        let root = Temp::new();
        let (hold, install) = empty_paths(&root);
        let hs = Fake::default();
        let is = Fake::default();
        for bits in 0..7 {
            let evidence = BaselineEvidence {
                authenticated_idle: bits & 1 != 0,
                installer_reconciled: bits & 2 != 0,
                identity_bound: bits & 4 != 0,
            };
            assert!(initialize_pair(&root.0, &hold, &install, &hs, &is, IDENTITY, evidence).is_err());
            assert_eq!(state(&hold), Ok(State::Uninitialized));
            assert_eq!(state(&install), Ok(State::Uninitialized));
            assert_eq!(pair_id(&root.0), Ok(None));
        }
        for _ in 0..2 {
            initialize_pair(&root.0, &hold, &install, &hs, &is, IDENTITY, PROVEN).unwrap();
            assert_eq!(pair_id(&root.0), Ok(Some(IDENTITY.into())));
            assert_eq!(checked_read(&hold, &hs), Ok(None));
            assert_eq!(checked_read(&install, &is), Ok(None));
        }
        assert!(initialize_pair(&root.0, &hold, &install, &hs, &is, &"a".repeat(PAIR_ID_LEN), PROVEN).is_err());
    }

    #[test]
    fn partially_staged_pair_stays_blocked_after_reopen() {
        let root = Temp::new();
        let (hold, install) = empty_paths(&root);
        OpenOptions::new().write(true).create_new(true).mode(0o600)
            .open(root.0.join(PAIR_STAGE)).unwrap().write_all(IDENTITY.as_bytes()).unwrap();
        set_state(&hold, State::Empty).unwrap();
        assert!(pair_id(&root.0).is_err());
        assert!(initialize_pair(
            &root.0, &hold, &install, &Fake::default(), &Fake::default(), IDENTITY, PROVEN,
        ).is_err());
        assert_eq!(state(&install), Ok(State::Uninitialized));
    }

    #[test]
    fn authorized_restart_finishes_only_the_same_partial_pair() {
        let root = Temp::new();
        let (hold, install) = empty_paths(&root);
        write_pair_record(&root.0.join(PAIR_INTENT), &format!("{IDENTITY}{}", "a".repeat(64))).unwrap();
        write_pair_record(&root.0.join(PAIR_STAGE), IDENTITY).unwrap();
        set_state(&hold, State::Empty).unwrap();
        let holds = Fake::default();
        let installs = Fake::default();
        assert!(pair_id(&root.0).is_err());
        assert!(initialize_pair(
            &root.0, &hold, &install, &holds, &installs, &"b".repeat(PAIR_ID_LEN), PROVEN,
        ).is_err());
        *installs.value.borrow_mut() = Some("unresolved".into());
        assert!(initialize_pair(&root.0, &hold, &install, &holds, &installs, IDENTITY, PROVEN).is_err());
        *installs.value.borrow_mut() = None;
        initialize_pair(&root.0, &hold, &install, &holds, &installs, IDENTITY, PROVEN).unwrap();
        assert_eq!(pair_id(&root.0), Ok(Some(IDENTITY.into())));
        assert!(read_intent(&root.0).unwrap().is_some());
    }

    #[test]
    fn committed_pair_stays_unavailable_until_completion_is_acknowledged() {
        let root = Temp::new();
        write_pair_record(&root.0.join(PAIR_MARKER), IDENTITY).unwrap();
        let intent = root.0.join(PAIR_INTENT);
        write_pair_record(&intent, &format!("{IDENTITY}{}", "a".repeat(64))).unwrap();
        assert!(available_pair(&root.0).is_err());
        fs::remove_file(&intent).unwrap();
        sync_dir(&root.0).unwrap();
        assert_eq!(available_pair(&root.0), Ok(IDENTITY.into()));
        write_pair_record(&intent, "partial").unwrap();
        assert!(available_pair(&root.0).is_err());
    }

    #[test]
    fn pair_does_not_adopt_existing_slot_or_secret() {
        let root = Temp::new();
        let (hold, install) = empty_paths(&root);
        let hs = Fake::default();
        let is = Fake::default();
        *is.value.borrow_mut() = Some("existing record".into());
        assert!(initialize_pair(&root.0, &hold, &install, &hs, &is, IDENTITY, PROVEN).is_err());
        assert_eq!(is.read(), Ok(Some("existing record".into())));
        *is.value.borrow_mut() = None;
        set_state(&hold, State::Empty).unwrap();
        assert!(initialize_pair(&root.0, &hold, &install, &hs, &is, IDENTITY, PROVEN).is_err());
        assert_eq!(pair_id(&root.0), Ok(None));
    }

    #[test]
    fn interrupted_marker_rename_leaves_both_slots_unavailable() {
        let root = Temp::new();
        let (hold, install) = empty_paths(&root);

        struct InterruptRename<'a>(&'a Path);
        impl Store for InterruptRename<'_> {
            fn read(&self) -> Result<Option<String>, String> {
                // Once the stage exists, force rename to fail after the slot
                // files have been synced. The namespace has no commit marker.
                if self.0.join(PAIR_STAGE).exists() {
                    fs::create_dir(self.0.join(PAIR_MARKER))
                        .or_else(|err| if err.kind() == std::io::ErrorKind::AlreadyExists {
                            Ok(())
                        } else { Err(err) })
                        .map_err(|_| ERROR)?;
                }
                Ok(None)
            }

            fn insert(&self, _: &str) -> Result<(), String> { Err(ERROR.into()) }
            fn delete(&self, _: &str) -> Result<(), String> { Err(ERROR.into()) }
        }

        assert!(initialize_pair(
            &root.0, &hold, &install, &Fake::default(), &InterruptRename(&root.0),
            IDENTITY, PROVEN,
        ).is_err());
        assert_eq!(state(&hold), Ok(State::Empty));
        assert_eq!(state(&install), Ok(State::Empty));
        assert!(pair_id(&root.0).is_err());
        assert!(initialize_pair(
            &root.0, &hold, &install, &Fake::default(), &Fake::default(), IDENTITY, PROVEN,
        ).is_err());
    }

    #[test]
    fn first_root_fsync_failure_keeps_pair_staged_and_both_slots_blocked() {
        let root = Temp::new();
        let (hold, install) = empty_paths(&root);
        assert!(initialize_pair_with_sync(
            &root.0, &hold, &install, &Fake::default(), &Fake::default(),
            IDENTITY, PROVEN, |_| Err(ERROR.into()),
        ).is_err());
        assert_eq!(state(&hold), Ok(State::Uninitialized));
        assert_eq!(state(&install), Ok(State::Uninitialized));
        assert!(pair_id(&root.0).is_err());
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
