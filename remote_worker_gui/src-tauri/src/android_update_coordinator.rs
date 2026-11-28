//! Fail-closed coordinator idle evidence for Android Worker update checks.
//! An authenticated HTTPS response must echo a per-request OS-random nonce,
//! identify the Worker, report zero tasks and carry a recent server timestamp.
//! This is only a read-only safety check, NOT permission to install.

use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
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
    if !matches!(action, "update-reserve" | "update-release" | "update-hold-status" | "update-pause-poll" | "update-resume-status" | "board-view" | "job-poll" | "job-result" | "ready" | "model-source" | "native-stop-report" | "native-stop-recovery" | "journal-init-acquire" | "journal-init-complete") {
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

#[cfg(any(target_os = "android", all(test, unix)))]
pub(crate) fn stop_recovery_origin(base: &str) -> Result<String, String> {
    Ok(coordinator_endpoint(base, "native-stop-recovery")?.origin().ascii_serialization())
}

#[cfg(any(target_os = "android", all(test, unix)))]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StopRecoveryReply {
    authenticated: bool,
    source: String,
    worker_id: String,
    nonce: String,
    job_id: String,
    request_id: String,
    state: String,
    install_allowed: bool,
    dispatch_allowed: bool,
}
#[cfg(any(target_os = "android", all(test, unix)))]
impl StopRecoveryReply {
    fn matches(&self, worker: &str, nonce: &str, job: &str, request: &str) -> bool {
        self.authenticated && self.source == "coordinator" && self.worker_id == worker
            && self.nonce == nonce && self.job_id == job && self.request_id == request
            && self.state == "stop_recovery_validated" && !self.install_allowed && !self.dispatch_allowed
    }
}
#[cfg(any(target_os = "android", all(test, unix)))]
pub(crate) async fn validate_pending_stop(
    base: &str, worker: &str, token: &str, report: &crate::android_native_stop_outbox::StopReport,
) -> Result<(), String> {
    let (job, request, lease) = report.recovery_identity(base, worker)
        .map_err(|_| "Niezgodna tożsamość raportu STOP")?;
    let nonce = random_nonce()?;
    let endpoint = coordinator_endpoint(base, "native-stop-recovery")?;
    validate_pending_stop_at(endpoint, worker, token, job, request, lease, &nonce, update_http_client()?).await
}

