"""Regression guards for bounded untrusted HTTPS response parsing in Worker."""
from pathlib import Path
import re

SOURCE = (Path(__file__).resolve().parents[1] /
          "remote_worker_gui/src-tauri/src/lib.rs")


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"async fn {name}\b", source)
    assert match, f"Missing Rust function: {name}"
    end = source.find("\n}", match.end())
    assert end != -1
    return source[match.start():end]


def test_bounded_reader_checks_claimed_and_actual_bytes_before_json():
    text = SOURCE.read_text(encoding="utf-8")
    body = _function_body(text, "bounded_json_response")
    assert "response.content_length()" in body
    assert "n > limit as u64" in body
    assert "while let Some(chunk) = response.chunk().await" in body
    assert "chunk.len() > limit - bytes.len()" in body
    assert body.index("chunk.len() > limit - bytes.len()") < body.index("bytes.extend_from_slice(&chunk)")
    assert body.index("bytes.extend_from_slice(&chunk)") < body.index("serde_json::from_slice(&bytes)")


def test_worker_update_safety_json_is_bounded():
    source = SOURCE.read_text(encoding="utf-8")
    body = _function_body(source, "check_update_safety")
    assert "bounded_json_response(response, 4096).await?" in body
    assert ".json().await" not in body
    assert 'fresh_authenticated_idle(&data, &worker_id, &nonce, now)' in body
    assert 'Ok("unknown".into())' in body
    assert 'if state == "running"' in body
    coordinator = (SOURCE.parent / "android_update_coordinator.rs").read_text()
    for key in ('"authenticated"', '"source"', '"worker_id"', '"nonce"', '"active_tasks"', '"checked_at_unix_ms"'):
        assert key in coordinator.split('fn fresh_authenticated_idle(', 1)[1].split('\n}', 1)[0]


def test_worker_release_metadata_json_is_bounded():
    source = SOURCE.read_text(encoding="utf-8")
    body = _function_body(source, "check_worker_release")
    fetch = _function_body(source, "fetch_worker_releases")
    assert "bounded_json_response(response, 1024 * 1024).await" in fetch
    assert "fetch_worker_releases().await?" in body
    assert ".json().await" not in fetch
    assert ".json().await" not in body
    assert "newest_worker_release(&releases, channel)" in body


def test_all_native_coordinator_json_replies_are_bounded():
    coordinator = (SOURCE.parent / "android_update_coordinator.rs").read_text()
    assert not re.search(r"\.json\(\)\.await", coordinator)
    limits = {
        "request_journal_init": "4096",
        "complete_journal_init": "4096",
        "reserve_update": "4096",
        "inspect_pending_update": "4096",
        "release_update": "4096",
        "announce_phone_ready": "4096",
        "submit_my_inference_move": "4096",
        "fetch_native_inference_job": "32 * 1024",
        "fetch_my_approved_gguf": "4096",
        "fetch_worker_board_view": "4096",
        "fetch_resume_status": "4096",
        "poll_pause_instruction": "4096",
        "report_native_stop": "4096",
    }
    assert coordinator.count("crate::bounded_json_response(") == len(limits)
    for name, limit in limits.items():
        body = _function_body(coordinator, name)
        assert re.search(rf"crate::bounded_json_response\((?:response|reply), {re.escape(limit)}\)\.await", body), name
