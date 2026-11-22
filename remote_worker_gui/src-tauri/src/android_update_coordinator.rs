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

/// Only trusted native Rust code should call these transport functions.
/// Frontend callers must never receive or persist the reservation capability.
/// Never follow cross-origin/HTTP redirects with the machine credential.
#[allow(dead_code)]
fn coordinator_endpoint(base: &str, action: &str) -> Result<reqwest::Url, String> {
    if !matches!(action, "update-reserve" | "update-release") {
        return Err("Nieznana operacja koordynatora".into());
    }
    let url = reqwest::Url::parse(base).map_err(|_| "Nieprawidłowy adres HTTPS")?;
    if url.scheme() != "https" || url.host_str().is_none()
        || !url.username().is_empty() || url.password().is_some()
        || url.query().is_some() || url.fragment().is_some()
        || url.port().is_some() || url.path() != "/" {
        return Err("Wymagany główny adres serwera HTTPS bez portu".into());
    }
    url.join(&format!("api/remote-workers/{action}"))
        .map_err(|_| "Nieprawidłowy endpoint koordynatora".into())
}

#[allow(dead_code)]
fn valid_worker_id(worker_id: &str) -> bool {
    (8..=96).contains(&worker_id.len()) && worker_id.bytes().all(|c|
        c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
}

#[allow(dead_code)]
fn update_http_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(std::time::Duration::from_secs(12))
        .build().map_err(|_| "Nie można utworzyć klienta HTTPS".into())
}

/// Authenticated, atomic coordinator reservation. A future installer must
/// acquire this only after it has verified APK bytes AND OS signing identity.
/// This API is internal; it is intentionally not a Tauri webview command.
#[allow(dead_code)]
pub(crate) async fn reserve_update(
    base: &str, worker_id: &str, machine_token: &str,
) -> Result<UpdateHold, String> {
    if !valid_worker_id(worker_id) || machine_token.is_empty() {
        return Err("Brak zaufanej tożsamości Workera".into());
    }
    let endpoint = coordinator_endpoint(base, "update-reserve")?;
    let mut secret = [0u8; 32];
    getrandom::getrandom(&mut secret)
        .map_err(|_| "Brak losowego tokenu rezerwacji")?;
    let hold_token: String = secret.iter().map(|b| format!("{b:02x}")).collect();
    let pending = crate::android_update_recovery::PendingHold {
        server: base.to_owned(), worker_id: worker_id.to_owned(), hold_token,
    };
    // The journal MUST be recoverable before the coordinator sees the request.
    crate::android_update_recovery::save_pending(&pending)?;
    let nonce = random_nonce()?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce,
            "hold_token": pending.hold_token
        }))
        .send().await.map_err(|_| "Brak potwierdzenia rezerwacji")?;
    if !response.status().is_success() {
        return Err("Koordynator odmówił rezerwacji aktualizacji".into());
    }
    let body: Value = response.json().await.map_err(|_|
        "Nieprawidłowa odpowiedź koordynatora")?;
    let verified = verify_reservation_reply(&body, worker_id, &nonce, system_unix_ms()?)?;
    if verified.token != pending.hold_token {
        return Err("Koordynator nie potwierdził zapisanego tokenu rezerwacji".into());
    }
    Ok(verified)
}

/// Explicit hold release after a cancelled/completed update transaction.
/// Release does not mark the Worker idle; coordinator sets state=unknown.
/// If release fails, durable coordinator hold stays in force (fail closed).
#[allow(dead_code)]
pub(crate) async fn release_update(
    base: &str, worker_id: &str, machine_token: &str,
) -> Result<(), String> {
    if !valid_worker_id(worker_id) || machine_token.is_empty() {
        return Err("Nieprawidłowe dane zwolnienia rezerwacji".into());
    }
    let pending = crate::android_update_recovery::load_pending()?
        .ok_or("Brak rezerwacji do uzgodnienia")?;
    if pending.server != base || pending.worker_id != worker_id {
        return Err("Rezerwacja dotyczy innego Workera lub koordynatora".into());
    }
    let endpoint = coordinator_endpoint(base, "update-release")?;
    let nonce = random_nonce()?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token,
            "nonce": nonce, "hold_token": pending.hold_token
        }))
        .send().await.map_err(|_| "Brak potwierdzenia zwolnienia rezerwacji")?;
    if !response.status().is_success() {
        return Err("Koordynator odmówił zwolnienia rezerwacji".into());
    }
    let body: Value = response.json().await.map_err(|_|
        "Nieprawidłowa odpowiedź o zwolnieniu rezerwacji")?;
    if verify_release_reply(&body, worker_id, &nonce) {
        crate::android_update_recovery::clear_pending(&pending)
    } else {
        Err("Koordynator nie potwierdził zwolnienia rezerwacji".into())
    }
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
    fn authenticated_update_transport_rejects_untrusted_destinations() {
        assert!(coordinator_endpoint("https://arena.example/", "update-reserve").is_ok());
        for base in [
            "http://arena.example/", "https://arena.example:8090/",
            "https://user:password@arena.example/", "https://arena.example/other",
            "https://arena.example/?q=1", "https://arena.example/#fragment",
            "file:///tmp/worker", "https://arena.example.evil/anything",
        ] {
            assert!(coordinator_endpoint(base, "update-reserve").is_err(), "{base}");
        }
        assert!(coordinator_endpoint("https://arena.example/", "install").is_err());
        assert!(!valid_worker_id("invalid"));
        assert!(valid_worker_id("worker_12345678"));
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
