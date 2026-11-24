//! #325: storage-only outbox. Caller must use Android Context.getNoBackupFilesDir()
//! and must observe actual native thread termination before storing. No IPC,
//! transport, checkpoint ACK or permission to install an update is granted here.
#![cfg(unix)]

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

const LIMIT: usize = 512;
const PENDING: &str = "pending.v1";
static SERIAL: AtomicU64 = AtomicU64::new(0);

// Deliberately no Debug or Serialize: lease_token is a bearer secret.
#[derive(PartialEq, Eq)]
pub(crate) struct StopReport {
    worker_id: String,
    job_id: String,
    request_id: String,
    lease_token: String,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum OutboxError {
    Invalid, Corrupt, Conflict, Storage,
}
type Result<T> = std::result::Result<T, OutboxError>;

fn valid_id(s: &str) -> bool {
    (8..=96).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}
fn hex(s: &str, n: usize) -> bool {
    s.len() == n && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

// Detect accidental corruption only; CRC is NOT authentication.
// Recovery must independently verify the lease at the coordinator (#326).
fn crc32(data: &[u8]) -> u32 {
    let mut c = !0u32;
    for &b in data {
        c ^= u32::from(b);
        for _ in 0..8 { c = (c >> 1) ^ (0xedb88320 & 0u32.wrapping_sub(c & 1)); }
    }
    !c
}

impl StopReport {
    pub(crate) fn new(worker_id: &str, job_id: &str, request_id: &str, lease_token: &str) -> Result<Self> {
        if !valid_id(worker_id) || !valid_id(job_id) || !hex(request_id, 32) || !hex(lease_token, 64) {
            return Err(OutboxError::Invalid);
        }
        Ok(Self { worker_id: worker_id.into(), job_id: job_id.into(), request_id: request_id.into(), lease_token: lease_token.into() })
    }
    fn encode(&self) -> Vec<u8> {
        let body = format!("STOP1\n{}\n{}\n{}\n{}\n", self.worker_id, self.job_id, self.request_id, self.lease_token);
        format!("{}{:08x}\n", body, crc32(body.as_bytes())).into_bytes()
    }
    fn decode(bytes: &[u8]) -> Result<Self> {
        if bytes.len() > LIMIT { return Err(OutboxError::Corrupt); }
        let s = std::str::from_utf8(bytes).map_err(|_| OutboxError::Corrupt)?;
        let f: Vec<_> = s.split('\n').collect();
        if f.len() != 7 || f[0] != "STOP1" || !hex(f[5], 8) || !f[6].is_empty() {
            return Err(OutboxError::Corrupt);
        }
        let r = Self::new(f[1], f[2], f[3], f[4]).map_err(|_| OutboxError::Corrupt)?;
        if r.encode() != bytes { return Err(OutboxError::Corrupt); }
        Ok(r)
    }
}

pub(crate) struct StopOutbox { dir: PathBuf }
fn storage(_: io::Error) -> OutboxError { OutboxError::Storage }
fn sync_dir(path: &Path) -> Result<()> { File::open(path).and_then(|f| f.sync_all()).map_err(storage) }
fn private_dir(path: &Path) -> Result<()> {
    let m = fs::symlink_metadata(path).map_err(storage)?;
    if !m.is_dir() || m.permissions().mode() & 0o077 != 0 { return Err(OutboxError::Storage); }
    Ok(())
}
struct Stage(PathBuf);
impl Drop for Stage { fn drop(&mut self) { let _ = fs::remove_file(&self.0); } }

impl StopOutbox {
    /// Caller supplies a trusted app-private, existing no-backup directory.
    pub(crate) fn open(root: &Path) -> Result<Self> {
        // Android owns this root; platform versions may use a shared app
        // group mode. Never chmod the platform directory. Only our dedicated
        // child must be 0700, and neither path may be a symlink.
        if !fs::symlink_metadata(root).map_err(storage)?.is_dir() {
            return Err(OutboxError::Storage);
        }
        let dir = root.join("native-stop-outbox");
        match fs::DirBuilder::new().mode(0o700).create(&dir) {
            Ok(()) => (),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => (),
            Err(e) => return Err(storage(e)),
        }
        private_dir(&dir)?;
        sync_dir(root)?;
        Ok(Self { dir })
    }
    pub(crate) fn load(&self) -> Result<Option<StopReport>> {
        let path = self.dir.join(PENDING);
        let m = match fs::symlink_metadata(&path) {
            Ok(m) => m,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(storage(e)),
        };
        if !m.is_file() || m.permissions().mode() & 0o077 != 0 || m.len() > LIMIT as u64 {
            return Err(OutboxError::Corrupt);
        }
        let mut bytes = Vec::new();
        File::open(path).map_err(storage)?.take((LIMIT + 1) as u64).read_to_end(&mut bytes).map_err(storage)?;
        StopReport::decode(&bytes).map(Some)
    }
    /// fsync staging bytes, atomically publish without replacement, then fsync
    /// directory. Interrupted staging files are never read as STOP evidence.
    pub(crate) fn store(&self, report: &StopReport) -> Result<()> {
        if let Some(r) = self.load()? {
            return if &r == report { sync_dir(&self.dir) } else { Err(OutboxError::Conflict) };
        }
        let (stage, mut file) = loop {
            let n = SERIAL.fetch_add(1, Ordering::Relaxed);
            let p = self.dir.join(format!(".stage-{}-{}", std::process::id(), n));
            match OpenOptions::new().write(true).create_new(true).mode(0o600).open(&p) {
                Ok(f) => break (Stage(p), f),
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(storage(e)),
            }
        };
        file.write_all(&report.encode()).map_err(storage)?;
        file.sync_all().map_err(storage)?;
        drop(file);
        match fs::hard_link(&stage.0, self.dir.join(PENDING)) {
            Ok(()) => (),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
                if self.load()?.as_ref() != Some(report) { return Err(OutboxError::Conflict); }
            }
            Err(e) => return Err(storage(e)),
        }
        sync_dir(&self.dir)?;
        fs::remove_file(&stage.0).map_err(storage)?;
        sync_dir(&self.dir)
    }
}

/// Join and persist in the same blocking task. The caller must retain its
/// generation permit throughout this call, even if the async waiter is dropped.
/// Never invoke persistence for a panic, a failed STOP signal, or an invalid
/// STOP instruction. A failed write cannot produce a successful STOP identity.
pub(crate) fn complete_joined_stop<T>(
    engine: std::thread::JoinHandle<T>,
    signal: std::result::Result<(), String>,
    stop: std::result::Result<String, String>,
    persist: impl FnOnce(&str) -> Result<()>,
) -> std::result::Result<String, String> {
    engine.join().map_err(|_| "Błąd wątku llama.cpp".to_owned())?;
    signal?;
    let request_id = stop?;
    persist(&request_id).map_err(|_| "Nie zapisano trwałego raportu STOP".to_owned())?;
    Ok(request_id)
}

#[cfg(target_os = "android")]
impl StopOutbox {
    /// Resolve Android's private, explicitly excluded-from-backup directory.
    /// The caller cannot supply a path through a WebView or remote message.
    /// No JNI exception details or lease contents are returned.
    pub(crate) fn open_android() -> Result<Self> {
        use jni::objects::{JObject, JString};
        use jni::JavaVM;
        let ctx = ndk_context::android_context();
        let vm = unsafe { JavaVM::from_raw(ctx.vm().cast()) }
            .map_err(|_| OutboxError::Storage)?;
        let mut env = vm.attach_current_thread().map_err(|_| OutboxError::Storage)?;
        let context = unsafe { JObject::from_raw(ctx.context().cast()) };
        let root = (|| {
            let files = env.call_method(&context, "getNoBackupFilesDir", "()Ljava/io/File;", &[])
                .map_err(|_| OutboxError::Storage)?
                .l().map_err(|_| OutboxError::Storage)?;
            if files.is_null() { return Err(OutboxError::Storage); }
            let path = env.call_method(&files, "getAbsolutePath", "()Ljava/lang/String;", &[])
                .map_err(|_| OutboxError::Storage)?
                .l().map_err(|_| OutboxError::Storage)?;
            if path.is_null() { return Err(OutboxError::Storage); }
            let path: String = env.get_string(&JString::from(path))
                .map_err(|_| OutboxError::Storage)?.into();
            if !Path::new(&path).is_absolute() { return Err(OutboxError::Storage); }
            Ok(PathBuf::from(path))
        })();
        // Do not leave an exception pending on the attached worker thread.
        if env.exception_check().map_err(|_| OutboxError::Storage)? {
            env.exception_clear().map_err(|_| OutboxError::Storage)?;
            return Err(OutboxError::Storage);
        }
        Self::open(&root?)
    }

