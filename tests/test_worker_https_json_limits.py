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
    assert coordinator.count("crate::bounded_json_response(") == 9
    assert "crate::bounded_json_response(response, 32 * 1024).await" in coordinator
    assert coordinator.count(", 4096).await") == 8
