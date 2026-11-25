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
    if !matches!(action, "update-reserve" | "update-release" | "update-hold-status" | "update-pause-poll" | "update-resume-status" | "board-view" | "job-poll" | "job-result" | "ready" | "model-source" | "native-stop-report") {
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

/// Read-only recovery of an uncertain coordinator hold after app restart.
/// Uses the secret in the native keyring; never exposes the capability to JS.
/// Even "absent" is only server evidence, NOT proof the Android installer
/// finished, so it must not trigger automatic release or a new assignment.
pub(crate) async fn inspect_pending_update(machine_token: &str) -> Result<&'static str, String> {
    if machine_token.is_empty() {
        return Err("Brak tokenu maszyny do uzgodnienia rezerwacji".into());
    }
    let pending = crate::android_update_recovery::load_pending()?
        .ok_or("Brak oczekującej rezerwacji".to_string())?;
    if !valid_worker_id(&pending.worker_id) {
        return Err("Nieprawidłowa tożsamość rezerwacji".into());
    }
    let endpoint = coordinator_endpoint(&pending.server, "update-hold-status")?;
    let nonce = random_nonce()?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": pending.worker_id, "token": machine_token,
            "nonce": nonce, "hold_token": pending.hold_token
        }))
        .send().await.map_err(|_| "Nie udało się uzgodnić rezerwacji")?;
    if !response.status().is_success() {
        return Err("Koordynator nie potwierdził stanu rezerwacji".into());
    }
    let body: Value = response.json().await
        .map_err(|_| "Nieprawidłowa odpowiedź koordynatora".to_string())?;
    if body.get("authenticated").and_then(Value::as_bool) != Some(true)
        || body.get("source").and_then(Value::as_str) != Some("coordinator")
        || body.get("worker_id").and_then(Value::as_str) != Some(pending.worker_id.as_str())
        || body.get("nonce").and_then(Value::as_str) != Some(nonce.as_str())
        || body.get("install_allowed").and_then(Value::as_bool) != Some(false) {
        return Err("Niepotwierdzony status rezerwacji".into());
    }
    match body.get("state").and_then(Value::as_str) {
        Some("active") => Ok("active"),
        Some("expired") => Ok("expired"),
        Some("absent") => Ok("absent"),
        _ => Err("Nieznany stan rezerwacji".into()),
    }
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

/// A stop instruction is NOT proof of actual execution suspension.
/// No webview command or automatic install is attached to this poll.
#[derive(Debug, PartialEq, Eq)]
#[allow(dead_code)]
pub(crate) enum StopInstruction {
    None,
    StopCheckpoint {
        job_id: String,
        request_id: String,
        lease_token_sha256: Option<String>,
    },
    AwaitReconciliation,
    Blocked,
}

