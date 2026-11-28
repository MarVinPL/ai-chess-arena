#[cfg(target_os = "android")]
#[allow(dead_code)]
mod credential_journal;
// Native updater asset metadata gate; installation stays disabled until
// verified APK bytes, signing certificate, package and coordinator idle checks.
#[allow(dead_code)]
mod android_update_asset;
#[allow(dead_code)]
mod android_update_download;
// Pure policy gate; native Android PackageManager evidence is not wired yet.
// Never expose this as a claim that a staged APK is ready for installation.
#[allow(dead_code)]
mod android_update_identity;
#[allow(dead_code)]
mod android_update_install_evidence;
#[cfg(target_os = "android")]
mod android_update_package_manager;
#[cfg(target_os = "android")]
mod android_update_package_installer;
mod android_update_coordinator;
mod android_update_recovery;
mod android_update_install_journal;
mod android_local_llama;
#[cfg(target_os = "android")]
mod android_worker_token_store;
mod credential_vault;
mod credential_vault_native;
#[allow(dead_code)]
mod android_update_install_state;

use serde::{Deserialize, Serialize};
use std::time::Duration;


// Lock order: change gate -> token vault -> journal gate. Never across await.
static CREDENTIAL_CHANGE_GATE: std::sync::Mutex<()> = std::sync::Mutex::new(());
pub(crate) fn credential_change_gate() -> Result<std::sync::MutexGuard<'static, ()>, String> {
    CREDENTIAL_CHANGE_GATE.lock().map_err(|_| "Blokada poświadczeń niedostępna".into())
}

// A pending coordinator hold requires the *original* machine token for
// explicit authenticated release. Never let webview rotation/deletion orphan it.
fn ensure_no_pending_update() -> Result<(), String> {
    if android_update_install_journal::load()?.is_some() { return Err("Nierozliczona instalacja".into()); }
    match android_update_recovery::load_pending() {
        Ok(None) => Ok(()),
        Ok(Some(_)) => Err(
            "Trwa uzgadnianie rezerwacji aktualizacji. Nie zmieniaj tokenu Workera.".into()
        ),
        Err(_) => Err(
            "Nie można ustalić stanu rezerwacji aktualizacji. Zmiana tokenu zablokowana.".into()
        ),
    }
}

#[derive(Serialize)]
struct UpdateRecoveryStatus {
    pending: bool,
    blocked: bool,
    message: &'static str,
}

/// Read-only diagnostic: neither hold secret, worker id nor server URL leaves
/// Rust. Corrupt/unavailable keyring is displayed as BLOCKED, not empty.
#[tauri::command]
fn update_recovery_status() -> UpdateRecoveryStatus {
    let _guard = match credential_change_gate() {
        Ok(guard) => guard,
        Err(_) => return UpdateRecoveryStatus { pending: false, blocked: true, message: "Stan aktualizacji niedostępny." },
    };
    match android_update_install_journal::load() {
        Ok(None) => (),
        Ok(Some(_)) => return UpdateRecoveryStatus { pending: true, blocked: true, message: "Nierozliczona instalacja APK." },
        Err(_) => return UpdateRecoveryStatus { pending: false, blocked: true, message: "Stan instalacji niedostępny lub uszkodzony." },
    }
    match android_update_recovery::load_pending() {
        Ok(None) => UpdateRecoveryStatus {
            pending: false, blocked: false,
            message: "Brak oczekującej rezerwacji aktualizacji.",
        },
        Ok(Some(_)) => UpdateRecoveryStatus {
            pending: true, blocked: true,
            message: "Wykryto niezakończoną rezerwację. Przydziały pozostają zablokowane do uzgodnienia z koordynatorem.",
        },
        Err(_) => UpdateRecoveryStatus {
            pending: false, blocked: true,
            message: "Stan rezerwacji jest niedostępny lub uszkodzony. Aktualizacja i zmiana tokenu zablokowane.",
        },
    }
}

// Recovery is read-only: the private hold capability is used inside Rust,
// never returned to the webview. No reservation is released automatically.
#[tauri::command]
async fn inspect_update_hold() -> Result<String, String> {
    if android_update_recovery::load_pending()?.is_none() {
        return Err("Brak oczekującej rezerwacji".into());
    }
    let machine_token = token_entry()?.get_password()?;
    let status = android_update_coordinator::inspect_pending_update(&machine_token).await?;
    Ok(status.to_owned())
}

/// Query authenticated coordinator stop instructions through native HTTPS.
/// This is real network transport, but the Worker still has NO inference
/// executor. Never claim a STOP/ACK or authorize an install from this poll.
#[tauri::command]
async fn poll_worker_update_pause(server: String, worker_id: String) -> Result<&'static str, String> {
    // A local unresolved update journal wins over any newly reported status.
    if android_update_recovery::load_pending()?.is_some() {
        return Err("Trwa uzgadnianie poprzedniej aktualizacji".into());
    }
    let token = token_entry()?.get_password()?;
    use android_update_coordinator::StopInstruction;
    match android_update_coordinator::poll_pause_instruction(
        &server, &worker_id, &token,
    ).await? {
        StopInstruction::None => Ok("none"),
        StopInstruction::StopCheckpoint { .. } => Ok("stop_checkpoint"),
        StopInstruction::AwaitReconciliation => Ok("await_reconciliation"),
        StopInstruction::Blocked => Ok("blocked"),
    }
}

/// Read-only OS session inventory. Requires an existing private hold journal;
/// no install session creation, commits or automatic coordinator release.
#[tauri::command]
fn inspect_android_install_sessions() -> Result<&'static str, String> {
    if android_update_recovery::load_pending()?.is_none() {
        return Err("Brak oczekującej rezerwacji aktualizacji".into());
    }
    #[cfg(not(target_os = "android"))]
    {
        Err("Stan instalatora jest dostępny tylko na Androidzie".into())
    }
    #[cfg(target_os = "android")]
    {
        use android_update_package_installer::SessionObservation as S;
        let observation = android_update_package_installer::observe_own_session()?;
        // No observation is a confirmed terminal installation result.
        if android_update_package_installer::observation_is_terminal(observation) {
            return Err("Brak wiarygodnego wyniku systemowego".into());
        }
        Ok(match observation {
            S::NoVisibleSession => "not_visible",
            S::Uncommitted => "uncommitted",
            S::CommittedOrAwaitingConsent => "committed_or_pending",
            S::Ambiguous => "ambiguous",
        })
    }
}

fn token_entry() -> Result<credential_vault::CredentialVault<credential_vault_native::NativeBackend>, String> {
    credential_vault_native::native()
}

#[tauri::command]
fn store_worker_token(token: String) -> Result<(), String> {
    let _credential_guard = credential_change_gate()?;
    token_entry()?.set_password(&token, ensure_no_pending_update)
}

