//! Internal machine-token boundary. No new IPC or storage migration.
use std::sync::Mutex;

static OPERATIONS: Mutex<()> = Mutex::new(());

pub(crate) trait Backend {
    fn read(&self) -> Result<String, String>;
    fn write(&self, value: &str) -> Result<(), String>;
    fn delete(&self) -> Result<(), String>;
}

pub(crate) struct CredentialVault<B> {
    backend: B,
    operations: &'static Mutex<()>,
}

fn valid(value: &str) -> bool {
    (24..=512).contains(&value.len())
        && value.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

impl<B: Backend> CredentialVault<B> {
    pub(crate) fn new(backend: B) -> Self { Self { backend, operations: &OPERATIONS } }
    pub(crate) fn get_password(&self) -> Result<String, String> {
        let _guard = self.operations.lock().map_err(|_| "Magazyn poświadczeń niedostępny")?;
        self.backend.read()
    }

    pub(crate) fn set_password(&self, value: &str, check: impl FnOnce() -> Result<(), String>) -> Result<(), String> {
        let _guard = self.operations.lock().map_err(|_| "Magazyn poświadczeń niedostępny")?;
        check()?;
        if !valid(value) {
            return Err("Nieprawidłowy format tokenu".into());
        }
        self.backend.write(value).map_err(|_| "Nie udało się zapisać tokenu w systemowym magazynie".into())
    }

    pub(crate) fn delete_credential(&self, check: impl FnOnce() -> Result<(), String>) -> Result<(), String> {
        let _guard = self.operations.lock().map_err(|_| "Magazyn poświadczeń niedostępny")?;
        check()?;
        self.backend.delete()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[derive(Default)]
    struct Fake(Mutex<Option<String>>);
    impl Backend for Fake {
        fn read(&self) -> Result<String, String> { self.0.lock().unwrap().clone().ok_or("missing".into()) }
        fn write(&self, value: &str) -> Result<(), String> { *self.0.lock().unwrap() = Some(value.into()); Ok(()) }
        fn delete(&self) -> Result<(), String> { *self.0.lock().unwrap() = None; Ok(()) }
    }
    fn vault() -> CredentialVault<Fake> { CredentialVault::new(Fake::default()) }
    #[test]
    fn lifecycle_and_rotation() {
        let v = vault();
        assert!(v.get_password().is_err());
        for token in ["a".repeat(24), "b".repeat(512)] {
            v.set_password(&token, || Ok(())).unwrap();
            assert_eq!(v.get_password().unwrap(), token);
        }
        v.delete_credential(|| Ok(())).unwrap();
        v.delete_credential(|| Ok(())).unwrap();
        assert!(v.get_password().is_err());
    }
    #[test]
    fn rejects_invalid_and_pending_mutations() {
        let v = vault();
        let token = "a".repeat(32);
        v.set_password(&token, || Ok(())).unwrap();
        for bad in ["x".into(), "x".repeat(513), "!".repeat(32)] {
            assert!(v.set_password(&bad, || Ok(())).is_err());
        }
        assert!(v.set_password(&"b".repeat(32), || Err("pending".into())).is_err());
        assert!(v.delete_credential(|| Err("unknown".into())).is_err());
        assert_eq!(v.get_password().unwrap(), token);
    }
}