#[allow(dead_code)]
pub(crate) fn verify_pause_poll_reply(
    response: &Value, worker_id: &str, nonce: &str,
) -> Result<StopInstruction, String> {
    if nonce.len() != 32 || !nonce.bytes().all(|b|
        b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || response.get("authenticated").and_then(Value::as_bool) != Some(true)
        || response.get("source").and_then(Value::as_str) != Some("coordinator")
        || response.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || response.get("nonce").and_then(Value::as_str) != Some(nonce)
        || response.get("install_allowed").and_then(Value::as_bool) != Some(false) {
        return Err("Niewiarygodne polecenie wstrzymania Workera".into());
    }
    let instruction = response.get("instruction")
        .and_then(Value::as_object)
        .ok_or("Brak polecenia koordynatora")?;
    match instruction.get("action").and_then(Value::as_str) {
        Some("none") if instruction.len() == 1 => Ok(StopInstruction::None),
        Some("await_reconciliation") if instruction.len() == 1 =>
            Ok(StopInstruction::AwaitReconciliation),
        Some("blocked") if instruction.len() == 1 => Ok(StopInstruction::Blocked),
        Some("stop_checkpoint") if matches!(instruction.len(), 3 | 4) => {
            let job_id = instruction.get("job_id")
                .and_then(Value::as_str).ok_or("Brak zadania do przerwania")?;
            let request_id = instruction.get("request_id")
                .and_then(Value::as_str).ok_or("Brak identyfikatora pauzy")?;
            if !valid_worker_id(job_id) || request_id.len() != 32
                || !request_id.bytes().all(|b|
                    b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
                return Err("Nieprawidłowe polecenie pauzy".into());
            }
            let lease_token_sha256 = if instruction.len() == 4 {
                Some(instruction.get("lease_token_sha256").and_then(Value::as_str)
                    .filter(|value| valid_lease_token(value))
                    .ok_or("Brak poprawnego skrótu lease w poleceniu STOP")?.to_owned())
            } else {
                None
            };
            Ok(StopInstruction::StopCheckpoint {
                job_id: job_id.to_owned(), request_id: request_id.to_owned(),
                lease_token_sha256,
            })
        }
        _ => Err("Nieznane polecenie zatrzymania Workera".into()),
    }
}

fn valid_lease_token(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b|
        b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Announce a locally active model to Arena without claiming any active job.
/// The response is bound to this Worker's token and a fresh nonce.
#[allow(dead_code)]
pub(crate) async fn announce_phone_ready(
    base: &str, worker_id: &str, machine_token: &str, model_sha256: &str,
) -> Result<&'static str, String> {
    if !valid_worker_id(worker_id) || machine_token.is_empty()
        || model_sha256.len() != 64 || !model_sha256.bytes().all(|b|
            b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return Err("Nieprawidłowa gotowość lokalnego modelu".into());
    }
    let endpoint = coordinator_endpoint(base, "ready")?;
    let nonce = random_nonce()?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce,
            "model_sha256": model_sha256,
        }))
        .send().await.map_err(|_| "Arena jest niedostępna podczas zgłoszenia gotowości")?;
    if !response.status().is_success() {
        return Err("Arena odmówiła przyjęcia gotowości lokalnego Workera".into());
    }
    let value: Value = response.json().await
        .map_err(|_| "Nieprawidłowe potwierdzenie gotowości".to_owned())?;
    if value.get("authenticated").and_then(Value::as_bool) != Some(true)
        || value.get("source").and_then(Value::as_str) != Some("coordinator")
        || value.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || value.get("nonce").and_then(Value::as_str) != Some(nonce.as_str()) {
        return Err("Nieautoryzowana odpowiedź o gotowości Workera".into());
    }
    match value.get("state").and_then(Value::as_str) {
        Some("ready") => Ok("ready"),
        Some("blocked") => Ok("blocked"),
        _ => Err("Nieznana gotowość Workera".into()),
    }
}

/// Transmit a proposed local-model chess move to Arena. A returned ACK means
/// Arena durably RECEIVED it, not that the tournament applied a chess move.
/// No JS command exposes this method until the on-device GGUF engine exists.
#[allow(dead_code)]
pub(crate) async fn submit_my_inference_move(
    base: &str, worker_id: &str, machine_token: &str,
    job_id: &str, uci: &str, lease_token: Option<&str>,
) -> Result<&'static str, String> {
    if !valid_worker_id(worker_id) || !valid_worker_id(job_id)
        || machine_token.is_empty() {
        return Err("Nieprawidłowa tożsamość wyniku inferencji".into());
    }
    if !crate::android_local_llama::parse_exact_uci_answer(uci)
        .is_ok_and(|parsed| parsed == uci) {
        return Err("Nieprawidłowa składnia UCI".into());
    }
    let endpoint = coordinator_endpoint(base, "job-result")?;
    let nonce = random_nonce()?;
    let result = match lease_token {
        Some(lease) if valid_lease_token(lease) =>
            serde_json::json!({"version": 2, "job_id": job_id, "uci": uci, "lease_token": lease}),
        Some(_) => return Err("Nieprawidłowy lease wyniku inferencji".into()),
        None => serde_json::json!({"version": 1, "job_id": job_id, "uci": uci}),
    };
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce,
            "result": result,
        }))
        .send().await.map_err(|_| "Nie udało się przesłać wyniku do Areny")?;
    if !response.status().is_success() {
        return Err("Arena odrzuciła nielegalny lub nieaktualny ruch".into());
    }
    let body: Value = response.json().await
        .map_err(|_| "Nieprawidłowe potwierdzenie wyniku".to_owned())?;
    if body.get("authenticated").and_then(Value::as_bool) != Some(true)
        || body.get("source").and_then(Value::as_str) != Some("coordinator")
        || body.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || body.get("nonce").and_then(Value::as_str) != Some(nonce.as_str())
        || body.get("job_id").and_then(Value::as_str) != Some(job_id) {
        return Err("Nieautoryzowane potwierdzenie wyniku".into());
    }
    match body.get("state").and_then(Value::as_str) {
        Some("received_not_applied") => Ok("received_not_applied"),
        Some("already_received") => Ok("already_received"),
        _ => Err("Nieznany status wyniku".into()),
    }
}