#[tauri::command]
fn has_saved_worker_token() -> bool {
    token_entry().and_then(|entry| entry.get_password()).is_ok()
}

// No secret crosses IPC; distinguish missing from inaccessible storage.
#[tauri::command]
fn worker_token_state() -> &'static str {
    match token_entry().and_then(|entry| entry.read_optional()) {
        Ok(Some(_)) => "saved",
        Ok(None) => "missing",
        Err(_) => "unavailable",
    }
}

#[tauri::command]
fn forget_worker_token() -> Result<(), String> {
    let _credential_guard = credential_change_gate()?;
    token_entry()?.delete_credential(ensure_no_pending_update)
}


#[tauri::command]
fn worker_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[derive(Serialize)]
struct WorkerStatus {
    connected: bool,
    running: bool,
    message: &'static str,
}

// A phone can run an optional local llama.cpp server. Desktop continues to
// show a disabled inference path. "running" means an active local request.
#[tauri::command]
fn worker_status() -> WorkerStatus {
    WorkerStatus {
        connected: false,
        running: android_local_llama::generation_active(),
        message: "Brak trwałej sesji przydziału; status obliczeń jest lokalny",
    }
}

#[tauri::command]
fn phone_inference_supported() -> bool {
    cfg!(target_os = "android")
}

#[tauri::command]
async fn inspect_phone_local_model() -> serde_json::Value {
    #[cfg(target_os = "android")]
    {
        android_local_llama::diagnostic_model_status().await
    }
    #[cfg(not(target_os = "android"))]
    {
        serde_json::json!({"state": "unsupported"})
    }
}

