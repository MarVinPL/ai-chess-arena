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
#[cfg(target_os = "android")]
mod android_update_package_manager;
#[cfg(target_os = "android")]
mod android_update_package_installer;
mod android_update_coordinator;
mod android_update_recovery;
mod android_update_install_journal;
mod android_local_llama;
#[allow(dead_code)]
mod android_update_install_state;

use serde::Serialize;
use std::time::Duration;

const TOKEN_SERVICE: &str = "pl.marvin.aichessarena.worker";
const TOKEN_ACCOUNT: &str = "worker-token";

// A pending coordinator hold requires the *original* machine token for
// explicit authenticated release. Never let webview rotation/deletion orphan it.
fn ensure_no_pending_update() -> Result<(), String> {
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
    let machine_token = token_entry()?.get_password()
        .map_err(|_| "Brak oryginalnego tokenu Workera".to_string())?;
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
    let token = token_entry()?.get_password()
        .map_err(|_| "Brak zapisanego tokenu Workera".to_string())?;
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

fn token_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(TOKEN_SERVICE, TOKEN_ACCOUNT)
        .map_err(|_| "Systemowy magazyn poświadczeń jest niedostępny".to_string())
}

#[tauri::command]
fn store_worker_token(token: String) -> Result<(), String> {
    ensure_no_pending_update()?;
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
    ensure_no_pending_update()?;
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
    let nonce = android_update_coordinator::random_nonce()?;
    let response = client.post(endpoint).json(&serde_json::json!({
        "worker_id": worker_id, "token": token, "nonce": nonce
    })).send().await.map_err(|_| "Nie udało się sprawdzić stanu zadań")?;
    if !response.status().is_success() { return Err("Nie udało się potwierdzić stanu zadań".into()); }
    let data: serde_json::Value = response.json().await.map_err(|_| "Nieprawidłowa odpowiedź serwera")?;
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

// Select only stable releases belonging to the Worker, not Arena server.
fn newest_worker_release(releases: &[serde_json::Value]) -> Option<([u64; 3], &str, &serde_json::Value)> {
    releases.iter()
        .filter(|release| release.get("draft").and_then(|v| v.as_bool()) == Some(false)
            && release.get("prerelease").and_then(|v| v.as_bool()) == Some(false))
        .filter_map(|release| {
            let tag = release.get("tag_name")?.as_str()?;
            let version = numeric_version(tag.strip_prefix("worker-v")?)?;
            Some((version, tag, release))
        })
        .max_by_key(|(version, _, _)| *version)
}

// The same bounded GitHub fetch is used for display and download decisions.
// JavaScript cannot provide a release URL, asset digest or destination path.
async fn fetch_worker_releases() -> Result<Vec<serde_json::Value>, String> {
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
    // Read bounded GitHub metadata; reject oversized bodies before parsing.
    let mut response = response;
    const MAX_RELEASE_JSON: usize = 1024 * 1024;
    if response.content_length().map(|length| length > MAX_RELEASE_JSON as u64).unwrap_or(false) {
        return Err("Zbyt duże metadane wydań".into());
    }
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "Błąd odczytu wydań")? {
        if chunk.len() > MAX_RELEASE_JSON - body.len() {
            return Err("Zbyt duże metadane wydań".into());
        }
        body.extend_from_slice(&chunk);
    }
    let data: serde_json::Value = serde_json::from_slice(&body)
        .map_err(|_| "Nieprawidłowe metadane wydań")?;
    let releases = data.as_array().ok_or("Nieprawidłowa lista wydań")?;
    Ok(releases.clone())
}

