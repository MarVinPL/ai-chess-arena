use serde::Serialize;
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
    let response = client.post(endpoint).json(&serde_json::json!({
        "worker_id": worker_id, "token": token
    })).send().await.map_err(|_| "Nie udało się połączyć przez HTTPS")?;
    if response.status() == reqwest::StatusCode::UNAUTHORIZED { return Ok(false); }
    if !response.status().is_success() { return Err("Serwer odrzucił żądanie".into()); }
    let result: serde_json::Value = response.json().await
        .map_err(|_| "Nieprawidłowa odpowiedź serwera")?;
    Ok(result.get("authenticated") == Some(&serde_json::Value::Bool(true)))
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

#[tauri::command]
async fn check_worker_release() -> Result<ReleaseInfo, String> {
    let current = env!("CARGO_PKG_VERSION").to_string();
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(8))
        .build().map_err(|_| "Błąd klienta HTTPS")?;
    // The repository also publishes non-Worker releases. Inspect a bounded
    // list rather than GitHub's /latest, which may belong to the server.
    let response = client.get("https://api.github.com/repos/MarVinPL/ai-chess-arena/releases?per_page=100")
        .header(reqwest::header::USER_AGENT, "ai-chess-arena-worker-update-check")
        .send().await.map_err(|_| "Nie udało się sprawdzić wydań")?;
    if !response.status().is_success() { return Err("GitHub nie udostępnił informacji o wydaniach".into()); }
    let data: serde_json::Value = response.json().await.map_err(|_| "Nieprawidłowe metadane wydań")?;
    let releases = data.as_array().ok_or("Nieprawidłowa lista wydań")?;
    let installed = numeric_version(&current).ok_or("Nieprawidłowa wersja aplikacji")?;
    let candidate = releases.iter()
        .filter(|release| release.get("draft").and_then(|v| v.as_bool()) == Some(false)
            && release.get("prerelease").and_then(|v| v.as_bool()) == Some(false))
        .filter_map(|release| {
            let tag = release.get("tag_name")?.as_str()?;
            let version = numeric_version(tag.strip_prefix("worker-v")?)?;
            Some((version, tag, release))
        })
        .max_by_key(|(version, _, _)| *version);
    let Some((latest, tag, data)) = candidate else {
        return Ok(ReleaseInfo { current_version: current, latest_version: None, update_available: false, release_notes_url: None });
    };
    let notes = data.get("html_url").and_then(|v| v.as_str())
        .and_then(|raw| reqwest::Url::parse(raw).ok())
        .filter(|url| url.scheme() == "https" && url.host_str() == Some("github.com")
            && url.path().starts_with("/MarVinPL/ai-chess-arena/releases/tag/worker-v")
            && url.query().is_none() && url.fragment().is_none()
            && url.username().is_empty() && url.password().is_none())
        .map(|url| url.to_string());
    Ok(ReleaseInfo { current_version: current, latest_version: Some(tag.to_owned()), update_available: latest > installed, release_notes_url: notes })
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
    use super::{numeric_version, may_install_update, update_installation_readiness, VerifiedTaskState};
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
        .invoke_handler(tauri::generate_handler![worker_version, worker_status, store_worker_token, has_saved_worker_token, forget_worker_token, verify_worker_connection, check_worker_release, update_installation_readiness])
        .run(tauri::generate_context!())
        .expect("failed to run remote worker GUI");
}