// The desktop app connects outbound only. Never pass the token into JS or a URL.
// HTTPS is mandatory; redirect following is disabled to avoid credential leakage.
#[tauri::command]
async fn verify_worker_connection(server: String, worker_id: String) -> Result<bool, String> {
    if worker_id.len() < 8 || worker_id.len() > 96 ||
        !worker_id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') {
        return Err("Nieprawidłowy identyfikator komputera".into());
    }
    let url = reqwest::Url::parse(&server).map_err(|_| "Nieprawidłowy adres serwera")?;
    if url.scheme() != "https" || url.host_str().is_none() || url.username() != "" ||
        url.password().is_some() || url.query().is_some() || url.fragment().is_some() ||
        url.path() != "/" || url.port().is_some() {
        return Err("Wymagany adres HTTPS bez portu, ścieżki i danych logowania".into());
    }
    let token = token_entry()?.get_password()?;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(12))
        .build().map_err(|_| "Błąd klienta HTTPS")?;
    let endpoint = url.join("api/remote-workers/authenticate")
        .map_err(|_| "Nieprawidłowy adres endpointu")?;
    let mut response = client.post(endpoint).json(&serde_json::json!({
        "worker_id": worker_id, "token": token
    })).send().await.map_err(|_| "Nie udało się połączyć przez HTTPS")?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED { return Ok(false); }
    if !response.status().is_success() { return Err("Serwer odrzucił żądanie".into()); }
    // A remote endpoint can lie about Content-Length or use chunked encoding.
    // Bound actual response bytes before parsing, including on low-memory Android.
    const MAX_AUTH_RESPONSE_BYTES: usize = 4096;
    if response.content_length().map(|n| n > MAX_AUTH_RESPONSE_BYTES as u64).unwrap_or(false) {
        return Err("Zbyt duża odpowiedź serwera".into());
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "Błąd odczytu HTTPS")? {
        if chunk.len() > MAX_AUTH_RESPONSE_BYTES - bytes.len() {
            return Err("Zbyt duża odpowiedź serwera".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    let result: serde_json::Value = serde_json::from_slice(&bytes)
        .map_err(|_| "Nieprawidłowa odpowiedź serwera")?;
    Ok(result.get("authenticated") == Some(&serde_json::Value::Bool(true)))
}

// Read-only, explicit job inspection. This never starts inferencing, downloads
// models, executes a command, or acknowledges a chess move.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PendingWorkerJob {
    version: u8,
    job_id: String,
    fen: String,
    model_sha256: String,
    max_tokens: u16,
    timeout_seconds: u16,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    variant: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    legal_moves: Option<Vec<String>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PendingJobResponse {
    job: Option<PendingWorkerJob>,
}

fn validate_pending_worker_job(job: &PendingWorkerJob) -> bool {
    let id = job.job_id.as_bytes();
    if !matches!(job.version, 1..=3) || id.len() < 8 || id.len() > 96 ||
        !id.iter().all(|c| c.is_ascii_alphanumeric() || *c == b'_' || *c == b'-') {
        return false;
    }
    if job.model_sha256.len() != 64 ||
        !job.model_sha256.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        return false;
    }
    if !(1..=1024).contains(&job.max_tokens) || !(1..=600).contains(&job.timeout_seconds) {
        return false;
    }
    match (job.version, job.variant.as_deref(), job.legal_moves.as_ref()) {
        (1, None, None) => {},
        (2, None, Some(moves)) | (3, Some("standard" | "elimination"), Some(moves)) => {
            if moves.is_empty() || moves.len() > 256 || !moves.windows(2).all(|w| w[0] < w[1]) ||
                !moves.iter().all(|m| {
                    let b = m.as_bytes();
                    matches!(b.len(), 4 | 5) && matches!(b[0], b'a'..=b'h') && matches!(b[1], b'1'..=b'8') &&
                    matches!(b[2], b'a'..=b'h') && matches!(b[3], b'1'..=b'8') &&
                    (b.len() == 4 || b"qrbn".contains(&b[4])) && b[..2] != b[2..4]
                }) { return false; }
        },
        _ => return false,
    }
    // A structural FEN check is defense in depth. The VPS validates a full
    // chess.Board and is the sole legal-move/checkpoint authority.
    if job.fen.len() > 120 || !job.fen.is_ascii() { return false; }
    let parts: Vec<&str> = job.fen.split(' ').collect();
    if parts.len() != 6 || !["w", "b"].contains(&parts[1]) ||
        !parts[4].bytes().all(|c| c.is_ascii_digit()) ||
        !parts[5].bytes().all(|c| c.is_ascii_digit()) ||
        parts[4].is_empty() || parts[5].is_empty() {
        return false;
    }
    if parts[3] != "-" && !(parts[3].len() == 2
        && matches!(parts[3].as_bytes()[0], b'a'..=b'h')
        && matches!(parts[3].as_bytes()[1], b'3' | b'6')) {
        return false;
    }
    if parts[2] != "-" && (parts[2].is_empty() ||
        parts[2].len() > 4 || !parts[2].bytes().all(|c| b"KQkq".contains(&c))) {
        return false;
    }
    let ranks: Vec<&str> = parts[0].split('/').collect();
    ranks.len() == 8 && ranks.iter().all(|rank| {
        let mut n = 0usize;
        for b in rank.bytes() {
            if (b'1'..=b'8').contains(&b) { n += (b - b'0') as usize; }
            else if b"prnbqkPRNBQK".contains(&b) { n += 1; }
            else { return false; }
        }
        n == 8
    })
}

#[tauri::command]
async fn preview_worker_job(server: String, worker_id: String) -> Result<Option<PendingWorkerJob>, String> {
    if worker_id.len() < 8 || worker_id.len() > 96 ||
        !worker_id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') {
        return Err("Nieprawidłowy identyfikator Workera".into());
    }
    let url = reqwest::Url::parse(&server).map_err(|_| "Nieprawidłowy adres VPS")?;
    if url.scheme() != "https" || url.host_str().is_none() ||
        !url.username().is_empty() || url.password().is_some() ||
        url.path() != "/" || url.query().is_some() ||
        url.fragment().is_some() || url.port().is_some() {
        return Err("Adres Workera musi być HTTPS bez portu, ścieżki i danych".into());
    }
    let token = token_entry()?.get_password()?;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(12)).build()
        .map_err(|_| "Błąd klienta HTTPS")?;
    let endpoint = url.join("api/remote-workers/job")
        .map_err(|_| "Błąd adresu endpointu")?;
    let mut response = client.post(endpoint)
        .json(&serde_json::json!({"worker_id": worker_id, "token": token}))
        .send().await.map_err(|_| "Błąd sieci HTTPS")?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED {
        return Err("Serwer odrzucił zapisany token".into());
    }
    if response.status() == reqwest::StatusCode::TOO_MANY_REQUESTS {
        return Err("Przekroczono limit sprawdzania zadań".into());
    }
    if !response.status().is_success() {
        return Err("Serwer nie udostępnia jeszcze odbioru zadań".into());
    }
    if response.content_length().map(|n| n > 4096).unwrap_or(false) {
        return Err("Nieprawidłowy rozmiar odpowiedzi".into());
    }
    // Limit actual bytes too: chunked/malformed servers must not cause
    // an unbounded allocation on a battery-powered volunteer device.
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "Błąd odczytu HTTPS")? {
        if chunk.len() > 4096 - bytes.len() {
            return Err("Zbyt duża odpowiedź serwera".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    let reply: PendingJobResponse = serde_json::from_slice(&bytes)
        .map_err(|_| "Nieprawidłowa struktura zadania")?;
    if let Some(ref job) = reply.job {
        if !validate_pending_worker_job(job) {
            return Err("Nieprawidłowe lub niekompletne zlecenie inferencji".into());
        }
    }
    Ok(reply.job)
}

// Read untrusted HTTPS JSON with a strict memory budget, including chunked
// responses and inaccurate Content-Length values. Never allow a remote host
// to allocate unlimited memory on an Android device.
async fn bounded_json_response(mut response: reqwest::Response, limit: usize)
    -> Result<serde_json::Value, String> {
    if response.content_length().map(|n| n > limit as u64).unwrap_or(false) {
        return Err("Zbyt duża odpowiedź HTTPS".into());
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "Błąd odczytu HTTPS")? {
        if chunk.len() > limit - bytes.len() {
            return Err("Zbyt duża odpowiedź HTTPS".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    serde_json::from_slice(&bytes).map_err(|_| "Nieprawidłowy JSON HTTPS".into())
}

// Advisory only: the server currently returns unknown until its durable
// coordinator job registry is wired. Never interpret an error as idle.
#[tauri::command]
async fn check_update_safety(server: String, worker_id: String) -> Result<String, String> {
    if worker_id.len() < 8 || worker_id.len() > 96 ||
        !worker_id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') {
        return Err("Nieprawidłowy identyfikator komputera".into());
    }
    let url = reqwest::Url::parse(&server).map_err(|_| "Nieprawidłowy adres serwera")?;
    if url.scheme() != "https" || url.host_str().is_none() || !url.username().is_empty() ||
        url.password().is_some() || url.query().is_some() || url.fragment().is_some() ||
        url.path() != "/" || url.port().is_some() {
        return Err("Wymagany adres HTTPS bez portu, ścieżki i danych logowania".into());
    }
    let token = token_entry()?.get_password()?;
    let client = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(12)).build().map_err(|_| "Błąd klienta HTTPS")?;
    let endpoint = url.join("api/remote-workers/update-safety")
        .map_err(|_| "Nieprawidłowy endpoint")?;
    let nonce = android_update_coordinator::random_nonce()?;
    let response = client.post(endpoint).json(&serde_json::json!({
        "worker_id": worker_id, "token": token, "nonce": nonce
    })).send().await.map_err(|_| "Nie udało się sprawdzić stanu zadań")?;
    if !response.status().is_success() { return Err("Nie udało się potwierdzić stanu zadań".into()); }
    let data: serde_json::Value = bounded_json_response(response, 4096).await?;
    let state = data.get("state").and_then(|v| v.as_str()).unwrap_or("unknown");
    // Server response must explicitly prove fresh coordinator-derived idle:
    // previously cached or unauthenticated "idle_confirmed" is NOT enough.
    // Legacy servers without nonce/time/task evidence remain fail-closed.
    if state == "running" { return Ok("running".into()); }
    let now = android_update_coordinator::system_unix_ms()?;
    if android_update_coordinator::fresh_authenticated_idle(&data, &worker_id, &nonce, now) {
        Ok("idle_confirmed".into())
    } else {
        Ok("unknown".into())
    }
}

// Read-only release discovery. GitHub metadata is never treated as permission
// to install: signed Tauri artifacts and a verified idle state are required.
#[derive(Serialize)]
struct ReleaseInfo {
    current_version: String,
    latest_version: Option<String>,
    update_available: bool,
    release_notes_url: Option<String>,
    // Read-only indicator: candidate metadata is not an installable APK.
    apk_asset_available: bool,
}

fn numeric_version(version: &str) -> Option<[u64; 3]> {
    let version = version.strip_prefix('v').unwrap_or(version);
    if version.is_empty() || version.contains('-') || version.contains('+') { return None; }
    let parts: Vec<_> = version.split('.').collect();
    if parts.len() != 3 || parts.iter().any(|p| p.is_empty() || !p.bytes().all(|c| c.is_ascii_digit())) { return None; }
    Some([parts[0].parse().ok()?, parts[1].parse().ok()?, parts[2].parse().ok()?])
}

// Explicit, read-only release channels. Neither channel permits installation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum WorkerReleaseChannel { Latest, PreRelease }

impl WorkerReleaseChannel {
    fn parse(name: &str) -> Result<Self, String> {
        match name {
            "latest" => Ok(Self::Latest),
            "pre-release" => Ok(Self::PreRelease),
            _ => Err("Nieznany kanał aktualizacji".into()),
        }
    }
    fn is_prerelease(self) -> bool { self == Self::PreRelease }
}

fn worker_release_version(tag: &str, channel: WorkerReleaseChannel) -> Option<[u64; 3]> {
    let version = tag.strip_prefix("worker-v")?;
    if channel == WorkerReleaseChannel::Latest { return numeric_version(version); }
    let (base, suffix) = version.split_once('-')?;
    if suffix.is_empty() || suffix.len() > 64 || suffix.starts_with('.')
        || suffix.ends_with('.') || suffix.contains("..")
        || suffix.split('.').any(|part| part.is_empty() ||
            !part.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')) {
        return None;
    }
    numeric_version(base)
}

fn release_published_at(release: &serde_json::Value) -> &str {
    release.get("published_at").and_then(|v| v.as_str()).unwrap_or("")
}

// Filter out server releases, drafts, and mismatched prerelease flags.
fn newest_worker_release(releases: &[serde_json::Value], channel: WorkerReleaseChannel)
    -> Option<([u64; 3], &str, &serde_json::Value)> {
    releases.iter()
        .filter(|r| r.get("draft").and_then(|v| v.as_bool()) == Some(false)
            && r.get("prerelease").and_then(|v| v.as_bool()) == Some(channel.is_prerelease()))
        .filter_map(|r| {
            let tag = r.get("tag_name")?.as_str()?;
            Some((worker_release_version(tag, channel)?, tag, r))
        })
        .max_by(|a, b| a.0.cmp(&b.0)
            .then_with(|| release_published_at(a.2).cmp(release_published_at(b.2)))
            .then_with(|| a.1.cmp(b.1)))
}

// The same bounded GitHub fetch is used for display and download decisions.
// JavaScript cannot provide a release URL, asset digest or destination path.
async fn fetch_worker_releases() -> Result<Vec<serde_json::Value>, String> {
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(8))
        .build().map_err(|_| "Błąd klienta HTTPS")?;
    // This repository publishes server and adapter releases too.
    let response = client.get("https://api.github.com/repos/MarVinPL/ai-chess-arena/releases?per_page=100")
        .header(reqwest::header::USER_AGENT, "ai-chess-arena-worker-update-check")
        .send().await.map_err(|_| "Nie udało się sprawdzić wydań")?;
    if !response.status().is_success() {
        return Err("GitHub nie udostępnił informacji o wydaniach".into());
    }
    let data: serde_json::Value = bounded_json_response(response, 1024 * 1024).await
        .map_err(|_| "Nieprawidłowe metadane wydań")?;
    let releases = data.as_array().ok_or("Nieprawidłowa lista wydań")?;
    Ok(releases.clone())
}

#[tauri::command]
async fn check_worker_release(channel: String) -> Result<ReleaseInfo, String> {
    let channel = WorkerReleaseChannel::parse(&channel)?;
    let current = env!("CARGO_PKG_VERSION").to_string();
    let releases = fetch_worker_releases().await?;
    let installed = numeric_version(&current).ok_or("Nieprawidłowa wersja aplikacji")?;
    let candidate = newest_worker_release(&releases, channel);
    let Some((latest, tag, data)) = candidate else {
        return Ok(ReleaseInfo { current_version: current, latest_version: None, update_available: false, release_notes_url: None, apk_asset_available: false });
    };
    let expected_path = format!("/MarVinPL/ai-chess-arena/releases/tag/{tag}");
    let notes = data.get("html_url").and_then(|v| v.as_str())
        .and_then(|raw| reqwest::Url::parse(raw).ok())
        .filter(|url| url.scheme() == "https" && url.host_str() == Some("github.com")
            && url.path() == expected_path
            && url.port().is_none() && url.query().is_none()
            && url.fragment().is_none()
            && url.username().is_empty() && url.password().is_none())
        .map(|url| url.to_string());
    // A prerelease with the same base version is never a newer installable APK.
    // The update candidate selector is now used in the native release-check
    // path. This is metadata only, never a download/install authorization.
    let apk_asset_available = latest > installed
        && android_update_asset::select_apk_candidate(data, tag).is_some();
    Ok(ReleaseInfo { current_version: current, latest_version: Some(tag.to_owned()), update_available: latest > installed, release_notes_url: notes, apk_asset_available })
}

// Only Android may stage a private APK. Re-query the trusted release feed
// server-side rather than accepting a URL or digest from JavaScript.
#[tauri::command]
async fn download_worker_apk(app: tauri::AppHandle) -> Result<(), String> {
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Err("Pobieranie APK jest dostępne wyłącznie na Androidzie".into())
    }
    #[cfg(target_os = "android")]
    {
        let current = numeric_version(env!("CARGO_PKG_VERSION"))
            .ok_or("Nieprawidłowa wersja Workera")?;
        let releases = fetch_worker_releases().await?;
        let (latest, tag, release) = newest_worker_release(&releases, WorkerReleaseChannel::Latest)
            .ok_or("Brak stabilnego wydania Workera")?;
        if latest <= current { return Err("Brak nowszej wersji".into()); }
        let candidate = android_update_asset::select_apk_candidate(release, tag)
            .ok_or("Brak bezpiecznego pliku APK ARM64 w wydaniu")?;
        android_update_download::stage_candidate(&app, candidate, tag).await
    }
}

#[derive(Serialize)]
struct WorkerApkIdentityResult {
    verified: bool,
    installer_permission: bool,
    reason: &'static str,
}

// Read-only post-download verification. All inputs are re-derived in Rust
// from the official release feed; neither the APK path nor fingerprint comes
// from JavaScript. Even a verified result never authorizes installation.
#[tauri::command]
async fn verify_downloaded_worker_apk(app: tauri::AppHandle) -> Result<WorkerApkIdentityResult, String> {
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Err("Sprawdzanie APK jest dostępne wyłącznie na Androidzie".into())
    }
    #[cfg(target_os = "android")]
    {
        use tauri::Manager;
        let installed = numeric_version(env!("CARGO_PKG_VERSION"))
            .ok_or("Nieprawidłowa wersja aplikacji")?;
        let releases = fetch_worker_releases().await?;
        let (latest, tag, release) = newest_worker_release(&releases, WorkerReleaseChannel::Latest)
            .ok_or("Brak stabilnego wydania Workera")?;
        if latest <= installed { return Err("Brak nowszej aktualizacji".into()); }
        let candidate = android_update_asset::select_apk_candidate(release, tag)
            .ok_or("Brak kwalifikującego się APK")?;
        let apk = app.path().app_cache_dir()
            .map_err(|_| "Brak katalogu APK")?
            .join("worker-apk-updates")
            .join(format!("{tag}.apk"));
        let metadata = std::fs::symlink_metadata(&apk)
            .map_err(|_| "APK nie został jeszcze pobrany")?;
        if !metadata.file_type().is_file() || metadata.len() != candidate.size {
            return Err("Nieprawidłowy plik APK".into());
        }
        let mut file = std::fs::File::open(&apk)
            .map_err(|_| "Nie można sprawdzić pobranego APK")?;
        if !android_update_asset::verify_apk_stream(&mut file, &candidate)
            .map_err(|_| "Błąd weryfikacji SHA-256")? {
            return Err("APK nie przeszedł kontroli integralności".into());
        }
        drop(file);
        let identity = android_update_package_manager::inspect_staged_worker_apk(&apk)?;
        let verified = identity == android_update_identity::IdentityCheck::Verified;
        // This permission may be absent even when the APK identity matches.
        // A permission alone NEVER authorizes install, and never changes
        // update_installation_readiness(false).
        let installer_permission = android_update_package_manager::
            can_request_package_installation().unwrap_or(false);
        Ok(WorkerApkIdentityResult {
            verified,
            installer_permission,
            reason: if verified {
                "Podpis, pakiet i versionCode zgodne; instalacja nadal wyłączona"
            } else {
                "Certyfikat, pakiet lub versionCode APK nie spełnia wymagań"
            },
        })
    }
}

