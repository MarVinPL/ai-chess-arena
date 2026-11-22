//! Private crash-recovery journal for coordinator update holds.
//!
//! Persist the 256-bit reservation secret BEFORE the HTTP reserve request.
//! The keyring entry cannot be read from webview JavaScript, preferences,
//! logs or app cache. If the credential store is unavailable, fail closed.
//! Do NOT automatically release a hold on startup: the system installer may
//! still be working. A future reconciler must check installation state first.

use serde::{Deserialize, Serialize};

const HOLD_SERVICE: &str = "pl.marvin.aichessarena.worker";
const HOLD_ACCOUNT: &str = "coordinator-update-hold-v1";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub(crate) struct PendingHold {
    pub(crate) server: String,
    pub(crate) worker_id: String,
    pub(crate) hold_token: String,
}

fn valid_record(record: &PendingHold) -> bool {
    let Ok(url) = reqwest::Url::parse(&record.server) else { return false };
    url.scheme() == "https" && url.host_str().is_some()
        && url.username().is_empty() && url.password().is_none()
        && url.query().is_none() && url.fragment().is_none()
        && url.port().is_none() && url.path() == "/"
        && (8..=96).contains(&record.worker_id.len())
        && record.worker_id.bytes().all(|b|
            b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        && record.hold_token.len() == 64
        && record.hold_token.bytes().all(|b|
            b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(HOLD_SERVICE, HOLD_ACCOUNT)
        .map_err(|_| "Systemowy magazyn rezerwacji jest niedostępny".into())
}

/// A missing entry is safe; unreadable/malformed entry is NOT treated as empty.
#[allow(dead_code)]
pub(crate) fn load_pending() -> Result<Option<PendingHold>, String> {
    let saved = match entry()?.get_password() {
        Ok(saved) => saved,
        Err(keyring::Error::NoEntry) => return Ok(None),
        Err(_) => return Err("Nie można odczytać zapisanej rezerwacji".into()),
    };
    let record: PendingHold = serde_json::from_str(&saved)
        .map_err(|_| "Uszkodzony zapis rezerwacji — wymaga uzgodnienia".to_string())?;
    if !valid_record(&record) {
        return Err("Nieprawidłowy zapis rezerwacji — wymaga uzgodnienia".into());
    }
    Ok(Some(record))
}

/// Never replace a previous pending transaction; its hold must be reconciled.
#[allow(dead_code)]
pub(crate) fn save_pending(record: &PendingHold) -> Result<(), String> {
    if !valid_record(record) { return Err("Nieprawidłowe dane rezerwacji".into()); }
    if load_pending()?.is_some() {
        return Err("Poprzednia rezerwacja wymaga uzgodnienia".into());
    }
    let serialized = serde_json::to_string(record)
        .map_err(|_| "Nie można przygotować zapisu rezerwacji".to_string())?;
    entry()?.set_password(&serialized)
        .map_err(|_| "Nie można trwale zapisać tokenu rezerwacji".to_string())?;
    // Verify persistent read-back before allowing the network reservation.
    if load_pending()?.as_ref() != Some(record) {
        return Err("Nie potwierdzono trwałego zapisu rezerwacji".into());
    }
    Ok(())
}

/// Call only after authenticated coordinator release AND installer outcome
/// reconciliation. Failure retains a recoverable journal; no silent fallback.
#[allow(dead_code)]
pub(crate) fn clear_pending(expected: &PendingHold) -> Result<(), String> {
    if load_pending()?.as_ref() != Some(expected) {
        return Err("Nie można potwierdzić właściwej rezerwacji".into());
    }
    entry()?.delete_credential()
        .map_err(|_| "Nie udało się usunąć zakończonej rezerwacji".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn malformed_or_untrusted_hold_is_never_storable() {
        let record = PendingHold {
            server: "https://arena.example/".into(),
            worker_id: "worker_12345678".into(),
            hold_token: "f".repeat(64),
        };
        assert!(valid_record(&record));
        let mut invalid = record.clone();
        invalid.server = "http://arena.example/".into();
        assert!(!valid_record(&invalid));
        invalid = record.clone();
        invalid.server = "https://arena.example/evil".into();
        assert!(!valid_record(&invalid));
        invalid = record.clone();
        invalid.worker_id = "bad".into();
        assert!(!valid_record(&invalid));
        invalid = record;
        invalid.hold_token = "missing".into();
        assert!(!valid_record(&invalid));
    }
}
