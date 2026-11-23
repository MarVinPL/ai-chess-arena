"""Issue #295: Android GGUF cache safety contract until native E2E is available."""
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
CACHE = (BASE / "remote_worker_gui/src-tauri/src/android_model_cache.rs").read_text()
LIB = (BASE / "remote_worker_gui/src-tauri/src/lib.rs").read_text()


def test_gguf_cache_is_content_addressed_and_private():
    assert 'format!("{digest}.gguf")' in CACHE
    assert 'valid_digest(digest)' in CACHE
    assert 'fs::symlink_metadata(root)' in CACHE
    assert 'meta.file_type().is_file()' in CACHE
    assert 'file_type().is_dir()' in CACHE
    assert 'mod android_model_cache;' in LIB


def test_download_staging_never_publishes_unverified_bytes():
    assert 'create_new(true)' in CACHE
    assert 'format!("{digest}.gguf.partial")' in CACHE
    assert 'verify_model(&mut check, size, digest)' in CACHE
    assert 'fs::rename(&partial, &target)' in CACHE
    assert 'fs::remove_file(&partial)' in CACHE
    assert 'file.sync_all()' in CACHE
    assert 'prefix == *b"GGUF"' in CACHE


def test_cleanup_must_be_explicit_and_never_recursive():
    assert 'active_reference: bool, preserve_cache: bool' in CACHE
    assert 'if active_reference || preserve_cache { return Ok(false); }' in CACHE
    assert 'fs::remove_file(target)' in CACHE
    assert 'remove_dir_all' not in CACHE
    assert 'std::process::Command' not in CACHE
    assert '127.0.0.1' not in CACHE
    assert 'ARENA_PHONE_INFERENCE_ENABLED' not in CACHE


def test_cache_is_not_dispatched_or_exposed_to_untrusted_js():
    assert 'mod android_model_cache;' in LIB
    assert 'stage_verified_model(' not in LIB
    assert 'retire_model(' not in LIB
    assert 'download_model' not in LIB


def test_disk_reservation_is_lru_and_never_deletes_during_planning():
    assert 'pub(crate) const DEFAULT_FREE_RESERVE_BYTES: u64 = 10 * 1024 * 1024 * 1024' in CACHE
    assert 'fn plan_storage_reclamation(' in CACHE
    assert 'incoming_bytes.checked_add(reserve_bytes)' in CACHE
    assert '.filter(|entry| !entry.in_use && !entry.preserve)' in CACHE
    assert '!entry.in_use && !entry.preserve' in CACHE
    assert 'a.last_used_ms.cmp(&b.last_used_ms)' in CACHE
    planner = CACHE.split('fn plan_storage_reclamation(', 1)[1].split(
        '/// Write a previously approved transfer', 1
    )[0]
    assert 'fs::remove_file' not in planner
    assert 'still_missing_bytes: shortage.saturating_sub(reclaimed)' in planner
    assert 'seen.insert(&entry.digest)' in planner