/// Start a genuine OS PackageInstaller transaction. No path, digest or
/// signing certificate is accepted from JavaScript. On active jobs, refuse
/// installation until the real executor has fenced the previous lease.
#[tauri::command]
async fn install_downloaded_worker_apk(
    app: tauri::AppHandle, server: String, worker_id: String,
) -> Result<&'static str, String> {
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, server, worker_id);
        Err("Instalacja APK jest dostępna wyłącznie na Androidzie".into())
    }
    #[cfg(target_os = "android")]
    {
        use tauri::Manager;
        use android_update_identity::IdentityCheck;
        if android_update_recovery::load_pending()?.is_some()
            || android_update_install_journal::load()?.is_some() {
            return Err("Wcześniejsza instalacja wymaga uzgodnienia".into());
        }
        if android_local_llama::generation_active() {
            return Err("Telefon aktualnie oblicza ruch — instalacja APK zablokowana".into());
        }
        // Never turn a failed/unknown coordinator response into an idle state.
        // The server's reserve transaction rechecks state atomically.
        let state = check_update_safety(server.clone(), worker_id.clone()).await?;
        if state != "idle_confirmed" {
            return Err("Najpierw bezpiecznie przerwij zadanie Workera; brak potwierdzenia STOP".into());
        }
        let releases = fetch_worker_releases().await?;
        let (_, tag, release) = newest_worker_release(&releases, WorkerReleaseChannel::Latest)
            .ok_or("Brak aktualizacji Workera")?;
        let candidate = android_update_asset::select_apk_candidate(release, tag)
            .ok_or("Nie ma jednoznacznego, podpisanego APK ARM64")?;
        let apk = app.path().app_cache_dir()
            .map_err(|_| "Brak katalogu aktualizacji")?
            .join("worker-apk-updates").join(format!("{tag}.apk"));
        let metadata = std::fs::symlink_metadata(&apk)
            .map_err(|_| "Najpierw pobierz APK")?;
        if !metadata.file_type().is_file() || metadata.len() != candidate.size {
            return Err("Nieprawidłowy pobrany APK".into());
        }
        let mut file = std::fs::File::open(&apk)
            .map_err(|_| "Nie można ponownie odczytać APK")?;
        if !android_update_asset::verify_apk_stream(&mut file, &candidate)
            .map_err(|_| "Nie można zweryfikować SHA-256")? {
            return Err("Niezgodny SHA-256 pobranego APK".into());
        }
        drop(file);
        let (identity, previous, target) =
            android_update_package_manager::inspect_staged_worker_apk_details(&apk)?;
        if identity != IdentityCheck::Verified {
            return Err("Podpis, nazwa pakietu lub versionCode APK nie zgadzają się".into());
        }
        if !android_update_package_manager::can_request_package_installation()? {
            return Err("Zezwól Android Workerowi na instalowanie aplikacji w ustawieniach systemu".into());
        }
        let machine_token = token_entry()?.get_password()?;
        // reserve_update journals the random hold before any network request.
        // The coordinator rejects running jobs and blocks further assignments.
        android_update_coordinator::reserve_update(&server, &worker_id, &machine_token).await?;
        let session_id = android_update_package_installer::stage_verified_apk_session(
            &apk, true, true, &candidate.expected_sha256,
        )?;
        let journal = android_update_install_journal::PendingInstall {
            session_id,
            previous_version_code: previous,
            target_version_code: target,
            apk_sha256: candidate.expected_sha256,
        };
        android_update_install_journal::save(&journal)?;
        // Success here means submitted to Android, NOT installed.
        // Consent is handled by the non-exported Kotlin receiver.
        android_update_package_installer::commit_staged_session(session_id)?;
        Ok("submitted")
    }
}

