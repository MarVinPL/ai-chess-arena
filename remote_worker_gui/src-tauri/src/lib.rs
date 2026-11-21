use serde::Serialize;

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![worker_status])
        .run(tauri::generate_context!())
        .expect("failed to run remote worker GUI");
}
