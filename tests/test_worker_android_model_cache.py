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


def test_native_storage_command_is_read_only_and_displayed_in_android_ui():
    ui = (BASE / "remote_worker_gui/public/index.html").read_text()
    assert 'fn inspect_phone_model_storage(app: tauri::AppHandle) -> Result<PhoneModelStorage, String>' in LIB
    assert 'android_model_cache::android_private_free_bytes()?' in LIB
    assert 'free_bytes.saturating_sub(reserve_bytes)' in LIB
    assert 'inspect_phone_model_storage,' in LIB
    section = LIB.split('fn inspect_phone_model_storage(app: tauri::AppHandle)', 1)[1].split(
        'async fn inspect_phone_local_model()', 1
    )[0]
    assert 'stage_verified_model' not in section
    assert 'retire_model' not in section
    assert 'id="localModelStorageStatus"' in ui
    assert "invoke('inspect_phone_model_storage')" in ui
    assert 'szacunek; brak rezerwacji miejsca' in ui
    assert "if(localModelStorageProbeInFlight||document.hidden)return;" in ui
    assert 'void checkPhoneModelStorage();' in ui


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


def test_model_cache_inventory_is_read_only_and_shown_in_ui():
    ui = (BASE / "remote_worker_gui/public/index.html").read_text()
    assert 'fn inspect_model_cache(root: &Path) -> Result<ModelCacheInventory, String>' in CACHE
    assert 'fs::read_dir(root)' in CACHE
    assert 'fs::symlink_metadata(entry.path())' in CACHE
    inventory = CACHE.split('fn inspect_model_cache(', 1)[1].split(
        '/// An entry is eligible for eviction', 1
    )[0]
    assert 'fs::remove_file' not in inventory
    assert 'valid_digest(digest)' in inventory
    assert 'entry.file_name()' in inventory
    assert '.join("worker-gguf-models")' in LIB
    assert 'android_model_cache::inspect_model_cache(&private_root)?' in LIB
    assert 'cached_models: inventory.model_count' in LIB
    assert 'cached_bytes: inventory.occupied_bytes' in LIB
    assert "storage.cached_models" in ui
    assert "storage.cached_bytes" in ui
    assert "Cache GGUF:" in ui


def test_android_http3_bridge_is_packaged_with_tauri_and_requires_approved_gguf():
    root = BASE / "remote_worker_gui"
    bridge = (root / "android/WorkerQuicTransport.kt").read_text()
    injector = (root / "android/inject_package_installer.py").read_text()
    workflow = (BASE / ".github/workflows/android-worker-apk.yml").read_text()
    assert 'import android.net.http.HttpEngine' in bridge
    assert 'setEnableQuic(true)' in bridge
    assert 'setEnableHttp2(true)' in bridge
    assert 'info.negotiatedProtocol' in bridge
    assert 'info.headers.asMap.entries' in bridge
    assert 'it.key.equals("content-length", ignoreCase = true)' in bridge
    assert 'info.allHeaders' not in bridge
    assert 'setEnableHttpCache' not in bridge
    assert 'expectedBytes' in bridge and 'reserveBytes' in bridge
    assert 'sha.digest()' in bridge
    assert 'check(actual == sha256)' in bridge
    assert 'context.filesDir' in bridge
    assert 'redirectHosts' in bridge
    assert 'check(redirects++ < 3)' in bridge
    assert 'java.nio.file.Files.isSymbolicLink' in bridge
    assert 'WorkerQuicTransport.kt' in injector
    assert 'shutil.copyfile(QUIC_SOURCE, VAULT_TARGET.parent / QUIC_SOURCE.name)' in injector
    assert 'python3 android/inject_package_installer.py' in workflow
    assert 'downloadApproved(' not in LIB  # no untrusted Tauri IPC


def test_stacked_android_quic_build_runs_without_exposing_signing_secrets():
    workflow = (BASE / ".github/workflows/worker-multiplatform.yml").read_text()
    apk = (BASE / ".github/workflows/android-worker-apk.yml").read_text()
    assert "branches: [main, feat/issue-295-native-model-cache]" in workflow
    assert "feat/issue-293-android-model-diagnostics" in workflow
    assert workflow.count("'remote_worker_gui/android/**'") == 3
    guard = workflow.split("\n  android:\\n", 1)[1].split("\n  android_signed:\\n", 1)[0]
    assert "refs/heads/feat/issue-295-native-model-cache" in guard
    assert "secrets:" not in guard
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" not in guard
    signed = workflow.split("\n  android_signed:\\n", 1)[1]
    assert "github.ref == 'refs/heads/main'" in signed
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" in signed
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in apk
    assert 'python3 android/inject_package_installer.py' in apk
