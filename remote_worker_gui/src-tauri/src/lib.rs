use serde::{Deserialize, Serialize};
use std::time::Duration;

const TOKEN_SERVICE: &str = "pl.marvin.aichessarena.worker";
const TOKEN_ACCOUNT: &str = "worker-token";

fn token_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(TOKEN_SERVICE, TOKEN_ACCOUNT)
        .map_err(|_| "Systemowy magazyn poświadczeń jest niedostępny".to_string())
}

#[tauri::command]
fn store_worker_token(token: String) -> Result<(), String> {
    // Never return, log, or serialize the secret back to the frontend.
    if token.len() < 24 || token.len() > 512 || !token.is_ascii()
        || !token.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') {
        return Err("Nieprawidłowy format tokenu".to_string());
    }
    token_entry()?.set_password(&token)
        .map_err(|_| "Nie udało się zapisać tokenu w systemowym magazynie".to_string())
}

#[tauri::command]
fn has_saved_worker_token() -> bool {
    token_entry().and_then(|entry| entry.get_password()
        .map_err(|_| "Brak zapisanego tokenu".to_string())).is_ok()
}

#[tauri::command]
fn forget_worker_token() -> Result<(), String> {
    let entry = token_entry()?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err("Nie udało się usunąć tokenu".to_string()),
    }
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

// Local-only status command. No remote network connection, shell execution,
// filesystem access, or model inference is available in this prototype.
#[tauri::command]
fn worker_status() -> WorkerStatus {
    WorkerStatus {
        connected: false,
        running: false,
        message: "Agent nie jest jeszcze połączony z VPS",
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
    let token = token_entry()?.get_password().map_err(|_| "Brak zapisanego tokenu")?;
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
    let token = token_entry()?.get_password().map_err(|_| "Brak zapisanego tokenu")?;
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
    let token = token_entry()?.get_password().map_err(|_| "Brak zapisanego tokenu")?;
    let client = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(12)).build().map_err(|_| "Błąd klienta HTTPS")?;
    let endpoint = url.join("api/remote-workers/update-safety")
        .map_err(|_| "Nieprawidłowy endpoint")?;
    let response = client.post(endpoint).json(&serde_json::json!({
        "worker_id": worker_id, "token": token
    })).send().await.map_err(|_| "Nie udało się sprawdzić stanu zadań")?;
    if !response.status().is_success() { return Err("Nie udało się potwierdzić stanu zadań".into()); }
    let data: serde_json::Value = bounded_json_response(response, 4096).await?;
    let state = data.get("state").and_then(|v| v.as_str()).unwrap_or("unknown");
    // This command does not authorize installation; unknown fields fail closed.
    match state {
        "idle_confirmed" | "running" | "unknown" => Ok(state.to_string()),
        _ => Ok("unknown".into()),
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

#[tauri::command]
async fn check_worker_release(channel: String) -> Result<ReleaseInfo, String> {
    let channel = WorkerReleaseChannel::parse(&channel)?;
    let current = env!("CARGO_PKG_VERSION").to_string();
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
    let installed = numeric_version(&current).ok_or("Nieprawidłowa wersja aplikacji")?;
    let candidate = newest_worker_release(releases, channel);
    let Some((latest, tag, data)) = candidate else {
        return Ok(ReleaseInfo {
            current_version: current, latest_version: None,
            update_available: false, release_notes_url: None
        });
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
    // A prerelease with the same base version is visible as a channel
    // release, NOT falsely advertised as a newer installable Android APK.
    Ok(ReleaseInfo {
        current_version: current, latest_version: Some(tag.to_owned()),
        update_available: latest > installed, release_notes_url: notes
    })
}

// Fail closed: a missing coordinator confirmation is not an idle worker.
// The scheduler transport must provide an authenticated, fresh idle state.

#[allow(dead_code)] // Running is reserved for authenticated scheduler state integration.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum VerifiedTaskState {
    IdleConfirmed,
    Running,
    Unknown,
}

fn may_install_update(state: VerifiedTaskState, signature_verified: bool, newer_version: bool) -> bool {
    state == VerifiedTaskState::IdleConfirmed && signature_verified && newer_version
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
        .invoke_handler(tauri::generate_handler![worker_version, worker_status, store_worker_token, has_saved_worker_token, forget_worker_token, verify_worker_connection, preview_worker_job, check_worker_release, check_update_safety, update_installation_readiness])
        .run(tauri::generate_context!())
        .expect("failed to run remote worker GUI");
}