/// Read a pre-assigned inference job. This is transport only: an Android
/// local GGUF runtime must validate model bytes and accept the job before
/// inference can start. JS never receives the machine token or model paths.
#[allow(dead_code)]
pub(crate) async fn fetch_my_inference_job(
    base: &str, worker_id: &str, machine_token: &str,
) -> Result<Value, String> {
    if !valid_worker_id(worker_id) || machine_token.is_empty() {
        return Err("Brak zaufanej tożsamości Workera".into());
    }
    let endpoint = coordinator_endpoint(base, "job-poll")?;
    let nonce = random_nonce()?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce,
        }))
        .send().await.map_err(|_| "Brak połączenia z kolejką Areny")?;
    if !response.status().is_success() {
        return Err("Arena odmówiła odczytu zadania".into());
    }
    let body: Value = response.json().await
        .map_err(|_| "Nieprawidłowa odpowiedź zadania".to_owned())?;
    if body.get("authenticated").and_then(Value::as_bool) != Some(true)
        || body.get("source").and_then(Value::as_str) != Some("coordinator")
        || body.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || body.get("nonce").and_then(Value::as_str) != Some(nonce.as_str()) {
        return Err("Odpowiedź zadania nie należy do tego telefonu".into());
    }
    match body.get("state").and_then(Value::as_str) {
        Some("none" | "blocked" | "expired" | "awaiting_arena") => {
            if body.get("job").is_some() {
                return Err("Nieaktywna kolejka ujawnia obce zadanie".into());
            }
            Ok(serde_json::json!({
                "state": body.get("state").and_then(Value::as_str).unwrap_or("blocked")
            }))
        }
        Some("assigned") => {
            let job = body.get("job").and_then(Value::as_object)
                .ok_or("Brak zatwierdzonego zadania".to_owned())?;
            let version = job.get("version").and_then(Value::as_u64)
                .ok_or("Brak wersji protokołu".to_owned())?;
            if !matches!((version, job.len()), (1, 6) | (2, 7) | (3, 8))
                || (version == 3 && !job.get("lease_token").and_then(Value::as_str)
                    .is_some_and(valid_lease_token)) {
                return Err("Niezgodny format zadania".into());
            }
            let job_id = job.get("job_id").and_then(Value::as_str)
                .ok_or("Brak ID zadania".to_owned())?;
            let fen = job.get("fen").and_then(Value::as_str)
                .ok_or("Brak FEN".to_owned())?;
            let digest = job.get("model_sha256").and_then(Value::as_str)
                .ok_or("Brak SHA-256 modelu".to_owned())?;
            let max_tokens = job.get("max_tokens").and_then(Value::as_u64)
                .ok_or("Brak limitu tokenów".to_owned())?;
            let timeout = job.get("timeout_seconds").and_then(Value::as_u64)
                .ok_or("Brak limitu czasu".to_owned())?;
            if !valid_worker_id(job_id)
                || fen.len() > 120 || fen.split(' ').count() != 6
                || !fen.bytes().all(|b| b.is_ascii_alphanumeric()
                    || matches!(b, b'/' | b' ' | b'-'))
                || digest.len() != 64 || !digest.bytes().all(|b|
                    b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                || !(1..=1024).contains(&max_tokens)
                || !(1..=600).contains(&timeout) {
                return Err("Arena przysłała nieprawidłowe zadanie".into());
            }
            if matches!(version, 2 | 3) {
                let legal = job.get("legal_moves").and_then(Value::as_array)
                    .ok_or("Arena nie podała legalnych ruchów")?;
                if legal.is_empty() || legal.len() > 256 {
                    return Err("Nieprawidłowa liczba ruchów z Areny".into());
                }
                let mut previous: Option<&str> = None;
                for item in legal {
                    let uci = item.as_str()
                        .ok_or("Nieprawidłowy typ legalnego ruchu")?;
                    let b = uci.as_bytes();
                    if !(b.len() == 4 || b.len() == 5)
                        || !(b'a'..=b'h').contains(&b[0])
                        || !(b'1'..=b'8').contains(&b[1])
                        || !(b'a'..=b'h').contains(&b[2])
                        || !(b'1'..=b'8').contains(&b[3])
                        || (b.len() == 5
                            && !matches!(b[4], b'q' | b'r' | b'b' | b'n'))
                        || previous.is_some_and(|p| p >= uci) {
                        return Err("Nieprawidłowa lista ruchów z Areny".into());
                    }
                    previous = Some(uci);
                }
            }
            Ok(serde_json::json!({
                "state": "assigned", "job": job,
            }))
        }
        _ => Err("Nieznany stan zadania".into()),
    }
}

