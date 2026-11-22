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
    let response = client.get("https://api.github.com/repos/MarVinPL/ai-chess-arena/releases/latest")
        .header(reqwest::header::USER_AGENT, "ai-chess-arena-worker-update-check")
        .send().await.map_err(|_| "Nie udało się sprawdzić wydań")?;
    if response.status() == reqwest::StatusCode::NOT_FOUND {
        return Ok(ReleaseInfo { current_version: current, latest_version: None, update_available: false, release_notes_url: None });
    }
    if !response.status().is_success() { return Err("GitHub nie udostępnił informacji o wydaniu".into()); }
    let data: serde_json::Value = response.json().await.map_err(|_| "Nieprawidłowe metadane wydania")?;
    let tag = data.get("tag_name").and_then(|v| v.as_str()).ok_or("Brak numeru wersji wydania")?;
    let latest = numeric_version(tag).ok_or("Nieobsługiwany format wersji wydania")?;
    let installed = numeric_version(&current).ok_or("Nieprawidłowa wersja aplikacji")?;
    let notes = data.get("html_url").and_then(|v| v.as_str())
        .filter(|url| url.starts_with("https://github.com/MarVinPL/ai-chess-arena/releases/tag/"))
        .map(str::to_owned);
    Ok(ReleaseInfo { current_version: current, latest_version: Some(tag.to_owned()), update_available: latest > installed, release_notes_url: notes })
}

#[cfg(test)]
mod version_tests {
    use super::numeric_version;
    #[test]
    fn compares_numeric_versions_without_lexical_ordering() {
        assert!(numeric_version("v1.10.0") > numeric_version("1.9.9"));
        assert_eq!(numeric_version("v0.1.0"), Some([0, 1, 0]));
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
        .invoke_handler(tauri::generate_handler![worker_status, store_worker_token, has_saved_worker_token, forget_worker_token, verify_worker_connection, check_worker_release])
        .run(tauri::generate_context!())
        .expect("failed to run remote worker GUI");
}