    /// Internal call site: only after the actual engine join succeeded and
    /// the coordinator's STOP identity matched. Does not send or clear STOP.
    pub(crate) fn persist_after_join(
        worker_id: &str, job_id: &str, request_id: &str, lease_token: &str,
    ) -> Result<()> {
        let report = StopReport::new(worker_id, job_id, request_id, lease_token)?;
        Self::open_android()?.store(&report)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    struct Temp(PathBuf);
    impl Temp {
        fn new() -> Self {
            let p = std::env::temp_dir().join(format!("stop-outbox-{}-{}", std::process::id(), SERIAL.fetch_add(1, Ordering::Relaxed)));
            fs::DirBuilder::new().mode(0o700).create(&p).unwrap();
            Self(p)
        }
    }
    impl Drop for Temp { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }
    fn report() -> StopReport { StopReport::new("worker_1234", "job_12345678", &"a".repeat(32), &"b".repeat(64)).unwrap() }
    fn other() -> StopReport { StopReport::new("worker_1234", "job_87654321", &"a".repeat(32), &"b".repeat(64)).unwrap() }

    #[test]
    fn roundtrip_and_repeat() {
        let t = Temp::new(); let r = report(); let q = StopOutbox::open(&t.0).unwrap();
        assert!(q.load().unwrap().is_none());
        q.store(&r).unwrap(); q.store(&r).unwrap();
        let q = StopOutbox::open(&t.0).unwrap();
        assert!(q.load().unwrap().as_ref() == Some(&r));
        assert_eq!(fs::metadata(q.dir.join(PENDING)).unwrap().permissions().mode() & 0o777, 0o600);
    }
    #[test]
    fn conflict_preserves_first() {
        let t = Temp::new(); let q = StopOutbox::open(&t.0).unwrap(); let r = report();
        q.store(&r).unwrap();
        assert_eq!(q.store(&other()), Err(OutboxError::Conflict));
        assert!(q.load().unwrap().as_ref() == Some(&r));
    }
    #[test]
    fn invalid_truncated_oversize_and_changed_bytes() {
        let t = Temp::new(); let q = StopOutbox::open(&t.0).unwrap(); let r = report();
        q.store(&r).unwrap();
        let p = q.dir.join(PENDING);
        let mut changed = r.encode(); changed[7] = b'x';
        for bytes in [b"STOP1\n".to_vec(), changed, vec![b'x'; LIMIT+1]] {
            fs::write(&p, bytes).unwrap();
            assert!(matches!(q.load(), Err(OutboxError::Corrupt)));
            assert_eq!(q.store(&r), Err(OutboxError::Corrupt));
        }
    }
    #[test]
    fn interrupted_staging_not_visible() {
        let t = Temp::new(); let q = StopOutbox::open(&t.0).unwrap();
        OpenOptions::new().write(true).create_new(true).mode(0o600).open(q.dir.join(".stage-interrupted")).unwrap().write_all(b"partial").unwrap();
        assert!(StopOutbox::open(&t.0).unwrap().load().unwrap().is_none());
        q.store(&report()).unwrap();
        assert!(q.load().unwrap().is_some());
    }
    #[test]
    fn symlink_and_insecure_mode_rejected() {
        let t = Temp::new(); let q = StopOutbox::open(&t.0).unwrap(); let p = q.dir.join(PENDING);
        symlink(t.0.join("missing"), &p).unwrap();
        assert!(matches!(q.load(), Err(OutboxError::Corrupt)));
        fs::remove_file(&p).unwrap(); q.store(&report()).unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(matches!(q.load(), Err(OutboxError::Corrupt)));
    }
    #[test]
    fn concurrent_writers_no_replace() {
        let t = Temp::new(); let q = StopOutbox::open(&t.0).unwrap(); let r = report(); let o = other();
        let (a,b) = std::thread::scope(|s| {
            let a = s.spawn(|| q.store(&r)); let b = s.spawn(|| q.store(&o));
            (a.join().unwrap(), b.join().unwrap())
        });
        assert!((a == Ok(()) && b == Err(OutboxError::Conflict)) || (b == Ok(()) && a == Err(OutboxError::Conflict)));
        assert!(q.load().unwrap().as_ref() == Some(if a.is_ok() { &r } else { &o }));
    }
    #[test]
    fn joined_stop_persists_only_after_thread_exit() {
        use std::sync::{Arc, atomic::AtomicBool};
        struct Exited(Arc<AtomicBool>);
        impl Drop for Exited {
            fn drop(&mut self) { self.0.store(true, Ordering::Release); }
        }
        let t = Temp::new(); let q = StopOutbox::open(&t.0).unwrap();
        let exited = Arc::new(AtomicBool::new(false));
        let thread_flag = Arc::clone(&exited);
        let engine = std::thread::spawn(move || {
            let _exited = Exited(thread_flag);
        });
        let id = "a".repeat(32);
        let result = complete_joined_stop(engine, Ok(()), Ok(id.clone()), |request| {
            assert!(exited.load(Ordering::Acquire));
            q.store(&StopReport::new("worker_1234", "job_12345678", request, &"b".repeat(64))?)
        }).unwrap();
        assert_eq!(result, id);
        assert!(q.load().unwrap().as_ref() == Some(&report()));
    }

    #[test]
    fn failed_join_signal_or_instruction_never_persists() {
        let t = Temp::new(); let q = StopOutbox::open(&t.0).unwrap();
        for (panic, signal_ok, stop_ok) in [(true,true,true), (false,false,true), (false,true,false)] {
            let engine = std::thread::spawn(move || { if panic { panic!("test"); } });
            let signal = if signal_ok { Ok(()) } else { Err("signal".into()) };
            let stop = if stop_ok { Ok("a".repeat(32)) } else { Err("instruction".into()) };
            assert!(complete_joined_stop(engine, signal, stop, |_| {
                panic!("persistence must not run")
            }).is_err());
            assert!(q.load().unwrap().is_none());
        }
    }

    #[test]
    fn write_failure_or_conflict_cannot_return_successful_stop() {
        let t = Temp::new(); let q = StopOutbox::open(&t.0).unwrap();
        q.store(&other()).unwrap();
        let result = complete_joined_stop(std::thread::spawn(|| ()), Ok(()), Ok("a".repeat(32)), |_| q.store(&report()));
        assert_eq!(result, Err("Nie zapisano trwałego raportu STOP".into()));
        assert!(q.load().unwrap().as_ref() == Some(&other()));
        let result = complete_joined_stop(std::thread::spawn(|| ()), Ok(()), Ok("a".repeat(32)), |_| Err(OutboxError::Storage));
        assert!(result.is_err());
    }

    #[test]
    fn strict_schema_and_crc_vector() {
        assert_eq!(crc32(b"123456789"), 0xcbf43926);
        assert!(matches!(StopReport::new("worker\n1234", "job_12345678", &"a".repeat(32), &"b".repeat(64)), Err(OutboxError::Invalid)));
        assert!(matches!(StopReport::new("worker_1234", "job_12345678", &"A".repeat(32), &"b".repeat(64)), Err(OutboxError::Invalid)));
    }
}