/// Pure validation of the machine-authenticated, metadata-only response.
/// This does NOT authorize downloading weights; source and SHA are checked
/// independently against the assigned job and pinned HF registry entry.
pub(crate) fn verify_assigned_model_source_reply(
    body: &Value, worker_id: &str, nonce: &str, assigned_digest: &str,
) -> Result<crate::android_model_source::ApprovedGguf, String> {
    if nonce.len() != 32 || !nonce.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || body.get("authenticated").and_then(Value::as_bool) != Some(true)
        || body.get("source").and_then(Value::as_str) != Some("coordinator")
        || body.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || body.get("nonce").and_then(Value::as_str) != Some(nonce)
        || body.get("state").and_then(Value::as_str) != Some("approved_metadata_only") {
        return Err("Niezaufana odpowiedź rejestru GGUF".into());
    }
    let model = body.get("model").and_then(Value::as_object)
        .ok_or("Brak zatwierdzonych metadanych modelu")?;
    if model.len() != 5
        || !["model_id", "version_id", "sha256", "size_bytes", "source_url"]
            .iter().all(|field| model.contains_key(*field))
        || model.get("model_id").and_then(Value::as_u64).filter(|v| *v > 0).is_none()
        || model.get("version_id").and_then(Value::as_u64).filter(|v| *v > 0).is_none() {
        return Err("Nieprawidłowy format wpisu GGUF".into());
    }
    let approved = crate::android_model_source::ApprovedGguf {
        sha256: model.get("sha256").and_then(Value::as_str)
            .ok_or("Brak SHA-256 wpisu GGUF")?.to_owned(),
        size_bytes: model.get("size_bytes").and_then(Value::as_u64)
            .ok_or("Brak rozmiaru GGUF")?,
        source_url: model.get("source_url").and_then(Value::as_str)
            .ok_or("Brak adresu źródła GGUF")?.to_owned(),
    };
    crate::android_model_source::bind_approved_model_to_job(assigned_digest, &approved)?;
    Ok(approved)
}

/// Obtain a registry entry ONLY for the currently authenticated, leased job.
/// A before/after poll detects revoked or changed assignments. This transport
/// does not invoke the Android downloader or return any secret to JavaScript.
#[allow(dead_code)]
pub(crate) async fn fetch_my_approved_gguf(
    base: &str, worker_id: &str, machine_token: &str,
    expected_job: &Value,
) -> Result<crate::android_model_source::ApprovedGguf, String> {
    if !valid_worker_id(worker_id) || machine_token.is_empty() {
        return Err("Brak zaufanej tożsamości Workera".into());
    }
    let previous = fetch_my_inference_job(base, worker_id, machine_token).await?;
    let assigned_digest = previous.get("job")
        .filter(|_| previous.get("state").and_then(Value::as_str) == Some("assigned"))
        .filter(|job| *job == expected_job)
        .and_then(|job| job.get("model_sha256")).and_then(Value::as_str)
        .ok_or("Brak aktywnego przydziału modelu")?;
    let nonce = random_nonce()?;
    let endpoint = coordinator_endpoint(base, "model-source")?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce
        }))
        .send().await.map_err(|_| "Brak połączenia z rejestrem Areny")?;
    if !response.status().is_success() {
        return Err("Arena odmówiła udostępnienia metadanych modelu".into());
    }
    let body: Value = response.json().await
        .map_err(|_| "Nieprawidłowa odpowiedź rejestru GGUF".to_owned())?;
    let approved = verify_assigned_model_source_reply(
        &body, worker_id, &nonce, assigned_digest)?;
    let current = fetch_my_inference_job(base, worker_id, machine_token).await?;
    if current != previous || current.get("state").and_then(Value::as_str) != Some("assigned") {
        return Err("Przydział modelu zmienił się podczas odczytu rejestru".into());
    }
    Ok(approved)
}

