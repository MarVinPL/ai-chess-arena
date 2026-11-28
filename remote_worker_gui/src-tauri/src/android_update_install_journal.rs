//! Crash-safe journal for a submitted Android PackageInstaller session.
//! The package version check on relaunch is authoritative, never a callback.
use serde::{Deserialize, Serialize};

#[cfg(not(target_os = "android"))]
const ACCOUNT: &str = "android-package-install-v1";
#[cfg(not(target_os = "android"))]
const SERVICE: &str = "pl.marvin.aichessarena.worker";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct PendingInstall {
    pub(crate) session_id: i32,
    pub(crate) previous_version_code: u64,
    pub(crate) target_version_code: u64,
    pub(crate) apk_sha256: String,
}

fn valid(p: &PendingInstall) -> bool {
    p.session_id >= 0
        && p.previous_version_code > 0
        && p.target_version_code > p.previous_version_code
        && p.apk_sha256.len() == 64
        && p.apk_sha256
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[cfg(not(target_os = "android"))]
fn entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(SERVICE, ACCOUNT)
        .map_err(|_| "Niedostępny dziennik instalacji Android".into())
}

pub(crate) fn load() -> Result<Option<PendingInstall>, String> {
    #[cfg(target_os = "android")]
    let raw = match crate::credential_journal::read(crate::credential_vault::Slot::PendingInstall)?
    {
        Some(value) => value,
        None => return Ok(None),
    };
    #[cfg(not(target_os = "android"))]
    let raw = match entry()?.get_password() {
        Ok(v) => v,
        Err(keyring::Error::NoEntry) => return Ok(None),
        Err(_) => return Err("Nie można odczytać dziennika instalatora".into()),
    };
    let p: PendingInstall =
        serde_json::from_str(&raw).map_err(|_| "Uszkodzony dziennik instalacji".to_owned())?;
    if !valid(&p) {
        return Err("Nieprawidłowy dziennik instalacji".into());
    }
    Ok(Some(p))
}

/// Must be called before Session.commit(). Never overwrite an uncertain
/// previous installation; both the journal and hold survive process death.
pub(crate) fn save(p: &PendingInstall) -> Result<(), String> {
    let _credential_guard = crate::credential_change_gate()?;
    #[cfg(target_os = "android")]
    if crate::android_update_recovery::load_pending()?.is_none() {
        return Err("Brak rezerwacji dla instalacji".into());
    }
    if !valid(p) {
        return Err("Nieprawidłowa instalacja".into());
    }
    if load()?.is_some() {
        return Err("Wcześniejsza instalacja wymaga uzgodnienia".into());
    }
    let raw = serde_json::to_string(p).map_err(|_| "Błąd dziennika".to_owned())?;
    #[cfg(target_os = "android")]
    crate::credential_journal::insert(crate::credential_vault::Slot::PendingInstall, &raw)?;
    #[cfg(not(target_os = "android"))]
    entry()?
        .set_password(&raw)
        .map_err(|_| "Nie zapisano dziennika instalacji".to_owned())?;
    if load()?.as_ref() != Some(p) {
        return Err("Nie potwierdzono zapisu instalacji".into());
    }
    Ok(())
}

/// Clear only after the authenticated coordinator hold release, never after
/// an absent session, timeout, JS action or an unverified Android callback.
pub(crate) fn clear_confirmed(expected: &PendingInstall) -> Result<(), String> {
    let _credential_guard = crate::credential_change_gate()?;
    if load()?.as_ref() != Some(expected) {
        return Err("Dziennik instalacji nie zgadza się z oczekiwanym".into());
    }
    #[cfg(target_os = "android")]
    {
        let raw = serde_json::to_string(expected).map_err(|_| "Błąd dziennika")?;
        crate::credential_journal::delete_expected(
            crate::credential_vault::Slot::PendingInstall,
            &raw,
        )
    }
    #[cfg(not(target_os = "android"))]
    {
        entry()?
            .delete_credential()
            .map_err(|_| "Nie można zakończyć dziennika instalacji".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn journal_rejects_bad_versions_digest_and_session() {
        let mut p = PendingInstall {
            session_id: 3,
            previous_version_code: 1000,
            target_version_code: 1001,
            apk_sha256: "a".repeat(64),
        };
        assert!(valid(&p));
        p.target_version_code = 1000;
        assert!(!valid(&p));
        p.target_version_code = 1001;
        p.session_id = -1;
        assert!(!valid(&p));
        p.session_id = 3;
        p.apk_sha256 = "not-a-digest".into();
        assert!(!valid(&p));
    }
}