#[tauri::command]
async fn check_worker_release() -> Result<ReleaseInfo, String> {
    let current = env!("CARGO_PKG_VERSION").to_string();
    let releases = fetch_worker_releases().await?;
    let installed = numeric_version(&current).ok_or("Nieprawidłowa wersja aplikacji")?;
    let candidate = newest_worker_release(&releases);
    let Some((latest, tag, data)) = candidate else {
        return Ok(ReleaseInfo { current_version: current, latest_version: None, update_available: false, release_notes_url: None, apk_asset_available: false });
    };
    let notes = data.get("html_url").and_then(|v| v.as_str())
        .and_then(|raw| reqwest::Url::parse(raw).ok())
        .filter(|url| url.scheme() == "https" && url.host_str() == Some("github.com")
            && url.path().starts_with("/MarVinPL/ai-chess-arena/releases/tag/worker-v")
            && url.query().is_none() && url.fragment().is_none()
            && url.username().is_empty() && url.password().is_none())
        .map(|url| url.to_string());
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
        let (latest, tag, release) = newest_worker_release(&releases)
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
        let (latest, tag, release) = newest_worker_release(&releases)
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
        // Never turn a failed/unknown coordinator response into an idle state.
        // The server's reserve transaction rechecks state atomically.
        let state = check_update_safety(server.clone(), worker_id.clone()).await?;
        if state != "idle_confirmed" {
            return Err("Najpierw bezpiecznie przerwij zadanie Workera; brak potwierdzenia STOP".into());
        }
        let releases = fetch_worker_releases().await?;
        let (_, tag, release) = newest_worker_release(&releases)
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
        let machine_token = token_entry()?.get_password()
            .map_err(|_| "Brak tokenu Workera".to_string())?;
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
    let machine_token = token_entry()?.get_password()
        .map_err(|_| "Brak zapisanego tokenu Workera".to_owned())?;
    android_update_coordinator::fetch_my_inference_job(
        &server, &worker_id, &machine_token,
    ).await
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
        let token = token_entry()?.get_password()
            .map_err(|_| "Brak oryginalnego tokenu Workera".to_owned())?;
        let assignment = android_update_coordinator::fetch_my_inference_job(
            &server, &worker_id, &token,
        ).await?;
        let state = assignment.get("state").and_then(serde_json::Value::as_str)
            .ok_or("Brak stanu przydziału Areny")?;
        if state != "assigned" {
            return Ok(serde_json::json!({"state": state}));
        }
        let job = assignment.get("job")
            .ok_or("Brak zadania obliczeń")?;
        let job_id = job.get("job_id").and_then(serde_json::Value::as_str)
            .ok_or("Brak identyfikatora zadania")?;
        let uci = android_local_llama::infer_local_uci(job).await?;
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
    let machine_token = token_entry()?.get_password()
        .map_err(|_| "Brak zapisanego tokenu Workera".to_owned())?;
    android_update_coordinator::fetch_worker_board_view(
        &server, &worker_id, &machine_token,
    ).await
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
    let token = token_entry()?.get_password()
        .map_err(|_| "Brak tokenu Workera".to_owned())?;
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
            let machine_token = token_entry()?.get_password()
                .map_err(|_| "Brak poprzedniego tokenu Workera".to_string())?;
            android_update_coordinator::release_update(
                &pending.server, &pending.worker_id, &machine_token,
            ).await?;
            android_update_install_journal::clear_confirmed(&journal)?;
            return Ok("installation_cancelled");
        }
        let machine_token = token_entry()?.get_password()
            .map_err(|_| "Brak poprzedniego tokenu Workera".to_string())?;
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
    use super::{numeric_version, newest_worker_release, may_install_update, update_installation_readiness, VerifiedTaskState};
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
        let selected = newest_worker_release(&releases).unwrap();
        assert_eq!(selected.1, "worker-v1.10.0");
        assert_eq!(selected.0, [1,10,0]);
    }
    #[test]
    fn no_eligible_release_means_no_update() {
        let releases = vec![json!({"tag_name":"worker-v2.0.0","draft":true,"prerelease":false}), json!({"tag_name":"server-v9.0.0","draft":false,"prerelease":false})];
        assert!(newest_worker_release(&releases).is_none());
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
        .invoke_handler(tauri::generate_handler![worker_version, worker_status, store_worker_token, has_saved_worker_token, forget_worker_token, verify_worker_connection, check_worker_release, download_worker_apk, verify_downloaded_worker_apk, check_update_safety, update_recovery_status, inspect_update_hold, inspect_android_install_sessions, poll_worker_update_pause, update_installation_readiness, install_downloaded_worker_apk, reconcile_installed_worker_update, inspect_android_install_result, inspect_worker_resume_readiness, get_my_worker_board, poll_my_inference_job, execute_phone_inference_once, phone_inference_supported])
        .run(tauri::generate_context!())
        .expect("failed to run remote worker GUI");
}