/// Inspect only the job that Arena has atomically assigned to this phone.
/// This does not run inference, claim a lease or mutate Arena's chess state.
#[tauri::command]
async fn poll_my_inference_job(
    server: String, worker_id: String,
) -> Result<serde_json::Value, String> {
    if android_update_recovery::load_pending()?.is_some()
        || android_update_install_journal::load()?.is_some() {
        return Ok(serde_json::json!({"state": "blocked"}));
    }
    let machine_token = token_entry()?.get_password()?;
    android_update_coordinator::fetch_my_inference_job(
        &server, &worker_id, &machine_token,
    ).await
}

/// Refuse new or completed inference when Arena has asked for STOP.
/// This is a negative safety gate, NOT STOP/ACK: the Arena remains owner of
/// the checkpoint and keeps the execution lease fenced until reconciliation.
#[cfg(target_os = "android")]
async fn check_phone_stop_gate(
    server: &str, worker_id: &str, token: &str, job_id: &str,
) -> Result<(), String> {
    use android_update_coordinator::StopInstruction;
    match android_update_coordinator::poll_pause_instruction(
        server, worker_id, token,
    ).await? {
        StopInstruction::None => Ok(()),
        StopInstruction::StopCheckpoint { job_id: requested, .. } => {
            if requested != job_id {
                Err("Polecenie STOP dotyczy innego zadania — blokada".into())
            } else {
                Err("Arena zażądała STOP. Telefon nie wyśle ruchu; wymagane uzgodnienie checkpointu przez Arenę".into())
            }
        }
        StopInstruction::AwaitReconciliation | StopInstruction::Blocked => {
            Err("Arena blokuje inferencję do uzgodnienia zatrzymania".into())
        }
    }
}

