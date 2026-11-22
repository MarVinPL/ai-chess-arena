//! Experimental on-device inference adapter for an existing llama.cpp server
//! running on THIS Android phone (e.g. Termux). It is never an Arena/VPS
//! inference service. The address is fixed to loopback and cannot be changed
//! by webview input or remote jobs.
//!
//! Important: a llama-server --alias equal to model SHA-256 is a routing
//! check, NOT cryptographic verification of actual GGUF bytes. Production
//! acceptance still needs an app-owned verified model/runtime.
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use serde_json::{json, Value};

const LOCAL_SERVER: &str = "http://127.0.0.1:8088";
static GENERATION_ACTIVE: AtomicBool = AtomicBool::new(false);

pub(crate) struct LocalGenerationPermit;

impl Drop for LocalGenerationPermit {
    fn drop(&mut self) {
        GENERATION_ACTIVE.store(false, Ordering::SeqCst);
    }
}

pub(crate) fn acquire_generation() -> Result<LocalGenerationPermit, String> {
    GENERATION_ACTIVE
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .map_err(|_| "Telefon wykonuje już obliczenia dla tego Workera".to_owned())?;
    Ok(LocalGenerationPermit)
}

pub(crate) fn generation_active() -> bool {
    GENERATION_ACTIVE.load(Ordering::SeqCst)
}

fn valid_hex_sha256(digest: &str) -> bool {
    digest.len() == 64
        && digest.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn model_alias_matches(models: &Value, expected: &str) -> bool {
    if !valid_hex_sha256(expected) {
        return false;
    }
    let Some(data) = models.get("data").and_then(Value::as_array) else {
        return false;
    };
    // A single-server GGUF model must unambiguously advertise its SHA alias.
    data.len() == 1 && data[0].get("id").and_then(Value::as_str)
        .is_some_and(|id| id == expected || id == format!("sha256:{expected}"))
}

/// Advertise local readiness only if one llama.cpp model has a strict SHA
/// alias. The alias is not a cryptographic hash attestation of GGUF bytes.
pub(crate) async fn probe_local_model_alias() -> Result<String, String> {
    let client = reqwest::Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(3))
        .timeout(Duration::from_secs(5))
        .build().map_err(|_| "Brak klienta lokalnego modelu".to_owned())?;
    let response = client.get(format!("{LOCAL_SERVER}/v1/models"))
        .send().await.map_err(|_| "Uruchom llama.cpp na telefonie (127.0.0.1:8088)")?;
    if !response.status().is_success() {
        return Err("Lokalny model na telefonie nie jest dostępny".into());
    }
    let body = response.bytes().await
        .map_err(|_| "Nie można odczytać modelu telefonu".to_owned())?;
    if body.len() > 64 * 1024 {
        return Err("Zbyt duża lista modeli llama.cpp".into());
    }
    let models: Value = serde_json::from_slice(&body)
        .map_err(|_| "Nieprawidłowa lista modeli llama.cpp".to_owned())?;
    let items = models.get("data").and_then(Value::as_array)
        .ok_or("Lokalny serwer nie podał listy modeli")?;
    if items.len() != 1 {
        return Err("Wymagany dokładnie jeden lokalnie uruchomiony model".into());
    }
    let id = items[0].get("id").and_then(Value::as_str)
        .ok_or("Lokalny model nie ma aliasu SHA-256")?;
    let alias = id.strip_prefix("sha256:").unwrap_or(id);
    if !valid_hex_sha256(alias) {
        return Err("Uruchom llama-server z aliasem SHA-256 modelu".into());
    }
    Ok(alias.to_owned())
}

pub(crate) fn parse_exact_uci_answer(content: &str) -> Result<String, String> {
    let answer = content.trim();
    let b = answer.as_bytes();
    if !(b.len() == 4 || b.len() == 5)
        || !(b'a'..=b'h').contains(&b[0])
        || !(b'1'..=b'8').contains(&b[1])
        || !(b'a'..=b'h').contains(&b[2])
        || !(b'1'..=b'8').contains(&b[3])
        || (b.len() == 5 && !matches!(b[4], b'q' | b'r' | b'b' | b'n'))
    {
        return Err("Model nie zwrócił jednoznacznego ruchu UCI".into());
    }
    // Move legality is checked by Arena, never inferred from the model text.
    Ok(answer.to_owned())
}

