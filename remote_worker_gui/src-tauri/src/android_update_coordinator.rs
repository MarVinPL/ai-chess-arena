//! Fail-closed coordinator idle evidence for Android Worker update checks.
//! An authenticated HTTPS response must echo a per-request OS-random nonce,
//! identify the Worker, report zero tasks and carry a recent server timestamp.
//! This is only a read-only safety check, NOT permission to install.

use serde_json::Value;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_IDLE_AGE_MS: u64 = 30_000;
const MAX_FUTURE_SKEW_MS: u64 = 5_000;

pub(crate) fn random_nonce() -> Result<String, String> {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes)
        .map_err(|_| "Brak bezpiecznego źródła losowości")?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect::<String>())
}

pub(crate) fn system_unix_ms() -> Result<u64, String> {
    let now = SystemTime::now().duration_since(UNIX_EPOCH)
        .map_err(|_| "Nieprawidłowy czas systemowy")?;
    u64::try_from(now.as_millis()).map_err(|_| "Nieprawidłowy czas systemowy".into())
}

pub(crate) fn fresh_authenticated_idle(
    response: &Value,
    worker_id: &str,
    nonce: &str,
    now_ms: u64,
) -> bool {
    if nonce.len() != 32 || !nonce.bytes().all(|c|
        c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        return false;
    }
    if response.get("state").and_then(Value::as_str) != Some("idle_confirmed")
        || response.get("authenticated").and_then(Value::as_bool) != Some(true)
        || response.get("source").and_then(Value::as_str) != Some("coordinator")
        || response.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || response.get("nonce").and_then(Value::as_str) != Some(nonce)
        || response.get("active_tasks").and_then(Value::as_u64) != Some(0) {
        return false;
    }
    let Some(checked_at) = response.get("checked_at_unix_ms").and_then(Value::as_u64)
        else { return false; };
    if checked_at > now_ms {
        checked_at - now_ms <= MAX_FUTURE_SKEW_MS
    } else {
        now_ms - checked_at <= MAX_IDLE_AGE_MS
    }
}

/// A coordinator-issued reservation token is a short-lived capability and
/// must never be returned to the webview or included in diagnostic logs.
#[derive(Debug)]
#[allow(dead_code)]
pub(crate) struct UpdateHold {
    pub(crate) token: String,
    pub(crate) expires_at_unix_ms: u64,
}

/// Validate an authenticated reserve response before *ever* passing a hold
/// into the installer. This proves only server confirmation, not that the
/// APK or Android installation is safe.
#[allow(dead_code)]
pub(crate) fn verify_reservation_reply(
    response: &Value,
    worker_id: &str,
    nonce: &str,
    now_ms: u64,
) -> Result<UpdateHold, String> {
    if nonce.len() != 32 || !nonce.bytes().all(|c|
        c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        return Err("Nieprawidłowy identyfikator rezerwacji".into());
    }
    if response.get("state").and_then(Value::as_str) != Some("reserved")
        || response.get("authenticated").and_then(Value::as_bool) != Some(true)
        || response.get("source").and_then(Value::as_str) != Some("coordinator")
        || response.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || response.get("nonce").and_then(Value::as_str) != Some(nonce)
        || response.get("install_allowed").and_then(Value::as_bool) != Some(false) {
        return Err("Niepotwierdzona rezerwacja koordynatora".into());
    }
    let token = response.get("hold_token").and_then(Value::as_str)
        .ok_or("Brak tokenu rezerwacji")?;
    if token.len() != 64 || !token.bytes().all(|c|
        c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        return Err("Nieprawidłowy token rezerwacji".into());
    }
    let expires_at = response.get("expires_at_unix_ms").and_then(Value::as_u64)
        .ok_or("Brak czasu ważności rezerwacji")?;
    let remaining = expires_at.checked_sub(now_ms)
        .ok_or("Rezerwacja już wygasła")?;
    // Server issues 300-second holds. Reject implausibly distant deadlines
    // instead of trusting arbitrary fields supplied by a stale intermediary.
    if remaining < 5_000 || remaining > 305_000 {
        return Err("Rezerwacja jest nieaktualna lub ma niepoprawny czas ważności".into());
    }
    Ok(UpdateHold { token: token.to_owned(), expires_at_unix_ms: expires_at })
}

