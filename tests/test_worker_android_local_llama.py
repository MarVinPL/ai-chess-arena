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
    assert 'if(!phonePaired||!phoneWorkerEnabled||phoneWorkerRunning||document.hidden' in UI
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
    assert "validated.legal_moves.as_ref()" in LOCAL
    assert "crate::validate_pending_worker_job(&validated)" in LOCAL
    assert "moves.windows(2).all" in LIB
    assert 'Legal UCI moves:' in LOCAL
    assert 'request["grammar"] = json!(grammar)' in LOCAL
    assert 'legal_moves.contains(&proposed.as_str())' in LOCAL
    assert 'matches!((version, job.len()), (1, 6) | (2, 7) | (3, 8) | (4, 9))' in COORD
    assert 'let legal = job.get("legal_moves").and_then(Value::as_array)' in COORD
    assert 'job.get("lease_token").and_then(Value::as_str)' in COORD
    assert 'return Ok(serde_json::json!({"state": "blocked"}));' in COORD


def test_real_phone_inference_checks_authenticated_stop_at_both_boundaries():
    assert 'async fn check_phone_stop_gate(' in LIB
    assert 'android_update_coordinator::poll_pause_instruction(' in LIB
    assert 'StopInstruction::StopCheckpoint { job_id: requested, .. }' in LIB
    assert 'StopInstruction::AwaitReconciliation | StopInstruction::Blocked' in LIB
    assert LIB.count('check_phone_stop_gate(&server, &worker_id, &token, job_id).await?') == 2
    block = LIB.split('async fn execute_phone_inference_once(', 1)[1].split(
        '/// Arena is the sole owner of the chessboard', 1
    )[0]
    first = block.index('check_phone_stop_gate(&server, &worker_id, &token, job_id).await?')
    infer = block.index('android_local_llama::infer_local_uci(job).await?')
    second = block.rindex('check_phone_stop_gate(&server, &worker_id, &token, job_id).await?')
    submit = block.index('android_update_coordinator::submit_my_inference_move(')
    assert first < infer < second < submit
    assert 'android_local_llama::generation_active()' in LIB
    install = LIB.split('async fn install_downloaded_worker_apk(', 1)[1].split(
        '/// Inspect only the job that Arena', 1
    )[0]
    assert 'generation_active()' in install
    assert 'Telefon aktualnie oblicza ruch' in install


def test_android_diagnostic_is_bounded_and_read_only():
    probe = LOCAL.split('async fn inspect_model_alias()', 1)[1].split('fn parse_model_alias(', 1)[0]
    assert '.no_proxy()' in probe
    assert '.redirect(reqwest::redirect::Policy::none())' in probe
    assert 'response.chunk().await' in probe
    assert 'chunk.len() > LIMIT - body.len()' in probe
    assert 'response.bytes()' not in probe
    section = LIB.split('async fn inspect_phone_local_model()', 1)[1].split(
        '// The desktop app connects outbound only', 1)[0]
    assert 'diagnostic_model_status().await' in section
    assert 'announce_phone_ready' not in section
    assert 'inspect_phone_local_model,' in LIB
    assert 'LOCAL_OPERATION_ACTIVE' in LOCAL
    assert 'diagnostic_and_generation_exclude_each_other_without_false_running' in LOCAL


def test_android_diagnostic_foreground_and_distinct_statuses():
    section = UI.split('async function checkPhoneLocalModel(', 1)[1].split(
        'const phoneStartButton', 1)[0]
    assert 'document.hidden' in section
    assert 'phoneWorkerEnabled' in section
    assert "invoke('phone_inference_supported')" in section
    assert "invoke('inspect_phone_local_model')" in section
    assert 'Date.now()-localModelLastCheck<30000' in section
    assert "result?.state==='incompatible'" in section
    assert "result?.state==='busy'" in section
    assert 'result.alias.slice(0,12)' in section
    assert 'activatePhoneJobPolling' not in section
    assert 'nie potwierdza integralności pliku GGUF' in UI