pub(crate) async fn infer_local_uci(job: &Value) -> Result<String, String> {
    let expected = job.get("model_sha256").and_then(Value::as_str)
        .ok_or("Brak SHA-256 wymaganego modelu")?;
    let fen = job.get("fen").and_then(Value::as_str)
        .ok_or("Brak pozycji FEN")?;
    let timeout = job.get("timeout_seconds").and_then(Value::as_u64)
        .ok_or("Brak limitu czasu")?;
    let tokens = job.get("max_tokens").and_then(Value::as_u64)
        .ok_or("Brak limitu tokenów")?;
    if !valid_hex_sha256(expected) || fen.len() > 120 || fen.split(' ').count() != 6
        || !fen.bytes().all(|b|
            b.is_ascii_alphanumeric() || matches!(b, b'/' | b' ' | b'-'))
        || !(1..=600).contains(&timeout) || !(1..=1024).contains(&tokens) {
        return Err("Nieprawidłowe dane zadania inferencji".into());
    }
    let client = reqwest::Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(3))
        .timeout(Duration::from_secs(timeout))
        .build()
        .map_err(|_| "Brak klienta lokalnego silnika".to_owned())?;

    // A model swap is detectable by the advertised alias. This still is NOT
    // a file digest check: do not equate it with trusted GGUF verification.
    let models_response = client.get(format!("{LOCAL_SERVER}/v1/models"))
        .send().await.map_err(|_| "Brak llama.cpp na tym telefonie (127.0.0.1:8088)")?;
    if !models_response.status().is_success() {
        return Err("Lokalny llama.cpp nie potwierdził załadowanego modelu".into());
    }
    let models_bytes = models_response.bytes().await
        .map_err(|_| "Brak listy lokalnych modeli".to_owned())?;
    if models_bytes.len() > 64 * 1024 {
        return Err("Zbyt duża odpowiedź lokalnego silnika".into());
    }
    let models: Value = serde_json::from_slice(&models_bytes)
        .map_err(|_| "Nieprawidłowa lista modeli llama.cpp".to_owned())?;
    if !model_alias_matches(&models, expected) {
        return Err("Załadowany lokalny model nie zgadza się z SHA-256 przydziału (alias)".into());
    }
    // No uploaded checkpoints, board changes or user-provided local paths.
    // Arena alone validates legality and records the game.
    let prompt = format!(
        "You are an AI playing chess. Reply with exactly one move in UCI notation (e.g. e2e4 or a7a8q), without explanation, markup or punctuation.\n\
         Position FEN: {fen}\nUCI move:"
    );
    let response = client.post(format!("{LOCAL_SERVER}/completion"))
        .json(&json!({
            "prompt": prompt,
            "n_predict": tokens.min(64),
            "temperature": 0.0,
            "stream": false,
            "stop": ["\n"],
        }))
        .send().await.map_err(|_| "Lokalna inferencja nie zakończyła się w limicie")?;
    if !response.status().is_success() {
        return Err("Silnik telefonu odrzucił żądanie inferencji".into());
    }
    let bytes = response.bytes().await
        .map_err(|_| "Nie można odczytać ruchu modelu".to_owned())?;
    if bytes.len() > 64 * 1024 {
        return Err("Zbyt duża odpowiedź lokalnego modelu".into());
    }
    let result: Value = serde_json::from_slice(&bytes)
        .map_err(|_| "Silnik zwrócił nieprawidłowy JSON".to_owned())?;
    let generated = result.get("content").and_then(Value::as_str)
        .ok_or("Silnik nie zwrócił tekstu ruchu")?;
    parse_exact_uci_answer(generated)
}

#[cfg(test)]
mod tests {
    use super::{model_alias_matches, parse_exact_uci_answer};
    use serde_json::json;

    #[test]
    fn only_exact_uci_is_accepted() {
        for move_uci in ["e2e4", "g1f3", "a7a8q", "a2a1n"] {
            assert_eq!(parse_exact_uci_answer(move_uci).unwrap(), move_uci);
        }
        for bad in ["e2e4\nmore", "Move: e2e4", "e2e4.", "e9e4",
                    "e2e4q\nb2b4", "a7a8k", "", "e2e4 e7e5"] {
            assert!(parse_exact_uci_answer(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn model_routing_alias_must_match_exactly() {
        let sha = "a".repeat(64);
        assert!(model_alias_matches(&json!({"data":[{"id":sha}]}), &sha));
        assert!(model_alias_matches(&json!({"data":[{"id":format!("sha256:{sha}")}]}), &sha));
        assert!(!model_alias_matches(&json!({"data":[{"id":"some-other-model"}]}), &sha));
        assert!(!model_alias_matches(&json!({"data":[{"id":sha},{"id":"other"}]}), &sha));
    }
}