/// The Arena owns chess state. Return the FEN only when the authenticated
/// coordinator explicitly binds it to THIS worker's active job. A general
/// /api/live snapshot is deliberately not accepted.
#[allow(dead_code)]
pub(crate) async fn fetch_worker_board_view(
    base: &str, worker_id: &str, machine_token: &str,
) -> Result<Value, String> {
    if !valid_worker_id(worker_id) || machine_token.is_empty() {
        return Err("Brak zaufanej tożsamości Workera".into());
    }
    let endpoint = coordinator_endpoint(base, "board-view")?;
    let nonce = random_nonce()?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce
        }))
        .send().await.map_err(|_| "Brak połączenia z podglądem Areny")?;
    if !response.status().is_success() {
        return Err("Arena odmówiła podglądu partii".into());
    }
    let body: Value = response.json().await
        .map_err(|_| "Nieprawidłowy podgląd partii".to_owned())?;
    if body.get("authenticated").and_then(Value::as_bool) != Some(true)
        || body.get("source").and_then(Value::as_str) != Some("coordinator")
        || body.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || body.get("nonce").and_then(Value::as_str) != Some(nonce.as_str()) {
        return Err("Odpowiedź podglądu nie należy do Workera".into());
    }
    match body.get("active").and_then(Value::as_bool) {
        Some(false) => {
            // Never forward an unexpected FEN or job when no job is active.
            if body.get("fen").is_some() || body.get("job_id").is_some() {
                return Err("Nieaktywny podgląd zawiera cudzą partię".into());
            }
            Ok(serde_json::json!({"active": false, "state": "inactive"}))
        }
        Some(true) => {
            let fen = body.get("fen").and_then(Value::as_str)
                .ok_or("Brak pozycji aktywnej partii")?;
            let job = body.get("job_id").and_then(Value::as_str)
                .ok_or("Brak identyfikatora aktywnej partii")?;
            if !valid_worker_id(job) || fen.len() > 120 || fen.split(' ').count() != 6
                || !fen.bytes().all(|b| b.is_ascii_alphanumeric()
                    || matches!(b, b'/' | b' ' | b'-')) {
                return Err("Nieprawidłowe dane aktywnej partii".into());
            }
            let str_field = |key: &str| -> String {
                body.get(key).and_then(Value::as_str).unwrap_or("")
                    .chars().take(96).collect()
            };
            let last = str_field("last_move_uci");
            let last = if (4..=5).contains(&last.len()) && last.bytes().all(|b|
                b.is_ascii_lowercase() || (b'1'..=b'8').contains(&b)) {
                last
            } else { String::new() };
            Ok(serde_json::json!({
                "active": true, "fen": fen, "job_id": job,
                "white": str_field("white"), "black": str_field("black"),
                "model": str_field("model"), "stage": str_field("stage"),
                "last_move_uci": last,
            }))
        }
        _ => Err("Nieprawidłowy status partii Workera".into()),
    }
}

/// Read-only resume readiness. A checkpoint on the coordinator is not proof
/// that this Android app has accepted or started inference. All network
/// metadata is validated against the machine token + nonce.
#[allow(dead_code)]
pub(crate) async fn fetch_resume_status(
    base: &str, worker_id: &str, machine_token: &str,
) -> Result<&'static str, String> {
    if !valid_worker_id(worker_id) || machine_token.is_empty() {
        return Err("Brak tożsamości Workera do wznowienia".into());
    }
    let endpoint = coordinator_endpoint(base, "update-resume-status")?;
    let nonce = random_nonce()?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce,
        }))
        .send().await.map_err(|_| "Brak odpowiedzi koordynatora wznowienia")?;
    if !response.status().is_success() {
        return Err("Koordynator odmówił sprawdzenia wznowienia".into());
    }
    let value: Value = response.json().await
        .map_err(|_| "Nieprawidłowa odpowiedź wznowienia".to_owned())?;
    if value.get("authenticated").and_then(Value::as_bool) != Some(true)
        || value.get("source").and_then(Value::as_str) != Some("coordinator")
        || value.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || value.get("nonce").and_then(Value::as_str) != Some(nonce.as_str())
        || value.get("resume_available").and_then(Value::as_bool) != Some(false) {
        return Err("Niepotwierdzona gotowość Workera do wznowienia".into());
    }
    match value.get("state").and_then(Value::as_str) {
        Some("no_checkpoint") => Ok("no_checkpoint"),
        Some("waiting_update_reconciliation") => Ok("waiting_update_reconciliation"),
        Some("blocked") => Ok("blocked"),
        Some("awaiting_local_engine") => Ok("awaiting_local_engine"),
        _ => Err("Nieznany stan gotowości do wznowienia".into()),
    }
}

