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


def test_android_reads_private_partition_without_user_selected_path():
    assert 'fn android_private_free_bytes() -> Result<u64, String>' in CACHE
    assert 'getFilesDir' in CACHE
    assert 'getUsableSpace' in CACHE
    block = CACHE.split('fn android_private_free_bytes()', 1)[1].split(
        '/// Planning is pure:', 1
    )[0]
    assert 'ndk_context::android_context()' in block
    assert 'if usable < 0' in block
    assert 'std::env::' not in block
    assert 'getExternalStorage' not in block
    assert 'remove_file' not in block


def test_approved_hf_metadata_and_private_download_are_not_exposed_to_js():
    source = (BASE / "remote_worker_gui/src-tauri/src/android_model_source.rs").read_text()
    downloader = (BASE / "remote_worker_gui/src-tauri/src/android_model_download.rs").read_text()
    assert 'mod android_model_source;' in LIB
    assert 'mod android_model_download;' in LIB
    assert 'sha256: String' in source and 'size_bytes: u64' in source
    assert 'source_url: String' in source
    assert 'url.host_str() != Some("huggingface.co")' in source
    assert 'segments[3].len() != 40' in source
    assert 'url.scheme() != "https"' in source
    assert 'validate_model_source(approved)?' in downloader
    assert 'redirect::Policy::custom' in downloader
    assert 'file.sync_all()' in downloader
    assert 'verify_model(&mut check, approved.size_bytes, &approved.sha256)' in downloader
    assert 'fs::rename(&partial, &target)' in downloader
    assert 'free_space()? < needed' in downloader
    assert 'free_space()? < reserve_bytes' in downloader
    assert 'active_reference' in CACHE
    assert 'pub(crate) async fn download_approved_model' in downloader
    assert 'download_approved_model(' not in LIB
    assert 'download_model' not in LIB


def test_recover_stale_partial_only_after_trusted_stop_and_without_js_ipc():
    transport = (BASE / "remote_worker_gui/src-tauri/src/android_model_download.rs").read_text()
    assert 'fn reconcile_partial_after_restart(' in transport
    assert 'previous_execution_stopped_verified: bool' in transport
    assert 'if active_reference || !previous_execution_stopped_verified' in transport
    assert 'fs::symlink_metadata(&partial)' in transport
    assert 'meta.file_type().is_file()' in transport
    recovery = transport.split('fn reconcile_partial_after_restart(', 1)[1].split(
        'fn allowed_redirect(', 1
    )[0]
    assert 'fs::remove_file(&partial)' in recovery
    assert 'remove_dir_all' not in recovery
    assert 'std::process::Command' not in recovery
    assert 'reconcile_partial_after_restart(' not in LIB


def test_quic_bridge_is_private_and_packaged():
    bridge = (BASE / "remote_worker_gui/android/WorkerQuicTransport.kt").read_text()
    injector = (BASE / "remote_worker_gui/android/inject_package_installer.py").read_text()
    rust = (BASE / "remote_worker_gui/src-tauri/src/android_model_quic.rs").read_text()
    assert ".setEnableQuic(true)" in bridge
    assert ".setEnableHttp2(true)" in bridge
    assert 'setOf("h3", "h2", "http/1.1")' in bridge
    assert "WorkerQuicTransport.kt" in injector
    assert "shutil.copyfile(QUIC_SOURCE" in injector
    assert "getClassLoader" in rust and "loadClass" in rust
    assert "verify_model(&mut file, approved.size_bytes, &approved.sha256)" in rust
    assert "download_verified_gguf_via_http3(" not in LIB
    assert "#[tauri::command]" not in rust


def test_quic_publication_follows_successful_callback_and_reserve_check():
    bridge = (BASE / "remote_worker_gui/android/WorkerQuicTransport.kt").read_text()
    transfer = bridge.split("fun downloadApproved(", 1)[1]
    callback = transfer.split("override fun onSucceeded", 1)[1].split("override fun onFailed", 1)[0]
    assert "check(actual == sha256)" in callback
    assert "stream.fd.sync()" in callback
    assert "Files.move" not in callback
    assert transfer.index("done.await(1, TimeUnit.SECONDS)") < transfer.index("Files.move")
    assert transfer.index("check(error == null)") < transfer.index("Files.move")
    assert "expectedBytes - received + count.toLong()" in transfer
    assert "StandardCopyOption.ATOMIC_MOVE" in transfer
    assert "LinkOption.NOFOLLOW_LINKS" in transfer

def test_http3_requires_native_lease_throughout_transfer():
    bridge = (BASE / "remote_worker_gui/android/WorkerQuicTransport.kt").read_text()
    rust = (BASE / "remote_worker_gui/src-tauri/src/android_model_quic.rs").read_text()
    transfer = bridge.split("fun downloadApproved(", 1)[1]
    assert transfer.count("requirePreparation()") >= 6
    assert "private external fun nativePreparationAllowed()" in bridge
    assert "register_native_methods" in rust
    assert 'let _gate = crate::android_model_runtime_guard::begin_exclusive_update()?' in rust
    assert "if sdk >= 34" in rust
    assert "download_verified_gguf_via_http3(approved, check.clone())?" in rust
    assert "download_with_controlled_fallback(" not in LIB
