"""Native on-phone (Termux llama.cpp) inference regression and fail-closed UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = (ROOT / "remote_worker_gui/src-tauri/src/android_local_llama.rs").read_text()
LIB = (ROOT / "remote_worker_gui/src-tauri/src/lib.rs").read_text()
COORD = (ROOT / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text()
UI = (ROOT / "remote_worker_gui/public/index.html").read_text()
DELIVERY = (ROOT / "remote_worker_inference_delivery.py").read_text()


def test_on_device_computation_never_uses_arena_or_arbitrary_model_endpoint():
    assert 'const LOCAL_SERVER: &str = "http://127.0.0.1:8088"' in LOCAL
    assert 'format!("{LOCAL_SERVER}/completion")' in LOCAL
    assert 'format!("{LOCAL_SERVER}/v1/models")' in LOCAL
    assert '.no_proxy()' in LOCAL
    assert "http://0.0.0.0" not in LOCAL
    assert 'fn model_alias_matches(models: &Value, expected: &str)' in LOCAL
    assert 'data.len() == 1' in LOCAL
    assert "NOT cryptographic verification" in LOCAL or "NOT cryptographic" in LOCAL


def test_phone_inference_receives_own_job_and_checks_lease_after_generation():
    assert 'async fn execute_phone_inference_once(' in LIB
    assert 'cfg(not(target_os = "android"))' in LIB
    assert 'android_local_llama::acquire_generation()?' in LIB
    assert 'android_local_llama::infer_local_uci(job).await?' in LIB
    assert LIB.count('android_update_coordinator::fetch_my_inference_job(') >= 2
    assert 'again.get("job") != Some(job)' in LIB
    assert 'android_update_recovery::load_pending()?.is_some()' in LIB
    assert 'android_update_coordinator::submit_my_inference_move(' in LIB
    assert 'submit_my_inference_move(' in COORD


def test_uci_format_and_model_alias_have_rust_unit_tests():
    assert "fn only_exact_uci_is_accepted()" in LOCAL
    assert "fn model_routing_alias_must_match_exactly()" in LOCAL
    assert "parse_exact_uci_answer(generated)" in LOCAL
    assert 'b"a"' not in LOCAL or "sha256" in LOCAL


def test_phone_start_pause_stop_are_honest_and_foreground_only():
    assert 'invoke("execute_phone_inference_once"' not in UI
    assert "invoke('execute_phone_inference_once'" in UI
    assert "invoke('phone_inference_supported')" in UI
    assert 'let phoneWorkerEnabled=false' in UI
    assert 'if(!phoneWorkerEnabled||phoneWorkerRunning||document.hidden' in UI
    assert "phoneWorkerEnabled=false" in UI
    assert "Nowe zadania wstrzymane." in UI or "Nowe zadania na telefonie zostały wstrzymane." in UI
    assert "Lokalna inferencja zatrzymana:" in UI
    assert "Start lokalnie" in UI
    assert "Pauza po ruchu" in UI


def test_local_model_probe_and_ready_auth_are_required_before_dispatch():
    assert "pub(crate) async fn probe_local_model_alias()" in LOCAL
    assert 'format!("{LOCAL_SERVER}/v1/models")' in LOCAL
    assert 'let model_alias = android_local_llama::probe_local_model_alias().await?' in LIB
    assert 'android_update_coordinator::announce_phone_ready(' in LIB
    assert 'async fn announce_phone_ready(' in COORD
    assert 'register_local_ready(' in DELIVERY


def test_results_once_submitted_are_not_recomputed():
    assert 'SELECT 1 FROM worker_inference_results' in DELIVERY
    assert '"state": "awaiting_arena"' in DELIVERY
    assert '"awaiting_arena"' in COORD


def test_phone_model_is_restricted_to_arenas_canonical_legal_moves():
    assert 'job.get("legal_moves").and_then(Value::as_array)' in LOCAL
    assert '"Legal UCI moves:' in LOCAL
    assert 'request["grammar"] = json!(grammar)' in LOCAL
    assert 'legal_moves.contains(&proposed.as_str())' in LOCAL
    assert 'matches!((version, job.len()), (1, 6) | (2, 7))' in COORD
    assert 'let legal = job.get("legal_moves").and_then(Value::as_array)' in COORD