/// Retry an ambiguous delivery with the same STOP identity. Each HTTP attempt
/// builds its own nonce. Definitive rejection and invalid receipts stop immediately.
#[cfg(target_os = "android")]
async fn deliver_stop_report<F, Fut>(mut attempt: F) -> Result<(), String>
where
    F: FnMut() -> Fut,
    Fut: std::future::Future<Output = Result<Option<()>, String>>,
{
    for index in 0..3 {
        if index > 0 {
            tokio::time::sleep(std::time::Duration::from_millis(250 * index)).await;
        }
        if attempt().await?.is_some() {
            return Ok(());
        }
    }
    Err("Silnik zatrzymany. Brak potwierdzenia odbioru raportu STOP przez Arenę".into())
}

#[cfg(target_os = "android")]
pub(crate) async fn report_native_stop(
    base: &str, worker_id: &str, machine_token: &str,
    job_id: &str, request_id: &str, lease_token: &str,
) -> Result<(), String> {
    let endpoint = coordinator_endpoint(base, "native-stop-report")?;
    let client = update_http_client()?;
    deliver_stop_report(|| async {
        let nonce = random_nonce()?;
        let reply = client.post(endpoint.clone()).json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce,
            "job_id": job_id, "request_id": request_id, "lease_token": lease_token,
        })).send().await;
        let Ok(reply) = reply else { return Ok(None); };
        if reply.status().is_server_error() {
            return Ok(None);
        }
        if !reply.status().is_success() {
            return Err("Silnik zatrzymany. Arena odrzuciła raport STOP".into());
        }
        let Ok(bytes) = reply.bytes().await else { return Ok(None); };
        let body: Value = serde_json::from_slice(&bytes)
            .map_err(|_| "Nieprawidłowe potwierdzenie raportu STOP")?;
        if body["authenticated"] != true || body["source"] != "coordinator"
            || body["worker_id"] != worker_id || body["nonce"] != nonce
            || body["state"] != "stop_report_received" || body["install_allowed"] != false {
            return Err("Niepotwierdzony odbiór raportu STOP".into());
        }
        Ok(Some(()))
    }).await
}