/// Execute exactly one Arena-assigned move on THIS Android device, using a
/// local llama.cpp service (for example in Termux). No VPS inference, no game
/// state or checkpoint writes on the phone. The local llama-server model
/// alias prevents accidental model mismatches but is not a GGUF hash proof.
#[tauri::command]
async fn execute_phone_inference_once(
    server: String, worker_id: String,
) -> Result<serde_json::Value, String> {
    #[cfg(not(target_os = "android"))]
    {
        let _ = (server, worker_id);
        Err("Lokalna inferencja telefonu wymaga Androida".into())
    }
    #[cfg(target_os = "android")]
    {
        let _permit = android_local_llama::acquire_generation()?;
        if android_update_recovery::load_pending()?.is_some()
            || android_update_install_journal::load()?.is_some() {
            return Err("Trwa aktualizacja APK — inferencja zablokowana".into());
        }
        let token = token_entry()?.get_password()?;
        let assignment = android_update_coordinator::fetch_my_inference_job(
            &server, &worker_id, &token,
        ).await?;
        let state = assignment.get("state").and_then(serde_json::Value::as_str)
            .ok_or("Brak stanu przydziału Areny")?;
        if state == "none" {
            // Only an actually reachable on-phone llama.cpp instance with a
            // SHA-formatted model alias may advertise readiness. This alias
            // is NOT cryptographic GGUF byte verification.
            let model_alias = android_local_llama::probe_local_model_alias().await?;
            let ready = android_update_coordinator::announce_phone_ready(
                &server, &worker_id, &token, &model_alias,
            ).await?;
            return Ok(serde_json::json!({"state": ready}));
        }
        if state != "assigned" {
            return Ok(serde_json::json!({"state": state}));
        }
        let job = assignment.get("job")
            .ok_or("Brak zadania obliczeń")?;
        let job_id = job.get("job_id").and_then(serde_json::Value::as_str)
            .ok_or("Brak identyfikatora zadania")?;
        // STOP and missing coordinator connectivity both fail closed. This
        // request does not ACK the pause nor release its lease.
        check_phone_stop_gate(&server, &worker_id, &token, job_id).await?;
        let uci = android_local_llama::infer_local_uci(job).await?;
        check_phone_stop_gate(&server, &worker_id, &token, job_id).await?;
        // This check is also performed atomically by the coordinator result
        // endpoint. It guards against APK installation while generating.
        if android_update_recovery::load_pending()?.is_some()
            || android_update_install_journal::load()?.is_some() {
            return Err("Aktualizacja rozpoczęła się podczas obliczeń".into());
        }
        let again = android_update_coordinator::fetch_my_inference_job(
            &server, &worker_id, &token,
        ).await?;
        if again.get("state").and_then(serde_json::Value::as_str) != Some("assigned")
            || again.get("job") != Some(job) {
            return Err("Arena zmieniła lub unieważniła zadanie podczas obliczeń".into());
        }
        let result = android_update_coordinator::submit_my_inference_move(
            &server, &worker_id, &token, job_id, &uci,
        ).await?;
        Ok(serde_json::json!({
            "state": result, "job_id": job_id,
        }))
    }
}

/// Arena is the sole owner of the chessboard and checkpoint.
#[tauri::command]
async fn get_my_worker_board(
    server: String, worker_id: String,
) -> Result<serde_json::Value, String> {
    let machine_token = token_entry()?.get_password()?;
    android_update_coordinator::fetch_worker_board_view(
        &server, &worker_id, &machine_token,
    ).await
}

/// Recover only an EMPTY, previously idle phone update. Arena stores a
/// one-time proof created after exact update-hold release; it is consumed
/// by an authenticated local-model readiness. This never resumes an in-flight
/// chess move or reads a checkpoint payload.
#[tauri::command]
async fn reconcile_idle_phone_after_update(
    server: String, worker_id: String,
) -> Result<&'static str, String> {
    #[cfg(not(target_os = "android"))]
    {
        let _ = (server, worker_id);
        Err("Uzgodnienie telefonu wymaga Androida".into())
    }
    #[cfg(target_os = "android")]
    {
        if android_update_recovery::load_pending()?.is_some()
            || android_update_install_journal::load()?.is_some() {
            return Err("Niezakończona instalacja APK blokuje wznowienie".into());
        }
        if android_local_llama::generation_active() {
            return Err("Telefon nadal wykonuje inferencję".into());
        }
        let token = token_entry()?.get_password()?;
        let resume = android_update_coordinator::fetch_resume_status(
            &server, &worker_id, &token,
        ).await?;
        if resume != "no_checkpoint" {
            return Err("Przerwane zadanie wymaga oddzielnego ACK Areny".into());
        }
        let assignment = android_update_coordinator::fetch_my_inference_job(
            &server, &worker_id, &token,
        ).await?;
        if assignment.get("state").and_then(serde_json::Value::as_str)
            != Some("none") {
            return Err("Worker ma już przydział lub nierozliczoną partię".into());
        }
        // A local llama.cpp SHA alias is a routing check, NOT byte-level GGUF
        // attestation. No new inference job is started by this command.
        let digest = android_local_llama::probe_local_model_alias().await?;
        let status = android_update_coordinator::announce_phone_ready(
            &server, &worker_id, &token, &digest,
        ).await?;
        if status != "ready" {
            return Err("Arena nie potwierdziła bezczynnego Workera".into());
        }
        Ok("ready")
    }
}

/// Read coordinator resume readiness after an Android update. A checkpoint
/// without a real inference executor does not permit launching a game.
#[tauri::command]
async fn inspect_worker_resume_readiness(
    server: String, worker_id: String,
) -> Result<&'static str, String> {
    if android_update_recovery::load_pending()?.is_some()
        || android_update_install_journal::load()?.is_some() {
        return Ok("waiting_update_reconciliation");
    }
    let token = token_entry()?.get_password()?;
    android_update_coordinator::fetch_resume_status(&server, &worker_id, &token).await
}

/// Read OS install callback only for the journaled session. This does not
/// release the coordinator hold or claim an installed update.
#[tauri::command]
fn inspect_android_install_result() -> Result<&'static str, String> {
    #[cfg(not(target_os = "android"))]
    {
        Err("Wynik instalatora jest dostępny tylko na Androidzie".into())
    }
    #[cfg(target_os = "android")]
    {
        let journal = android_update_install_journal::load()?
            .ok_or("Brak oczekującej instalacji Android")?;
        let status = android_update_package_installer::read_session_result(journal.session_id)?;
        // Android PackageInstaller status values:
        // 0 success; -1 pending user action; 1 failure; other positives failure.
        // Never report installed just because STATUS_SUCCESS was received.
        Ok(match status {
            None => "no_callback",
            Some(-1) => "awaiting_user_consent",
            Some(0) => "os_success_unverified",
            Some(_) => "os_failed_or_cancelled",
        })
    }
}

