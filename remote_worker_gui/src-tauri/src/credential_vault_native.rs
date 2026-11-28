//! Platform adapters preserving existing credential identities.
use crate::credential_vault::{Backend, CredentialVault};

#[cfg(target_os = "android")]
pub(crate) type NativeBackend = crate::android_worker_token_store::AndroidTokenEntry;

#[cfg(not(target_os = "android"))]
pub(crate) type NativeBackend = keyring::Entry;

#[cfg(target_os = "android")]
impl Backend for NativeBackend {
    fn read(&self) -> Result<Option<String>, String> {
        self.read_optional()
    }
    fn write(&self, value: &str) -> Result<(), String> {
        self.set_password(value)
    }
    fn delete(&self) -> Result<(), String> {
        self.delete_credential()
            .map_err(|_| "Nie udało się usunąć tokenu z Android Keystore".into())
    }
}

#[cfg(not(target_os = "android"))]
impl Backend for NativeBackend {
    fn read(&self) -> Result<Option<String>, String> {
        match self.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err("Systemowy magazyn poświadczeń jest niedostępny".into()),
        }
    }
    fn write(&self, value: &str) -> Result<(), String> {
        self.set_password(value)
            .map_err(|_| "Systemowy magazyn poświadczeń jest niedostępny".into())
    }
    fn delete(&self) -> Result<(), String> {
        match self.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err("Nie udało się usunąć tokenu".into()),
        }
    }
}

pub(crate) fn native() -> Result<CredentialVault<NativeBackend>, String> {
    // Never use keyring v3 on Android: it may select a mock store.
    #[cfg(target_os = "android")]
    let backend = crate::android_worker_token_store::AndroidTokenEntry;
    #[cfg(not(target_os = "android"))]
    let backend = keyring::Entry::new("pl.marvin.aichessarena.worker", "worker-token")
        .map_err(|_| "Systemowy magazyn poświadczeń jest niedostępny".to_string())?;
    Ok(CredentialVault::new(backend))
}