/// Future native executor may poll while inference is active, then stop at a
/// safe checkpoint and transmit a *separately verified* acknowledgment.
/// This function does not stop execution or claim the worker is idle.
#[allow(dead_code)]
pub(crate) async fn poll_pause_instruction(
    base: &str, worker_id: &str, machine_token: &str,
) -> Result<StopInstruction, String> {
    if !valid_worker_id(worker_id) || machine_token.is_empty() {
        return Err("Brak zaufanej tożsamości Workera".into());
    }
    let endpoint = coordinator_endpoint(base, "update-pause-poll")?;
    let nonce = random_nonce()?;
    let reply = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce
        }))
        .send().await.map_err(|_| "Nie można pobrać polecenia pauzy")?;
    if !reply.status().is_success() {
        return Err("Koordynator nie potwierdził polecenia pauzy".into());
    }
    let body: Value = reply.json().await
        .map_err(|_| "Nieprawidłowa odpowiedź koordynatora".to_string())?;
    verify_pause_poll_reply(&body, worker_id, &nonce)
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
        assert!(coordinator_endpoint("https://arena.example/", "update-hold-status").is_ok());
        assert!(coordinator_endpoint("https://arena.example/", "install").is_err());
        assert!(!valid_worker_id("invalid"));
        assert!(valid_worker_id("worker_12345678"));
    }

    #[test]
    fn stop_poll_requires_exact_authenticated_instruction_and_nonce() {
        let reply = json!({
            "authenticated": true, "source": "coordinator",
            "worker_id": "worker_12345678", "nonce": NONCE,
            "install_allowed": false,
            "instruction": {
                "action": "stop_checkpoint",
                "job_id": "job_12345678", "request_id": "a".repeat(32)
            },
        });
        assert_eq!(
            verify_pause_poll_reply(&reply, "worker_12345678", NONCE).unwrap(),
            StopInstruction::StopCheckpoint {
                job_id: "job_12345678".into(), request_id: "a".repeat(32),
                lease_token_sha256: None,
            }
        );
        let mut fenced = reply.clone();
        fenced["instruction"]["lease_token_sha256"] = json!("b".repeat(64));
        assert_eq!(
            verify_pause_poll_reply(&fenced, "worker_12345678", NONCE).unwrap(),
            StopInstruction::StopCheckpoint {
                job_id: "job_12345678".into(), request_id: "a".repeat(32),
                lease_token_sha256: Some("b".repeat(64)),
            },
        );
        fenced["instruction"]["lease_token_sha256"] = json!("not-a-digest");
        assert!(verify_pause_poll_reply(&fenced, "worker_12345678", NONCE).is_err());
        for (key, forged) in [
            ("authenticated", json!(false)),
            ("source", json!("worker")),
            ("worker_id", json!("worker_87654321")),
            ("nonce", json!("f".repeat(32))),
            ("install_allowed", json!(true)),
        ] {
            let mut r = reply.clone();
            r[key] = forged;
            assert!(verify_pause_poll_reply(&r, "worker_12345678", NONCE).is_err());
        }
        for forged in [
            json!({"action": "stop_checkpoint", "job_id": "invalid",
                   "request_id": "a".repeat(32)}),
            json!({"action": "stop_checkpoint", "job_id": "job_12345678",
                   "request_id": "not-a-request"}),
            json!({"action": "resume", "job_id": "job_12345678"}),
            json!({"action": "none", "hold_token": "secret"}),
        ] {
            let mut r = reply.clone();
            r["instruction"] = forged;
            assert!(verify_pause_poll_reply(&r, "worker_12345678", NONCE).is_err());
        }
        for action in ["none", "await_reconciliation", "blocked"] {
            let mut r = reply.clone();
            r["instruction"] = json!({"action": action});
            assert!(verify_pause_poll_reply(&r, "worker_12345678", NONCE).is_ok());
        }
        assert!(coordinator_endpoint("https://arena.example/", "update-pause-poll").is_ok());
    }

    #[test]
    fn approved_model_response_requires_matching_assignment_and_nonce() {
        let digest = "a".repeat(64);
        let nonce = "b".repeat(32);
        let good = serde_json::json!({
            "authenticated": true, "source": "coordinator",
            "worker_id": "worker_12345678", "nonce": nonce,
            "state": "approved_metadata_only",
            "model": {
                "model_id": 1, "version_id": 2,
                "sha256": digest, "size_bytes": 4096,
                "source_url": format!(
                    "https://huggingface.co/owner/repo/resolve/{}/model.Q4.gguf",
                    "1".repeat(40))
            }
        });
        assert!(verify_assigned_model_source_reply(
            &good, "worker_12345678", &nonce, &digest).is_ok());
        for (field, replacement) in [
            ("authenticated", serde_json::json!(false)),
            ("nonce", serde_json::json!("c".repeat(32))),
            ("state", serde_json::json!("ready")),
            ("worker_id", serde_json::json!("worker_other999")),
        ] {
            let mut forged = good.clone();
            forged[field] = replacement;
            assert!(verify_assigned_model_source_reply(
                &forged, "worker_12345678", &nonce, &digest).is_err());
        }
        for (field, replacement) in [
            ("sha256", serde_json::json!("f".repeat(64))),
            ("size_bytes", serde_json::json!(0)),
            ("model_id", serde_json::json!(0)),
            ("source_url", serde_json::json!("https://evil.test/file.gguf")),
        ] {
            let mut forged = good.clone();
            forged["model"][field] = replacement;
            assert!(verify_assigned_model_source_reply(
                &forged, "worker_12345678", &nonce, &digest).is_err());
        }
        assert!(coordinator_endpoint("https://arena.example/", "model-source").is_ok());
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