#[cfg(any(target_os = "android", all(test, unix)))]
async fn validate_pending_stop_at(
    endpoint: reqwest::Url, worker: &str, token: &str, job: &str, request: &str,
    lease: &str, nonce: &str, client: reqwest::Client,
) -> Result<(), String> {
    let response = client.post(endpoint)
        .json(&serde_json::json!({"worker_id":worker,"token":token,"nonce":nonce,
            "job_id":job,"request_id":request,"lease_token":lease}))
        .send().await.map_err(|_| "Koordynator STOP niedostępny")?;
    if response.status() != reqwest::StatusCode::OK { return Err("Koordynator odrzucił recovery STOP".into()); }
    let reply: StopRecoveryReply = crate::bounded_json_response(response, 4096).await
        .map_err(|_| "Nieprawidłowa odpowiedź recovery STOP")?;
    if !reply.matches(worker, nonce, job, request) { return Err("Niezgodna odpowiedź recovery STOP".into()); }
    Ok(())
}
#[cfg(any(target_os = "android", all(test, unix)))]
pub(crate) async fn pending_stop_dispatch_gate<F: std::future::Future<Output = Result<(), String>>>(
    report: Option<crate::android_native_stop_outbox::StopReport>,
    validate: impl FnOnce(crate::android_native_stop_outbox::StopReport) -> F,
) -> Result<(), String> {
    match report {
        None => Ok(()),
        Some(report) => {
            validate(report).await?;
            Err("Raport STOP oczekuje na dostarczenie i uzgodnienie".into())
        }
    }
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

/// Native proof that this coordinator fenced this Worker for this token.
#[allow(dead_code)]
pub(crate) struct JournalInitAuthorization {
    server: reqwest::Url,
    worker_id: String,
    token_digest: [u8; 32],
    nonce: String,
    checked_at_unix_ms: u64,
    baseline_version: Option<u64>,
}

#[allow(dead_code)]
impl JournalInitAuthorization {
    pub(crate) fn matches(
        &self, base: &str, worker_id: &str, machine_token: &str, now_ms: u64,
    ) -> bool {
        let token_digest: [u8; 32] = Sha256::digest(machine_token.as_bytes()).into();
        coordinator_endpoint(base, "journal-init-acquire").as_ref() == Ok(&self.server)
            && self.worker_id == worker_id
            && self.token_digest == token_digest
            && journal_init_time_current(self.checked_at_unix_ms, now_ms)
    }

    pub(crate) fn baseline_version(&self) -> Option<u64> {
        self.baseline_version
    }

    pub(crate) fn nonce(&self) -> &str {
        &self.nonce
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct JournalInitReply {
    contract_version: u64,
    authenticated: bool,
    source: String,
    worker_id: String,
    nonce: String,
    fenced: bool,
    checked_at_unix_ms: u64,
    install_allowed: bool,
    #[serde(default)]
    baseline_version: Option<u64>,
}

fn journal_init_time_current(checked_at: u64, now_ms: u64) -> bool {
    now_ms.saturating_sub(checked_at) <= MAX_IDLE_AGE_MS
        && checked_at.saturating_sub(now_ms) <= MAX_FUTURE_SKEW_MS
}

impl JournalInitReply {
    fn verify(
        self, server: reqwest::Url, worker_id: &str, machine_token: &str,
        nonce: String, started_ms: u64, now_ms: u64, baseline_version: Option<u64>,
    ) -> Result<JournalInitAuthorization, String> {
        if self.baseline_version != baseline_version || self.contract_version != 1 || !self.authenticated || self.source != "coordinator"
            || self.worker_id != worker_id || self.nonce != nonce
            || !self.fenced || self.install_allowed || now_ms < started_ms
            || self.checked_at_unix_ms < started_ms.saturating_sub(MAX_FUTURE_SKEW_MS)
            || !journal_init_time_current(self.checked_at_unix_ms, now_ms)
        {
            return Err("Koordynator nie potwierdził blokady inicjalizacji".into());
        }
        Ok(JournalInitAuthorization {
            server,
            worker_id: self.worker_id,
            token_digest: Sha256::digest(machine_token.as_bytes()).into(),
            nonce,
            checked_at_unix_ms: self.checked_at_unix_ms,
            baseline_version: self.baseline_version,
        })
    }
}

#[allow(dead_code)]
pub(crate) async fn acquire_journal_init(
    base: &str, worker_id: &str, machine_token: &str,
) -> Result<JournalInitAuthorization, String> {
    request_journal_init(base, worker_id, machine_token, random_nonce()?, false, None).await
}

pub(crate) async fn request_journal_init(
    base: &str, worker_id: &str, machine_token: &str, nonce: String, retry: bool, baseline_version: Option<u64>,
) -> Result<JournalInitAuthorization, String> {
    if !valid_worker_id(worker_id)
        || !(24..=512).contains(&machine_token.len())
        || !machine_token.is_ascii()
    {
        return Err("Brak zaufanej tożsamości Workera".into());
    }
    let endpoint = coordinator_endpoint(base, "journal-init-acquire")?;
    let started_ms = system_unix_ms()?;
    let mut payload = serde_json::json!({
        "worker_id": worker_id, "token": machine_token, "nonce": nonce
    });
    if retry {
        payload["retry"] = Value::Bool(true);
    }
    if let Some(version) = baseline_version {
        payload["baseline_version"] = version.into();
    }
    let response = update_http_client()?.post(endpoint.clone())
        .json(&payload)
        .send().await.map_err(|_| "Brak odpowiedzi koordynatora inicjalizacji")?;
    if response.status() != reqwest::StatusCode::OK {
        return Err("Koordynator odmówił blokady inicjalizacji".into());
    }
    let reply: JournalInitReply = crate::bounded_json_response(response, 4096).await?;
    reply.verify(endpoint, worker_id, machine_token, nonce, started_ms, system_unix_ms()?, baseline_version)
}

#[cfg(target_os = "android")]
#[allow(dead_code)]
pub(crate) async fn complete_journal_init(
    base: &str, worker_id: &str,
) -> Result<(), String> {
    let endpoint = coordinator_endpoint(base, "journal-init-complete")?;
    let token = crate::token_entry()?.get_password()?;
    // This call holds the credential and journal gates only for local I/O.
    let completion = crate::credential_journal::completion(base, worker_id, &token)?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": completion.worker_id, "token": token, "nonce": completion.nonce,
            "pair_marker": completion.pair_marker, "origin": completion.origin,
        }))
        .send().await.map_err(|_| "Brak potwierdzenia koordynatora inicjalizacji")?;
    if response.status() != reqwest::StatusCode::OK {
        return Err("Koordynator odmówił zatwierdzenia inicjalizacji".into());
    }
    let reply: Value = crate::bounded_json_response(response, 4096).await?;
    if reply != serde_json::json!({
        "contract_version": 1, "authenticated": true, "source": "coordinator",
        "worker_id": worker_id, "nonce": completion.nonce, "completed": true,
        "state": "unknown", "install_allowed": false,
    }) {
        return Err("Nieprawidłowe potwierdzenie inicjalizacji".into());
    }
    Ok(())
}