/// Reconcile after restart using actual installed package version and signer.
/// No timeout, absent session or UI label is ever evidence of success.
#[tauri::command]
async fn reconcile_installed_worker_update() -> Result<&'static str, String> {
    #[cfg(not(target_os = "android"))]
    {
        Err("Weryfikacja instalacji jest dostępna tylko na Androidzie".into())
    }
    #[cfg(target_os = "android")]
    {
        let journal = match android_update_install_journal::load()? {
            Some(journal) => journal,
            None => return Ok("no_pending_install"),
        };
        let pending = android_update_recovery::load_pending()?
            .ok_or("Nie ma rezerwacji koordynatora dla instalacji")?;
        let installed = android_update_package_manager::inspect_installed_worker_identity()?;
        if !android_update_identity::installed_update_matches_journal(
            &installed, journal.previous_version_code, journal.target_version_code,
        ) {
            // Android explicitly reports failure/cancellation, AND the old
            // installed package remains intact with the original signing key:
            // only then may we release the hold. Absent/pending callbacks or
            // changed package identity keep the transaction blocked.
            let status = android_update_package_installer::read_session_result(journal.session_id)?;
            let old_app_intact = installed.package_name == android_update_identity::WORKER_PACKAGE_ID
                && installed.platform_signing_verified
                && installed.current_signer_count == 1
                && installed.certificate_sha256 ==
                   android_update_identity::WORKER_SIGNING_CERT_SHA256
                && installed.long_version_code == journal.previous_version_code;
            if !old_app_intact || !status.is_some_and(|code| code > 0) {
                return Ok("not_confirmed");
            }
            let machine_token = token_entry()?.get_password()?;
            android_update_coordinator::release_update(
                &pending.server, &pending.worker_id, &machine_token,
            ).await?;
            android_update_install_journal::clear_confirmed(&journal)?;
            return Ok("installation_cancelled");
        }
        let machine_token = token_entry()?.get_password()?;
        android_update_coordinator::release_update(
            &pending.server, &pending.worker_id, &machine_token,
        ).await?;
        android_update_install_journal::clear_confirmed(&journal)?;
        // The coordinator returns UNKNOWN. A real worker resume dispatcher
        // must independently reconcile its checkpoint and lease.
        Ok("installed_confirmed")
    }
}

// Fail closed: a missing coordinator confirmation is not an idle worker.
// The scheduler transport must provide an authenticated, fresh idle state.

#[allow(dead_code)] // Running is reserved for authenticated scheduler state integration.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum VerifiedTaskState {
    IdleConfirmed,
    // A job may be interrupted for an update after the real executor has
    // stopped and its previous lease has been fenced. This is NOT idle.
    InterruptedAndFenced,
    Running,
    Unknown,
}

fn may_install_update(state: VerifiedTaskState, signature_verified: bool, newer_version: bool) -> bool {
    matches!(state, VerifiedTaskState::IdleConfirmed | VerifiedTaskState::InterruptedAndFenced)
        && signature_verified && newer_version
}

#[derive(Serialize)]
struct UpdateReadiness {
    can_install: bool,
    reason: &'static str,
}

// Explicitly report blocked state until BOTH verified coordinator idle and
// cryptographically verified update artifacts are integrated. Never infer idle
// from a disconnected worker or from the read-only GitHub release listing.
#[tauri::command]
fn update_installation_readiness() -> UpdateReadiness {
    let can_install = may_install_update(VerifiedTaskState::Unknown, false, false);
    UpdateReadiness {
        can_install,
        reason: "Instalacja niedostępna: brak potwierdzenia bezczynności i podpisanej aktualizacji",
    }
}

#[cfg(test)]
mod version_tests {
    use super::{numeric_version, newest_worker_release, worker_release_version, WorkerReleaseChannel, may_install_update, update_installation_readiness, VerifiedTaskState, PendingWorkerJob, validate_pending_worker_job};
    use serde_json::json;
    #[test]
    fn selects_newest_stable_worker_only() {
        let releases = vec![
            json!({"tag_name":"v99.0.0","draft":false,"prerelease":false}),
            json!({"tag_name":"worker-v4.0.0","draft":false,"prerelease":true}),
            json!({"tag_name":"worker-v3.0.0","draft":true,"prerelease":false}),
            json!({"tag_name":"worker-v1.10.0","draft":false,"prerelease":false}),
            json!({"tag_name":"worker-v1.9.9","draft":false,"prerelease":false}),
            json!({"tag_name":"worker-v1.11.0-rc1","draft":false,"prerelease":false}),
        ];
        let selected = newest_worker_release(&releases, WorkerReleaseChannel::Latest).unwrap();
        assert_eq!(selected.1, "worker-v1.10.0");
        assert_eq!(selected.0, [1,10,0]);
    }
    #[test]
    fn variant_jobs_require_known_rules_and_sorted_uci() {
        let job = serde_json::json!({
            "version": 3, "variant": "elimination", "job_id": "job_12345678",
            "fen": "4k3/8/8/8/8/8/4R3/4K3 w - - 0 1",
            "model_sha256": "a".repeat(64), "max_tokens": 32,
            "timeout_seconds": 300, "legal_moves": ["e2e7", "e2e8"]
        });
        let valid = |v| validate_pending_worker_job(&serde_json::from_value::<PendingWorkerJob>(v).unwrap());
        assert!(valid(job.clone()));
        for (field, value) in [("variant", serde_json::json!("unknown")),
                               ("version", serde_json::json!(2)),
                               ("legal_moves", serde_json::json!(["e2e8", "e2e7"])),
                               ("legal_moves", serde_json::json!(["e2e7", "e2e7"])),
                               ("legal_moves", serde_json::json!(["e2e2"]))] {
            let mut bad = job.clone();
            bad[field] = value;
            assert!(!valid(bad));
        }
        let mut missing = job;
        missing.as_object_mut().unwrap().remove("variant");
        assert!(!valid(missing));
    }