/// Releasing a reservation is NOT a new idle confirmation. The coordinator
/// deliberately returns unknown, so new tasks require reconciliation.
#[allow(dead_code)]
pub(crate) fn verify_release_reply(
    response: &Value,
    worker_id: &str,
    nonce: &str,
) -> bool {
    nonce.len() == 32
        && nonce.bytes().all(|c|
            c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        && response.get("released").and_then(Value::as_bool) == Some(true)
        && response.get("authenticated").and_then(Value::as_bool) == Some(true)
        && response.get("source").and_then(Value::as_str) == Some("coordinator")
        && response.get("worker_id").and_then(Value::as_str) == Some(worker_id)
        && response.get("nonce").and_then(Value::as_str) == Some(nonce)
        && response.get("state").and_then(Value::as_str) == Some("unknown")
        && response.get("install_allowed").and_then(Value::as_bool) == Some(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    const NONCE: &str = "00112233445566778899aabbccddeeff";
    fn reply() -> Value {
        json!({
            "state": "idle_confirmed", "authenticated": true,
            "source": "coordinator", "worker_id": "worker_12345678",
            "nonce": NONCE, "active_tasks": 0,
            "checked_at_unix_ms": 1_000_000
        })
    }
    #[test]
    fn requires_all_authenticated_fresh_idle_evidence() {
        assert!(fresh_authenticated_idle(&reply(), "worker_12345678", NONCE, 1_010_000));
    }
    #[test]
    fn rejects_replay_stale_future_wrong_worker_and_tasks() {
        let r = reply();
        assert!(!fresh_authenticated_idle(&r, "worker_12345678", NONCE, 1_030_001));
        assert!(!fresh_authenticated_idle(&r, "worker_12345678", NONCE, 994_999));
        assert!(!fresh_authenticated_idle(&r, "worker_98765432", NONCE, 1_000_000));
        assert!(!fresh_authenticated_idle(&r, "worker_12345678", "a".repeat(32).as_str(), 1_000_000));
        for (key, value) in [
            ("state", json!("running")),
            ("authenticated", json!(false)),
            ("source", json!("worker")),
            ("active_tasks", json!(1)),
            ("checked_at_unix_ms", json!(null)),
        ] {
            let mut changed = reply();
            changed[key] = value;
            assert!(!fresh_authenticated_idle(&changed, "worker_12345678", NONCE, 1_000_000));
        }
    }
    #[test]
    fn rejects_legacy_or_incomplete_server_answers() {
        assert!(!fresh_authenticated_idle(&json!({"state":"idle_confirmed"}),
            "worker_12345678", NONCE, 1_000_000));
        assert!(!fresh_authenticated_idle(&json!({"state":"unknown"}),
            "worker_12345678", NONCE, 1_000_000));
        assert!(!fresh_authenticated_idle(&reply(),
            "worker_12345678", "invalid-nonce", 1_000_000));
    }
    #[test]
    fn reservation_requires_bound_nonce_and_bounded_lifetime() {
        let response = json!({
            "state": "reserved", "authenticated": true,
            "source": "coordinator", "worker_id": "worker_12345678",
            "nonce": NONCE, "install_allowed": false,
            "hold_token": "c".repeat(64), "expires_at_unix_ms": 1_300_000
        });
        let hold = verify_reservation_reply(&response, "worker_12345678", NONCE, 1_000_000).unwrap();
        assert_eq!(hold.token.len(), 64);
        assert_eq!(hold.expires_at_unix_ms, 1_300_000);
        for (key, changed) in [
            ("state", json!("idle_confirmed")),
            ("authenticated", json!(false)),
            ("source", json!("worker")),
            ("nonce", json!("a".repeat(32))),
            ("worker_id", json!("worker_87654321")),
            ("hold_token", json!("invalid")),
            ("install_allowed", json!(true)),
            ("expires_at_unix_ms", json!(1_500_000)),
        ] {
            let mut forged = response.clone();
            forged[key] = changed;
            assert!(verify_reservation_reply(&forged, "worker_12345678", NONCE, 1_000_000).is_err());
        }
        assert!(verify_reservation_reply(&response, "worker_12345678", NONCE, 1_300_001).is_err());
        assert!(verify_reservation_reply(&response, "worker_12345678", NONCE, 1_297_000).is_err());
    }
    #[test]
    fn release_is_never_conflated_with_idle_or_install_permission() {
        let response = json!({
            "state": "unknown", "released": true, "authenticated": true,
            "source": "coordinator", "worker_id": "worker_12345678",
            "nonce": NONCE, "install_allowed": false
        });
        assert!(verify_release_reply(&response, "worker_12345678", NONCE));
        for (key, changed) in [
            ("state", json!("idle_confirmed")),
            ("released", json!(false)),
            ("authenticated", json!(false)),
            ("nonce", json!("f".repeat(32))),
            ("install_allowed", json!(true))
        ] {
            let mut forged = response.clone();
            forged[key] = changed;
            assert!(!verify_release_reply(&forged, "worker_12345678", NONCE));
        }
    }
    #[test]
    fn random_nonce_uses_os_rng_and_is_correctly_encoded() {
        let a = random_nonce().unwrap();
        let b = random_nonce().unwrap();
        assert_ne!(a, b);
        assert_eq!(a.len(), 32);
        assert!(a.bytes().all(|c| c.is_ascii_hexdigit()));
    }
}