#[cfg(any(target_os = "android", test))]
struct JournalReconciliation;

#[cfg(any(target_os = "android", test))]
static RECONCILING_JOURNALS: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

#[cfg(any(target_os = "android", test))]
impl JournalReconciliation {
    fn start() -> Result<Self, String> {
        use std::sync::atomic::Ordering;
        RECONCILING_JOURNALS.compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
            .map(|_| Self).map_err(|_| "Trwa już uzgadnianie dzienników".into())
    }
}

#[cfg(any(target_os = "android", test))]
impl Drop for JournalReconciliation {
    fn drop(&mut self) {
        RECONCILING_JOURNALS.store(false, std::sync::atomic::Ordering::Release);
    }
}

/// Restart-safe driver for a native package observation and a coordinator
/// baseline approval. The WebView supplies only the connection identity.
#[cfg(target_os = "android")]
#[allow(dead_code)]
pub(crate) async fn reconcile_journal_initialization(
    base: &str, worker_id: &str,
    expectation: &crate::credential_journal::JournalBaseline,
) -> Result<(), String> {
    let _operation = JournalReconciliation::start()?;
    coordinator_endpoint(base, "journal-init-acquire")?;
    if !valid_worker_id(worker_id) { return Err("Nieprawidłowy identyfikator Workera".into()); }
    let (token, nonce, committed) =
        crate::credential_journal::prepare_reconciliation(base, worker_id, expectation)?;
    if !committed {
        let authorization = request_journal_init(base, worker_id, &token, nonce, true, Some(expectation.version())).await?;
        crate::credential_journal::initialize_authorized_pair(base, worker_id, &authorization, expectation)?;
    }
    complete_journal_init(base, worker_id).await?;
    crate::credential_journal::finish_reconciliation(base, worker_id, &token)
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
    {
        let _credential_guard = crate::credential_change_gate()?;
        if crate::token_entry()?.get_password()? != machine_token {
            return Err("Token Workera zmienił się".into());
        }
        if crate::android_update_install_journal::load()?.is_some() {
            return Err("Nierozliczona instalacja".into());
        }
        crate::android_update_recovery::save_pending(&pending)?;
    }
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
    let body: Value = crate::bounded_json_response(response, 4096).await.map_err(|_|
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
    let body: Value = crate::bounded_json_response(response, 4096).await
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
    let body: Value = crate::bounded_json_response(response, 4096).await.map_err(|_|
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
    StopCheckpoint { job_id: String, request_id: String },
    NativeStopCheckpoint { job_id: String, request_id: String, lease_token_sha256: String },
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
            if instruction.len() == 4 {
                let digest = instruction.get("lease_token_sha256").and_then(Value::as_str)
                    .filter(|s| crate::android_model_cache::valid_digest(s))
                    .ok_or("Nieprawidłowy skrót dzierżawy STOP")?;
                return Ok(StopInstruction::NativeStopCheckpoint {
                    job_id: job_id.to_owned(), request_id: request_id.to_owned(),
                    lease_token_sha256: digest.to_owned(),
                });
            }
            Ok(StopInstruction::StopCheckpoint {
                job_id: job_id.to_owned(), request_id: request_id.to_owned(),
            })
        }
        _ => Err("Nieznane polecenie zatrzymania Workera".into()),
    }
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
            "model_sha256": model_sha256, "protocol_version": 3,
        }))
        .send().await.map_err(|_| "Arena jest niedostępna podczas zgłoszenia gotowości")?;
    if !response.status().is_success() {
        return Err("Arena odmówiła przyjęcia gotowości lokalnego Workera".into());
    }
    let value: Value = crate::bounded_json_response(response, 4096).await
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
    job_id: &str, uci: &str,
) -> Result<&'static str, String> {
    if !valid_worker_id(worker_id) || !valid_worker_id(job_id)
        || machine_token.is_empty() {
        return Err("Nieprawidłowa tożsamość wyniku inferencji".into());
    }
    if !(4..=5).contains(&uci.len())
        || !uci.is_ascii()
        || !uci.bytes().all(|b|
            matches!(b, b'a'..=b'h' | b'1'..=b'8' | b'q' | b'r' | b'b' | b'n')) {
        return Err("Nieprawidłowa składnia UCI".into());
    }
    let endpoint = coordinator_endpoint(base, "job-result")?;
    let nonce = random_nonce()?;
    let response = update_http_client()?.post(endpoint)
        .json(&serde_json::json!({
            "worker_id": worker_id, "token": machine_token, "nonce": nonce,
            "result": {"version": 1, "job_id": job_id, "uci": uci},
        }))
        .send().await.map_err(|_| "Nie udało się przesłać wyniku do Areny")?;
    if !response.status().is_success() {
        return Err("Arena odrzuciła nielegalny lub nieaktualny ruch".into());
    }
    let body: Value = crate::bounded_json_response(response, 4096).await
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
    let reply = fetch_native_inference_job(base, worker_id, machine_token).await?;
    if reply.get("job").and_then(|j| j.get("version")).and_then(Value::as_u64) == Some(4) {
        return Ok(serde_json::json!({"state": "blocked"}));
    }
    Ok(reply)
}