    #[test]
    fn only_complete_typed_worker_jobs_are_displayable() {
        let ok: PendingWorkerJob = serde_json::from_value(serde_json::json!({
            "version": 1, "job_id": "job_12345678",
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "model_sha256": "a".repeat(64),
            "max_tokens": 512, "timeout_seconds": 300
        })).unwrap();
        assert!(validate_pending_worker_job(&ok));
        let mut illegal = serde_json::to_value(&ok).unwrap();
        illegal["fen"] = serde_json::json!("not a chess position");
        assert!(!validate_pending_worker_job(
            &serde_json::from_value::<PendingWorkerJob>(illegal).unwrap()
        ));
        let mut illegal = serde_json::to_value(&ok).unwrap();
        illegal["model_sha256"] = serde_json::json!("../unexpected-file.gguf");
        assert!(!validate_pending_worker_job(
            &serde_json::from_value::<PendingWorkerJob>(illegal).unwrap()
        ));
        let mut illegal = serde_json::to_value(&ok).unwrap();
        illegal["timeout_seconds"] = serde_json::json!(601);
        assert!(!validate_pending_worker_job(
            &serde_json::from_value::<PendingWorkerJob>(illegal).unwrap()
        ));
        let mut illegal = serde_json::to_value(&ok).unwrap();
        illegal["command"] = serde_json::json!("exec");
        assert!(serde_json::from_value::<PendingWorkerJob>(illegal).is_err());
        let mut illegal = serde_json::to_value(&ok).unwrap();
        illegal.as_object_mut().unwrap().remove("max_tokens");
        assert!(serde_json::from_value::<PendingWorkerJob>(illegal).is_err());
    }

    #[test]
    fn test_channel_finds_real_signed_worker_prerelease_only() {
        let releases = vec![
            json!({"tag_name":"issue-190-lora-v1","draft":false,"prerelease":true}),
            json!({"tag_name":"worker-v0.1.0-test.1","draft":false,"prerelease":true,
                   "published_at":"2026-11-21T15:00:00Z"}),
            json!({"tag_name":"worker-v0.1.0-test.2","draft":false,"prerelease":true,
                   "published_at":"2026-11-22T15:00:00Z"}),
            json!({"tag_name":"worker-v0.1.0-test.3","draft":true,"prerelease":true}),
            json!({"tag_name":"worker-v0.1.0","draft":false,"prerelease":false}),
            json!({"tag_name":"worker-v99.0.0","draft":false,"prerelease":true}),
        ];
        let selected = newest_worker_release(&releases, WorkerReleaseChannel::PreRelease).unwrap();
        assert_eq!(selected.1, "worker-v0.1.0-test.2");
        assert_eq!(selected.0, [0, 1, 0]);
    }
    #[test]
    fn channel_names_and_release_tag_flags_are_strict() {
        assert_eq!(WorkerReleaseChannel::parse("latest").unwrap(), WorkerReleaseChannel::Latest);
        assert_eq!(WorkerReleaseChannel::parse("pre-release").unwrap(), WorkerReleaseChannel::PreRelease);
        assert!(WorkerReleaseChannel::parse("anything-else").is_err());
        for tag in ["worker-v1.2.3", "worker-v1.2.3-",
                    "worker-v1.2.3-../bad", "worker-v1.2.3-foo/evil",
                    "worker-v1.2.3-test..1", "worker-v1.2.3-test.1+build"] {
            assert!(worker_release_version(tag, WorkerReleaseChannel::PreRelease).is_none());
        }
        assert!(worker_release_version("worker-v1.2.3-test.1", WorkerReleaseChannel::Latest).is_none());
        assert_eq!(worker_release_version("worker-v0.1.0-test.1", WorkerReleaseChannel::PreRelease), Some([0,1,0]));
        let invalid = vec![
            json!({"tag_name":"worker-v1.2.3","draft":false,"prerelease":true}),
            json!({"tag_name":"worker-v1.2.3-test.1","draft":false,"prerelease":false}),
        ];
        assert!(newest_worker_release(&invalid, WorkerReleaseChannel::Latest).is_none());
        assert!(newest_worker_release(&invalid, WorkerReleaseChannel::PreRelease).is_none());
    }
    #[test]
    fn same_version_prerelease_does_not_authorize_install() {
        let base = worker_release_version("worker-v0.1.0-test.1", WorkerReleaseChannel::PreRelease).unwrap();
        assert_eq!(base, numeric_version("0.1.0").unwrap());
        assert!(!(base > numeric_version("0.1.0").unwrap()));
        assert!(!update_installation_readiness().can_install);
    }
    #[test]
    fn no_eligible_release_means_no_update() {
        let releases = vec![json!({"tag_name":"worker-v2.0.0","draft":true,"prerelease":false}), json!({"tag_name":"server-v9.0.0","draft":false,"prerelease":false})];
        assert!(newest_worker_release(&releases, WorkerReleaseChannel::Latest).is_none());
    }
    #[test]
    fn gui_never_offers_installation_before_integrations() {
        assert!(!update_installation_readiness().can_install);
    }
    #[test]
    fn update_installation_requires_verified_idle_and_signature() {
        assert!(may_install_update(VerifiedTaskState::IdleConfirmed, true, true));
        // The user may update while a game is active, but ONLY after an
        // authenticated stop and exclusive lease fencing — never while running.
        assert!(may_install_update(VerifiedTaskState::InterruptedAndFenced, true, true));
        assert!(!may_install_update(VerifiedTaskState::InterruptedAndFenced, false, true));
        for state in [VerifiedTaskState::Running, VerifiedTaskState::Unknown] {
            assert!(!may_install_update(state, true, true));
        }
        assert!(!may_install_update(VerifiedTaskState::IdleConfirmed, false, true));
        assert!(!may_install_update(VerifiedTaskState::IdleConfirmed, true, false));
    }
    #[test]
    fn compares_numeric_versions_without_lexical_ordering() {
        assert!(numeric_version("v1.10.0") > numeric_version("1.9.9"));
        assert_eq!(numeric_version("v0.1.0"), Some([0, 1, 0]));
    }
    #[test]
    fn accepts_worker_tag_versions() {
        assert_eq!(numeric_version("worker-v1.2.3".strip_prefix("worker-v").unwrap()), Some([1, 2, 3]));
    }
    #[test]
    fn rejects_untrusted_or_prerelease_tags() {
        for v in ["", "latest", "1.2", "1.2.3-beta", "1.2.3+meta", "1.2.3/evil", "1..3"] {
            assert!(numeric_version(v).is_none(), "{v}");
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![worker_version, worker_status, inspect_phone_local_model, store_worker_token, has_saved_worker_token, worker_token_state, forget_worker_token, verify_worker_connection, preview_worker_job, check_worker_release, download_worker_apk, verify_downloaded_worker_apk, check_update_safety, update_recovery_status, inspect_update_hold, inspect_android_install_sessions, poll_worker_update_pause, update_installation_readiness, install_downloaded_worker_apk, reconcile_installed_worker_update, inspect_android_install_result, inspect_worker_resume_readiness, get_my_worker_board, poll_my_inference_job, execute_phone_inference_once, phone_inference_supported, reconcile_idle_phone_after_update])
        .run(tauri::generate_context!())
        .expect("failed to run remote worker GUI");
}
