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
    assert '"idle_confirmed" | "running" | "unknown"' in body


def test_worker_release_metadata_json_is_bounded():
    source = SOURCE.read_text(encoding="utf-8")
    body = _function_body(source, "check_worker_release")
    assert "bounded_json_response(response, 1024 * 1024).await" in body
    assert ".json().await" not in body
    assert "newest_worker_release(releases, channel)" in body
