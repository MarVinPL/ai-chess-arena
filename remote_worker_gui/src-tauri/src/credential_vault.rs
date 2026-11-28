//! Internal machine-token boundary. No new IPC or storage migration.
use std::sync::Mutex;

static OPERATIONS: Mutex<()> = Mutex::new(());

pub(crate) trait Backend {
    fn read(&self) -> Result<Option<String>, String>;
    fn write(&self, value: &str) -> Result<(), String>;
    fn delete(&self) -> Result<(), String>;
}

pub(crate) struct CredentialVault<B> {
    backend: B,
    operations: &'static Mutex<()>,
}

fn valid(value: &str) -> bool {
    (24..=512).contains(&value.len())
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

impl<B: Backend> CredentialVault<B> {
    pub(crate) fn new(backend: B) -> Self {
        Self {
            backend,
            operations: &OPERATIONS,
        }
    }
    pub(crate) fn read_optional(&self) -> Result<Option<String>, String> {
        let _guard = self
            .operations
            .lock()
            .map_err(|_| "Magazyn poświadczeń niedostępny")?;
        self.checked_read()
    }

    fn checked_read(&self) -> Result<Option<String>, String> {
        let value = self.backend.read()?;
        if value.as_ref().is_some_and(|v| !valid(v)) {
            return Err("Nieprawidłowe dane magazynu poświadczeń".into());
        }
        Ok(value)
    }

    pub(crate) fn get_password(&self) -> Result<String, String> {
        self.read_optional()?
            .ok_or_else(|| "Brak zapisanego tokenu".into())
    }

    pub(crate) fn set_password(
        &self,
        value: &str,
        check: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
        let _guard = self
            .operations
            .lock()
            .map_err(|_| "Magazyn poświadczeń niedostępny")?;
        check()?;
        if !valid(value) {
            return Err("Nieprawidłowy format tokenu".into());
        }
        self.checked_read()?;
        self.backend
            .write(value)
            .map_err(|_| "Nie udało się zapisać tokenu w systemowym magazynie".into())
    }

    /// Write only the first authenticated token. Recheck under the vault lock
    /// because a different registration may have finished during HTTPS.
    #[cfg(any(target_os = "android", test))]
    pub(crate) fn insert_registration_token(
        &self, value: &str, check: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
        let _guard = self.operations.lock()
            .map_err(|_| "Magazyn poświadczeń niedostępny")?;
        if self.registration_token_missing(value)? {
            check()?;
            self.backend.write(value)
                .map_err(|_| "Nie udało się zapisać tokenu w systemowym magazynie")?;
        }
        Ok(())
    }

    #[cfg(any(target_os = "android", test))]
    fn registration_token_missing(&self, value: &str) -> Result<bool, String> {
        if !valid(value) {
            return Err("Nieprawidłowy format tokenu".into());
        }
        match self.checked_read()? {
            None => Ok(true),
            Some(current) if current == value => Ok(false),
            Some(_) => Err("Zapisany token Workera różni się od podanego".into()),
        }
    }

    #[cfg(any(target_os = "android", test))]
    pub(crate) async fn authenticate_registration(
        &self, value: &str, authenticate: impl std::future::Future<Output = Result<bool, String>>,
    ) -> Result<(), String> {
        {
            let _guard = self.operations.lock()
                .map_err(|_| "Magazyn poświadczeń niedostępny")?;
            self.registration_token_missing(value)?;
        }
        if !authenticate.await? {
            return Err("Arena odrzuciła token dla tego identyfikatora Workera".into());
        }
        Ok(())
    }

    pub(crate) fn delete_credential(
        &self,
        check: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
        let _guard = self
            .operations
            .lock()
            .map_err(|_| "Magazyn poświadczeń niedostępny")?;
        check()?;
        self.checked_read()?;
        self.backend.delete()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[derive(Default)]
    struct Fake(Mutex<Option<String>>);
    impl Backend for Fake {
        fn read(&self) -> Result<Option<String>, String> {
            Ok(self.0.lock().unwrap().clone())
        }
        fn write(&self, value: &str) -> Result<(), String> {
            *self.0.lock().unwrap() = Some(value.into());
            Ok(())
        }
        fn delete(&self) -> Result<(), String> {
            *self.0.lock().unwrap() = None;
            Ok(())
        }
    }
    fn vault() -> CredentialVault<Fake> {
        CredentialVault::new(Fake::default())
    }
    #[test]
    fn absence_corruption_and_backend_failure_are_distinct() {
        let v = vault();
        assert_eq!(v.read_optional(), Ok(None));
        *v.backend.0.lock().unwrap() = Some("invalid".into());
        assert!(v.read_optional().is_err());
        assert!(v.set_password(&"a".repeat(32), || Ok(())).is_err());
        assert!(v.delete_credential(|| Ok(())).is_err());
        assert_eq!(v.backend.0.lock().unwrap().as_deref(), Some("invalid"));
        struct Broken;
        impl Backend for Broken {
            fn read(&self) -> Result<Option<String>, String> {
                Err("unavailable".into())
            }
            fn write(&self, _: &str) -> Result<(), String> {
                Err("commit failed".into())
            }
            fn delete(&self) -> Result<(), String> {
                Err("commit failed".into())
            }
        }
        let broken = CredentialVault::new(Broken);
        assert!(broken.read_optional().is_err());
        assert!(broken.set_password(&"a".repeat(32), || Ok(())).is_err());
        assert!(broken.delete_credential(|| Ok(())).is_err());
    }
    #[test]
    fn registration_only_persists_after_authentication_and_keeps_existing_token() {
        let vault = vault();
        let token = "a".repeat(43);
        let other = "b".repeat(43);
        let authenticate = |result| {
            tauri::async_runtime::block_on(vault.authenticate_registration(
                &token, std::future::ready(result),
            ))
        };
        assert!(authenticate(Ok(false)).is_err());
        assert!(authenticate(Err("offline".into())).is_err());
        assert_eq!(vault.read_optional(), Ok(None));
        authenticate(Ok(true)).unwrap();
        assert_eq!(vault.read_optional(), Ok(None)); // Interrupted before local commit.
        assert!(vault.insert_registration_token(&token, || Err("journal conflict".into())).is_err());
        assert_eq!(vault.read_optional(), Ok(None));
        vault.insert_registration_token(&token, || Ok(())).unwrap();
        authenticate(Ok(true)).unwrap();
        vault.insert_registration_token(&token, || panic!("existing credential must be left untouched")).unwrap();
        assert!(vault.insert_registration_token(&other, || Ok(())).is_err());
        assert_eq!(vault.get_password(), Ok(token));
    }

    #[test]
    fn registration_rechecks_vault_after_await_and_rejects_unavailable_storage() {
        let vault = vault();
        let token = "a".repeat(43);
        let other = "b".repeat(43);
        tauri::async_runtime::block_on(vault.authenticate_registration(&token, async {
            vault.insert_registration_token(&other, || Ok(())).unwrap();
            Ok(true)
        })).unwrap();
        assert!(vault.insert_registration_token(&token, || Ok(())).is_err());
        assert_eq!(vault.get_password(), Ok(other));
        *vault.backend.0.lock().unwrap() = Some("corrupt".into());
        assert!(tauri::async_runtime::block_on(vault.authenticate_registration(&token, async {
            panic!("must not send a token when the local vault cannot be read")
        })).is_err());
    }

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
        assert!(v
            .set_password(&"b".repeat(32), || Err("pending".into()))
            .is_err());
        assert!(v.delete_credential(|| Err("unknown".into())).is_err());
        assert_eq!(v.get_password().unwrap(), token);
    }
}

#[cfg(target_os = "android")]
#[derive(Clone, Copy)]
pub(crate) enum Slot {
    PendingHold,
    PendingInstall,
}

#[cfg(target_os = "android")]
impl Slot {
    pub(crate) fn account(self) -> &'static str {
        match self {
            Self::PendingHold => "pending-hold-v1",
            Self::PendingInstall => "pending-install-v1",
        }
    }
}

#[cfg(target_os = "android")]
pub(crate) mod journals {
    use super::Slot;
    use crate::android_worker_token_store as native;
    pub(crate) fn read(slot: Slot) -> Result<Option<String>, String> {
        native::journal_read(slot.account())
    }
    pub(crate) fn insert(slot: Slot, raw: &str) -> Result<(), String> {
        native::journal_mutate("journalInsert", slot.account(), raw)
    }
    pub(crate) fn delete_expected(slot: Slot, raw: &str) -> Result<(), String> {
        native::journal_mutate("journalDelete", slot.account(), raw)
    }
}