async fn fetch_native_inference_job(
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
    let body: Value = crate::bounded_json_response(response, 32 * 1024).await
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
            if !matches!((version, job.len()), (1, 6) | (2, 7) | (3, 8) | (4, 9)) {
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
            if version >= 3 && !matches!(job.get("variant").and_then(Value::as_str), Some("standard" | "elimination")) {
                return Err("Nieznany wariant szachów".into());
            }
            if version == 4 && !job.get("lease_token").and_then(Value::as_str)
                .is_some_and(crate::android_model_cache::valid_digest) {
                return Err("Nieprawidłowy token dzierżawy v4".into());
            }
            if version >= 2 {
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

#[allow(dead_code)]
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
    let previous = fetch_native_inference_job(base, worker_id, machine_token).await?;
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
    let body: Value = crate::bounded_json_response(response, 4096).await
        .map_err(|_| "Nieprawidłowa odpowiedź rejestru GGUF".to_owned())?;
    let approved = verify_assigned_model_source_reply(
        &body, worker_id, &nonce, assigned_digest)?;
    let current = fetch_native_inference_job(base, worker_id, machine_token).await?;
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
    let body: Value = crate::bounded_json_response(response, 4096).await
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
    let value: Value = crate::bounded_json_response(response, 4096).await
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
    let body: Value = crate::bounded_json_response(reply, 4096).await
        .map_err(|_| "Nieprawidłowa odpowiedź koordynatora".to_string())?;
    verify_pause_poll_reply(&body, worker_id, &nonce)
}

/// Match a native STOP to the exact execution generation. No token reaches JS.
fn native_stop_binding(instruction: &StopInstruction, job: &Value) -> Result<(String, String, String), String> {
    use sha2::{Digest, Sha256};
    let StopInstruction::NativeStopCheckpoint { job_id, request_id, lease_token_sha256 } = instruction else {
        return Err("Brak powiązanego STOP v4".into());
    };
    let token = job.get("lease_token").and_then(Value::as_str)
        .filter(|s| crate::android_model_cache::valid_digest(s)).ok_or("Brak dzierżawy v4")?;
    if job.get("version").and_then(Value::as_u64) != Some(4)
        || job.get("job_id").and_then(Value::as_str) != Some(job_id.as_str())
        || format!("{:x}", Sha256::digest(token.as_bytes())) != *lease_token_sha256 {
        return Err("STOP nie należy do tej dzierżawy".into());
    }
    Ok((job_id.clone(), request_id.clone(), token.to_owned()))
}

/// Internal native-only report. Caller must hold the actual executor ownership
/// fence and prove its threads/model have been stopped and joined; a local
/// quiescence check alone cannot establish that ownership. No caller wired.
#[cfg(target_os = "android")]
#[allow(dead_code)]
pub(crate) async fn report_native_stop(
    base: &str, worker_id: &str, machine_token: &str, job: &Value,
    prove_joined_executor: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    if !valid_worker_id(worker_id) || machine_token.is_empty() {
        return Err("Brak tożsamości Workera".into());
    }
    let instruction = poll_pause_instruction(base, worker_id, machine_token).await?;
    let (job_id, request_id, lease_token) = native_stop_binding(&instruction, job)?;
    prove_joined_executor()?;
    let _gate = crate::android_model_runtime_guard::begin_exclusive_update()?;
    if !crate::android_llama_runtime::embedded_inference_quiesced()? {
        return Err("Silnik nadal pracuje".into());
    }
    if poll_pause_instruction(base, worker_id, machine_token).await? != instruction {
        return Err("Polecenie STOP uległo zmianie".into());
    }
    let nonce = random_nonce()?;
    let response = update_http_client()?.post(coordinator_endpoint(base, "native-stop-report")?)
        .json(&serde_json::json!({"worker_id": worker_id, "token": machine_token,
            "nonce": nonce, "job_id": job_id, "request_id": request_id, "lease_token": lease_token}))
        .send().await.map_err(|_| "Nie można przesłać raportu STOP")?;
    if !response.status().is_success() { return Err("Arena odrzuciła raport STOP".into()); }
    let body: Value = crate::bounded_json_response(response, 4096).await?;
    if body.get("authenticated").and_then(Value::as_bool) != Some(true)
        || body.get("source").and_then(Value::as_str) != Some("coordinator")
        || body.get("worker_id").and_then(Value::as_str) != Some(worker_id)
        || body.get("nonce").and_then(Value::as_str) != Some(nonce.as_str())
        || body.get("state").and_then(Value::as_str) != Some("stop_report_received")
        || body.get("install_allowed").and_then(Value::as_bool) != Some(false) {
        return Err("Nieprawidłowe potwierdzenie raportu STOP".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    const NONCE: &str = "00112233445566778899aabbccddeeff";
    #[test]
    fn native_stop_is_bound_to_exact_v4_generation() {
        use sha2::{Digest, Sha256};
        let token = "a".repeat(64);
        let job = json!({"version":4,"job_id":"game_12345678","lease_token":token});
        let instruction = StopInstruction::NativeStopCheckpoint {
            job_id:"game_12345678".into(), request_id:"b".repeat(32),
            lease_token_sha256:format!("{:x}", Sha256::digest(token.as_bytes())),
        };
        assert!(native_stop_binding(&instruction, &job).is_ok());
        let mut changed = job.clone(); changed["lease_token"] = json!("c".repeat(64));
        assert!(native_stop_binding(&instruction, &changed).is_err());
        changed = job.clone(); changed["version"] = json!(3);
        assert!(native_stop_binding(&instruction, &changed).is_err());
        assert!(native_stop_binding(&StopInstruction::None, &job).is_err());
    }


    #[test]
    fn journal_reconciliation_rejects_concurrent_attempts_until_cancelled() {
        let current = JournalReconciliation::start().unwrap();
        assert!(std::thread::spawn(|| JournalReconciliation::start().is_err()).join().unwrap());
        drop(current);
        assert!(JournalReconciliation::start().is_ok());
    }

    fn journal_init_reply() -> Value {
        json!({
            "contract_version": 1, "authenticated": true, "source": "coordinator",
            "worker_id": "worker_12345678", "nonce": NONCE, "fenced": true,
            "checked_at_unix_ms": 1_000_000, "install_allowed": false,
        })
    }

    fn verify_journal_init(reply: Value, started: u64, now: u64) -> Result<JournalInitAuthorization, String> {
        let body: JournalInitReply = serde_json::from_value(reply)
            .map_err(|_| "Nieprawidłowa odpowiedź HTTPS")?;
        body.verify(
            coordinator_endpoint("https://arena.example", "journal-init-acquire")?,
            "worker_12345678", &"a".repeat(43), NONCE.into(), started, now, None,
        )
    }

    #[test]
    fn baseline_request_rejects_legacy_reply_without_explicit_approval() {
        for (version, accepted) in [(None, false), (Some(10), true), (Some(11), false)] {
            let mut payload = journal_init_reply();
            if let Some(version) = version {
                payload["baseline_version"] = json!(version);
            }
            let reply: JournalInitReply = serde_json::from_value(payload).unwrap();
            let proof = reply.verify(
                coordinator_endpoint("https://arena.example", "journal-init-acquire").unwrap(),
                "worker_12345678", &"a".repeat(43), NONCE.into(), 999_000, 1_000_001, Some(10),
            );
            assert_eq!(proof.is_ok(), accepted);
        }
    }

    #[test]
    fn journal_init_authorization_is_bound_to_origin_worker_token_nonce_and_time() {
        let proof = verify_journal_init(journal_init_reply(), 999_000, 1_000_001).unwrap();
        assert_eq!(proof.nonce(), NONCE);
        assert!(proof.matches("https://arena.example/", "worker_12345678", &"a".repeat(43), 1_030_000));
        assert!(!proof.matches("https://another.example", "worker_12345678", &"a".repeat(43), 1_000_001));
        assert!(!proof.matches("https://arena.example", "worker_87654321", &"a".repeat(43), 1_000_001));
        assert!(!proof.matches("https://arena.example", "worker_12345678", &"b".repeat(43), 1_000_001));
        assert!(!proof.matches("https://arena.example", "worker_12345678", &"a".repeat(43), 1_030_001));
    }

    #[test]
    fn journal_init_rejects_conflicting_unknown_and_stale_fields() {
        for (field, value) in [
            ("contract_version", json!(2)),
            ("authenticated", json!(false)),
            ("source", json!("client")),
            ("worker_id", json!("worker_87654321")),
            ("nonce", json!("0".repeat(32))),
            ("fenced", json!(false)),
            ("install_allowed", json!(true)),
            ("checked_at_unix_ms", json!(1_005_001)),
            ("extra", json!(true)),
        ] {
            let mut response = journal_init_reply();
            response[field] = value;
            assert!(verify_journal_init(response, 999_000, 1_000_000).is_err(), "{field}");
        }
        assert!(verify_journal_init(journal_init_reply(), 1_005_001, 1_005_002).is_err());
        assert!(verify_journal_init(journal_init_reply(), 999_000, 1_030_001).is_err());
        assert!(verify_journal_init(journal_init_reply(), 1_000_002, 1_000_001).is_err());
        let original = journal_init_reply().to_string();
        let duplicated = original.replacen('{', "{\"nonce\":\"duplicate\",", 1);
        assert!(serde_json::from_str::<JournalInitReply>(&duplicated).is_err());
    }

    #[test]
    fn journal_init_rejects_bad_https_origin_and_unreachable_server() {
        for base in [
            "http://arena.example", "https://arena.example/path",
            "https://user@arena.example", "https://arena.example?x=y",
        ] {
            assert!(coordinator_endpoint(base, "journal-init-acquire").is_err());
        }
        let result = tauri::async_runtime::block_on(acquire_journal_init(
            "https://[::1]", "worker_12345678", &"a".repeat(43),
        ));
        assert!(result.is_err());
    }
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
                job_id: "job_12345678".into(), request_id: "a".repeat(32)
            }
        );
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
    fn random_nonce_uses_os_rng_and_is_correctly_encoded() {
        let a = random_nonce().unwrap();
        let b = random_nonce().unwrap();
        assert_ne!(a, b);
        assert_eq!(a.len(), 32);
        assert!(a.bytes().all(|c| c.is_ascii_hexdigit()));
    }
}

#[cfg(all(test, unix))]
#[test]
fn stop_recovery_reply_is_strictly_bound() {
    let v = serde_json::json!({"authenticated":true,"source":"coordinator","worker_id":"worker_1234",
        "nonce":"nonce","job_id":"job_12345678","request_id":"request",
        "state":"stop_recovery_validated","install_allowed":false,"dispatch_allowed":false});
    let reply: StopRecoveryReply = serde_json::from_value(v.clone()).unwrap();
    assert!(reply.matches("worker_1234","nonce","job_12345678","request"));
    for (field, wrong) in [
        ("authenticated", serde_json::json!(false)), ("source", serde_json::json!("other")),
        ("worker_id", serde_json::json!("other")), ("nonce", serde_json::json!("other")),
        ("job_id", serde_json::json!("other")), ("request_id", serde_json::json!("other")),
        ("state", serde_json::json!("other")), ("install_allowed", serde_json::json!(true)),
        ("dispatch_allowed", serde_json::json!(true)),
    ] {
        let mut bad = v.clone();
        bad[field] = wrong;
        let reply: StopRecoveryReply = serde_json::from_value(bad).unwrap();
        assert!(!reply.matches("worker_1234","nonce","job_12345678","request"));
    }
}

#[cfg(all(test, unix))]
pub(crate) async fn test_unavailable_stop_transport(
    report: crate::android_native_stop_outbox::StopReport,
) -> Result<(), String> {
    let (job, request, lease) = report.recovery_identity("https://127.0.0.1", "worker_1234")
        .map_err(|_| "Unexpected test report identity")?;
    // Reserve a loopback port and drop the connection immediately: deterministic
    // real TLS transport failure, no DNS, external network or production token.
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    listener.set_nonblocking(true).unwrap();
    let peer = std::thread::spawn(move || {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
        loop {
            match listener.accept() {
                Ok((stream, _)) => { drop(stream); return true; }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock
                    && std::time::Instant::now() < deadline => std::thread::sleep(std::time::Duration::from_millis(5)),
                _ => return false,
            }
        }
    });
    let endpoint = reqwest::Url::parse(&format!("https://{address}/api/remote-workers/native-stop-recovery")).unwrap();
    let client = reqwest::Client::builder().no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(std::time::Duration::from_secs(4)).build().unwrap();
    let result = validate_pending_stop_at(endpoint, "worker_1234", "synthetic-token",
        job, request, lease, &random_nonce().unwrap(), client).await;
    assert!(peer.join().unwrap(), "test must reach the loopback transport");
    result
}
