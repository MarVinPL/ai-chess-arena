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
    assert 'android_model_quic::android_gguf_cache_root()?' in LIB
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
    guard = workflow.split("  android:", 1)[1].split("  android_signed:", 1)[0]
    assert "refs/heads/feat/issue-295-native-model-cache" in guard
    assert "secrets:" not in guard
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" not in guard
    signed = workflow.split("  android_signed:", 1)[1]
    assert "github.ref == 'refs/heads/main'" in signed
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" in signed
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in apk
    assert 'python3 android/inject_package_installer.py' in apk


def test_android_quic_probe_is_fixed_host_head_only_without_model_side_effects():
    root = BASE / "remote_worker_gui"
    bridge = (root / "android/WorkerQuicTransport.kt").read_text()
    ui = (root / "public/index.html").read_text()
    assert '@JvmStatic\n    fun probeProtocol(context: Context): String' in bridge
    section = bridge.split('fun probeProtocol(context: Context): String', 1)[1].split(
        'fun downloadApproved(', 1
    )[0]
    assert 'https://huggingface.co/robots.txt' in section
    assert '.setHttpMethod("HEAD")' in section
    assert 'done.await(20, TimeUnit.SECONDS)' in section
    assert 'request.cancel()' in section
    assert 'FileOutputStream' not in section
    assert 'sha.digest()' not in section
    assert 'inspect_phone_quic_protocol' in LIB
    assert 'spawn_blocking' in LIB
    assert 'probeProtocol' in LIB
    assert 'inspect_phone_quic_protocol' in ui
    assert 'checkQuicProtocol' in ui
    assert "protocol==='h3'" in ui
    assert 'https://huggingface.co/robots.txt' not in ui


def test_arena_assignment_digest_must_bind_independently_approved_model():
    source = (BASE / "remote_worker_gui/src-tauri/src/android_model_source.rs").read_text()
    assert 'fn bind_approved_model_to_job(' in source
    binding = source.split('pub(crate) fn bind_approved_model_to_job(', 1)[1].split(
        '#[cfg(test)]', 1
    )[0]
    assert 'assigned_model_sha256 != registry_model.sha256' in binding
    assert 'validate_model_source(registry_model)' in binding
    assert 'bind_approved_model_to_job(&"b".repeat(64), &approved).is_err()' in source
    assert 'assigned_digest_does_not_authorize_unpinned_registry_source' in source
    assert 'download_approved_model' not in binding
    assert 'bind_approved_model_to_job' not in LIB  # no WebView command


def test_native_model_registry_fetch_requires_active_verified_job():
    coordinator = (BASE / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text()
    assert '"model-source"' in coordinator
    section = coordinator.split('pub(crate) async fn fetch_my_approved_gguf(', 1)[1].split(
        '/// The Arena owns chess state.', 1
    )[0]
    assert 'fetch_my_inference_job(base, worker_id, machine_token).await?' in section
    assert 'coordinator_endpoint(base, "model-source")' in section
    assert 'verify_assigned_model_source_reply(' in section
    assert 'if current != previous' in section
    assert 'bind_approved_model_to_job(assigned_digest, &approved)?' in coordinator
    assert 'download_approved_model(' not in section
    assert 'fetch_my_approved_gguf(' not in LIB  # internal, no Tauri command


def test_http3_jni_bridge_rehashes_gguf_and_shares_private_cache_dir():
    bridge = (BASE / "remote_worker_gui/src-tauri/src/android_model_quic.rs").read_text()
    android = (BASE / "remote_worker_gui/android/WorkerQuicTransport.kt").read_text()
    lib = (BASE / "remote_worker_gui/src-tauri/src/lib.rs").read_text()
    assert 'mod android_model_quic;' in lib
    assert 'android_model_quic::android_gguf_cache_root()?' in lib
    assert 'app.path().app_data_dir()' not in lib.split(
        'fn inspect_phone_model_storage(', 1)[1].split(
        'async fn inspect_phone_quic_protocol(', 1
    )[0]
    assert 'getFilesDir' in bridge
    assert '"worker-gguf-models"' in bridge
    assert 'File(context.filesDir, "worker-gguf-models")' in android
    assert 'downloadApproved' in bridge
    assert 'JValue::Long' in bridge
    assert 'validate_model_source(approved)?' in bridge
    assert 'verify_model(&mut file, approved.size_bytes, &approved.sha256)' in bridge
    assert 'DEFAULT_FREE_RESERVE_BYTES' in bridge
    assert 'pub(crate) fn download_verified_gguf_via_http3(' in bridge
    assert 'download_verified_gguf_via_http3(' not in lib
    assert 'download_verified_gguf_via_http3(' not in (
        BASE / 'remote_worker_gui/src-tauri/src/android_update_coordinator.rs'
    ).read_text()  # not automatically executed without STOP/ACK


def test_download_preflight_never_treats_chess_job_as_transfer_lease():
    code = (BASE / "remote_worker_gui/src-tauri/src/android_model_preflight.rs").read_text()
    assert "mod android_model_preflight;" in LIB
    assert "bind_approved_model_to_job(assigned_sha256, approved)?" in code
    assert "DEFAULT_FREE_RESERVE_BYTES" in code
    assert "active_inference_or_job || stop_or_update_pending" in code
    assert "ModelPreflight::NeedsPreparationLease" in code
    assert "ModelPreflight::InsufficientStorage" in code
    assert "ModelPreflight::VerifiedCacheAvailable" in code
    assert "download_approved_model(" not in code
    assert "download_verified_gguf_via_http3(" not in code
    assert "retire_model(" not in code
    assert "preflight_assigned_gguf(" not in LIB.split("generate_handler![", 1)[-1]


def test_download_cannot_start_continue_or_publish_without_fresh_lease():
    downloader = (BASE / "remote_worker_gui/src-tauri/src/android_model_download.rs").read_text()
    section = downloader.split(
        "pub(crate) async fn download_approved_model<F, P, L>(", 1
    )[1].split("#[cfg(test)]", 1)[0]
    assert "mut check_active_lease: L," in section
    assert "L: FnMut() -> Result<(), String>" in section
    assert section.count("check_active_lease()?;") >= 4
    assert section.index("check_active_lease()?;") < section.index(
        "let metadata = fs::symlink_metadata(root)"
    )
    assert "while let Some(chunk)" in section
    after_chunk = section.split("while let Some(chunk)", 1)[1]
    assert after_chunk.index("check_active_lease()?;") < after_chunk.index(
        "file.write_all(&chunk)"
    )
    publish = section.split("fs::rename(&partial, &target)", 1)[0]
    assert publish.rfind("check_active_lease()?;") > publish.rfind(
        "file.sync_all()"
    )
    assert "if result.is_err()" in section
    assert "fs::remove_file(&partial)" in section
    assert 'download_approved_model(' not in LIB


def test_android_http3_never_publishes_late_aborted_or_non_gguf_download():
    bridge = (BASE / "remote_worker_gui/android/WorkerQuicTransport.kt").read_text()
    transfer = bridge.split("fun downloadApproved(", 1)[1]
    assert 'val aborted = AtomicBoolean(false)' in transfer
    assert transfer.count('aborted.set(true)') >= 2
    assert 'check(!aborted.get()) { "GGUF transfer aborted" }' in transfer
    assert 'val header = ByteArray(4)' in transfer
    assert 'bytes.copyInto(header, headerBytes, 0, take)' in transfer
    assert 'header.contentEquals(byteArrayOf(71, 71, 85, 70))' in transfer
    publish = transfer.split('onSucceeded(request: UrlRequest, info: UrlResponseInfo)', 1)[1]
    publish = publish.split('override fun onFailed(', 1)[0]
    assert publish.index('check(!aborted.get())') < publish.index('pending.renameTo(complete)')
    assert publish.index('headerBytes == header.size') < publish.index('pending.renameTo(complete)')
    assert publish.index('check(actual == sha256)') < publish.index('pending.renameTo(complete)')
    # The execution STOP/ACK gate is not yet wired, so this is still internal.
    assert 'download_verified_gguf_via_http3(' not in LIB



def test_android_quic_jni_bridge_is_private_and_rehashes_gguf():
    bridge = (BASE / "remote_worker_gui/src-tauri/src/android_quic_bridge.rs").read_text()
    assert '#[cfg(target_os = "android")]\nmod android_quic_bridge;' in LIB
    assert 'download_pinned_gguf_with_android_quic(' in bridge
    assert 'validate_model_source(source)?;' in bridge
    assert '"pl.marvin.aichessarena.worker.WorkerQuicTransport"' in bridge
    assert '"downloadApproved"' in bridge
    assert 'DEFAULT_FREE_RESERVE_BYTES' in bridge
    assert 'fs::symlink_metadata(&target)' in bridge
    assert 'verify_model(&mut file, source.size_bytes, &source.sha256)' in bridge
    assert 'fn private_cache_root(' in bridge
    assert 'download_pinned_gguf_with_android_quic(' not in LIB
    assert '#[tauri::command]' not in bridge
    assert 'JValue::Long(size)' in bridge
    assert 'JValue::Long(reserve)' in bridge


def test_model_lifecycle_requires_terminal_stop_ack_and_reconciliation():
    lifecycle = (BASE / "remote_worker_gui/src-tauri/src/android_model_lifecycle.rs").read_text()
    assert 'mod android_model_lifecycle;' in LIB
    assert 'pub(crate) fn model_download_gate(' in lifecycle
    assert 'pub(crate) fn model_eviction_gate(' in lifecycle
    assert 'pub(crate) fn plan_safe_cache_reclamation(' in lifecycle
    assert 'JobLifecycle::StopRequested' in lifecycle
    assert 'JobLifecycle::StopAcknowledged' in lifecycle
    assert 'JobLifecycle::IdleConfirmed' in lifecycle
    assert 'e.terminal_reconciled' in lifecycle
    assert 'e.checkpoint_finalized' in lifecycle
    assert 'e.runtime_stopped' in lifecycle
    assert 'e.update_hold || e.pause_pending || e.resume_pending' in lifecycle
    assert 'plan_storage_reclamation(' in lifecycle
    assert 'retire_model(' not in lifecycle
    assert 'download_pinned_gguf_with_android_quic(' not in lifecycle
    assert '#[tauri::command]' not in lifecycle


def test_app_owned_gguf_file_descriptor_remains_locked_until_runtime_stops():
    runtime = (BASE / "remote_worker_gui/src-tauri/src/android_model_runtime_guard.rs").read_text()
    assert 'mod android_model_runtime_guard;' in LIB
    assert 'pub(crate) struct ModelSession' in runtime
    assert 'pub(crate) file: File' in runtime
    assert 'impl Drop for ModelSession' in runtime
    assert 'pub(crate) fn open_verified_session(' in runtime
    assert 'verify_model(&mut file, expected_size, digest)' in runtime
    assert 'SeekFrom::Start(0)' in runtime
    assert 'pub(crate) fn with_no_open_sessions<' in runtime
    assert 'MAINTENANCE_ACTIVE.compare_exchange(' in runtime
    assert 'if !active.is_empty()' in runtime
    assert 'std::process::Command' not in runtime
    assert 'fs::remove_file(' not in runtime.split('#[cfg(test)]', 1)[0]
    assert 'retire_model(' not in runtime
    assert 'open_verified_session(' not in LIB
    assert 'with_no_open_sessions(' not in LIB
    assert '#[tauri::command]' not in runtime


def test_embedded_android_llama_probe_uses_only_own_package_abi_not_termux():
    source = (BASE / "remote_worker_gui/src-tauri/src/android_embedded_llama.rs").read_text()
    assert "mod android_embedded_llama;" in LIB
    assert "getApplicationInfo" in source
    assert '"nativeLibraryDir"' in source
    assert 'root.join("libllama.so")' in source
    assert 'fs::symlink_metadata(path)' in source
    assert 'info.file_type().is_file()' in source
    assert 'dlopen(raw.as_ptr(), RTLD_NOW | RTLD_LOCAL)' in source
    assert 'dlsym(library.0, symbol.as_ptr())' in source
    assert 'llama_model_load_from_file' in source
    assert 'llama_decode' in source
    assert 'llama_init_from_model' in source
    assert 'llama_new_context_with_model' not in source
    assert 'llama_sampler_init_greedy' in source
    assert 'llama-server' not in source.split('#[cfg(test)]', 1)[0].split('fn native_library_dir()', 1)[1]
    assert 'std::process::Command' not in source
    assert 'probe_embedded_llama_library(' not in LIB.split('async fn inspect_embedded_llama_engine()', 1)[0]
    assert 'probe_embedded_llama_library(' not in LIB.split('// The desktop app connects outbound only.', 1)[1]
    assert '#[tauri::command]' not in source


def test_embedded_llama_diagnostic_does_not_activate_inference():
    ui = (BASE / "remote_worker_gui/public/index.html").read_text()
    assert 'async fn inspect_embedded_llama_engine() -> Result<bool, String>' in LIB
    assert 'android_embedded_llama::probe_embedded_llama_library()?' in LIB
    assert 'inspect_embedded_llama_engine,' in LIB
    assert 'id="checkEmbeddedLlama"' in ui
    assert 'id="embeddedLlamaStatus"' in ui
    assert "invoke('inspect_embedded_llama_engine')" in ui
    assert 'Inferencja bez Termux pozostaje wyłączona' in ui


def test_pinned_embedded_llama_build_and_apk_packaging_are_fail_closed():
    script = (BASE / "remote_worker_gui/android/build_embedded_llama.sh").read_text()
    workflow = (BASE / ".github/workflows/android-worker-apk.yml").read_text()
    assert "LLAMA_COMMIT=b1b770d90d94cad9e9ea5b993bdbc383a522fb28" in script
    assert 'git -C "$STAGING/source" fetch --no-tags --depth 1 origin "$LLAMA_COMMIT"' in script
    assert 'rev-parse FETCH_HEAD' in script
    assert '-DANDROID_ABI=arm64-v8a' in script
    assert '-DGGML_NATIVE=OFF' in script
    assert '-DGGML_OPENMP=OFF' in script
    assert '-DLLAMA_BUILD_SERVER=OFF' in script
    assert 'test ! -e "$STAGING"' in script
    assert 'git -C "$STAGING/source" remote add origin "$LLAMA_REPO"' in script
    assert 'cp -L "$so" "$LIB_DIR/lib${lib}.so"' in script
    assert 'llvm-nm' in script
    assert 'llama-symbols.txt' in script
    assert 'awk -v expected="$sym"' in script
    assert '| grep -Fxq "$sym"' not in script
    assert 'llama_model_load_from_file' in script
    assert 'llama_init_from_model' in script
    assert 'llama_new_context_with_model' not in script
    assert 'bash android/build_embedded_llama.sh' in workflow
    assert 'unzip -Z1 "$apk"' in workflow
    assert 'lib/arm64-v8a/lib${name}.so' in workflow
    assert workflow.index('bash android/build_embedded_llama.sh') < workflow.index('npx tauri android build')
    assert 'LLAMA_COMMIT=master' not in script
    assert 'curl ' not in script


def test_extracted_native_packaging_for_own_apk_library_directory():
    injected = (BASE / "remote_worker_gui/android/force_extracted_native_libs.py").read_text()
    workflow = (BASE / ".github/workflows/android-worker-apk.yml").read_text()
    assert 'jniLibs.useLegacyPackaging = true' in injected
    assert '"src-tauri/gen/android/app/build.gradle.kts"' in injected or (
        '"src-tauri/gen/android/app/build.gradle.kts"' in injected.replace(
            '\\n', ''
        )
    )
    assert 'Generated Android Gradle layout changed' in injected
    assert 'python3 android/force_extracted_native_libs.py' in workflow
    assert workflow.index('python3 android/force_extracted_native_libs.py') < (
        workflow.index('bash android/build_embedded_llama.sh')
    )


def test_native_embedded_llama_loader_keeps_gguf_handle_until_unload():
    runtime = (BASE / "remote_worker_gui/src-tauri/src/android_llama_runtime.rs").read_text()
    shim = (BASE / "remote_worker_gui/android/arena_llama_bridge.cpp").read_text()
    build = (BASE / "remote_worker_gui/android/build_embedded_llama.sh").read_text()
    ci = (BASE / ".github/workflows/android-worker-apk.yml").read_text()
    assert 'mod android_llama_runtime;' in LIB
    assert 'pub(crate) fn load_verified_embedded_model(' in runtime
    assert 'open_verified_session(&root, digest, expected_size)?' in runtime
    assert 'native_library_dir()?.join("libarena_llama_bridge.so")' in runtime
    assert 'fs::symlink_metadata(&library_path)' in runtime
    assert 'lib.symbol(c"arena_llama_model_open")' in runtime
    assert 'lib.symbol(c"arena_llama_model_close")' in runtime
    assert 'impl Drop for EmbeddedLlamaModel' in runtime
    assert '(self.close)();' in runtime
    assert '_verified_file: ModelSession' in runtime
    assert 'std::mem::transmute' in runtime
    assert 'arena_llama_model_open(' in shim
    assert 'llama_model_load_from_file(private_gguf_path, params)' in shim
    assert 'llama_model_free(loaded_model)' in shim
    assert 'params.n_gpu_layers = 0' in shim
    assert 'std::lock_guard<std::mutex>' in shim
    assert 'libarena_llama_bridge.so' in build
    assert 'arena_llama_model_is_open' in build
    assert 'for name in llama ggml ggml-base ggml-cpu arena_llama_bridge; do' in ci
    assert 'load_verified_embedded_model(' not in LIB  # no frontend dispatch
    assert '#[tauri::command]' not in runtime


def test_native_llama_bridge_compiles_with_pinned_ggml_headers():
    build = (BASE / "remote_worker_gui/android/build_embedded_llama.sh").read_text()
    bridge = (BASE / "remote_worker_gui/android/arena_llama_bridge.cpp").read_text()
    assert '#include "llama.h"' in bridge
    assert '-I"$STAGING/source/include"' in build
    assert '-I"$STAGING/source/ggml/include"' in build
    assert build.index('-I"$STAGING/source/ggml/include"') < build.index('android/arena_llama_bridge.cpp')


def test_embedded_llama_bridge_links_exact_file_not_relative_library_search():
    script = (BASE / "remote_worker_gui/android/build_embedded_llama.sh").read_text()
    assert '"$(realpath "$LIB_DIR/libllama.so")"' in script
    assert ' -llama ' not in script
    assert 'libarena_llama_bridge.so' in script


def test_native_embedded_greedy_uci_is_bounded_and_not_dispatched():
    bridge = (BASE / "remote_worker_gui/android/arena_llama_bridge.cpp").read_text()
    runtime = (BASE / "remote_worker_gui/src-tauri/src/android_llama_runtime.rs").read_text()
    build = (BASE / "remote_worker_gui/android/build_embedded_llama.sh").read_text()
    assert 'arena_llama_generate_greedy(' in bridge
    assert 'llama_init_from_model(loaded_model, params)' in bridge
    assert 'llama_tokenize(vocab' in bridge
    assert 'llama_decode(ctx, llama_batch_get_one(' in bridge
    assert 'llama_sampler_init_greedy()' in bridge
    assert 'llama_sampler_sample(sampler, ctx, -1)' in bridge
    assert 'llama_token_to_piece(vocab' in bridge
    assert 'llama_vocab_is_eog(vocab, next)' in bridge
    assert 'llama_free(ptr)' in bridge
    assert 'llama_sampler_free(ptr)' in bridge
    assert 'std::lock_guard<std::mutex> guard(engine_mutex)' in bridge
    assert 'max_tokens > 64' in bridge
    assert 'output_capacity > 4096' in bridge
    assert 'prompt_len > 8192' in bridge
    assert 'params.n_gpu_layers = 0' in bridge
    assert 'arena_llama_generate_greedy' in build
    assert 'pub(crate) fn propose_uci(' in runtime
    assert 'legal_moves.is_empty() || legal_moves.len() > 256' in runtime
    assert 'parse_exact_uci_answer(text)?' in runtime
    assert 'legal_moves.iter().any(|mv| mv == &proposed)' in runtime
    assert 'self.generate' in runtime
    assert 'propose_uci(' not in LIB
    assert '#[tauri::command]' not in runtime


def test_apk_update_exclusivity_fences_native_gguf_across_async_handoff():
    runtime_guard = (BASE / "remote_worker_gui/src-tauri/src/android_model_runtime_guard.rs").read_text()
    native_runtime = (BASE / "remote_worker_gui/src-tauri/src/android_llama_runtime.rs").read_text()
    assert 'pub(crate) fn begin_exclusive_update()' in runtime_guard
    assert 'impl Drop for ExclusiveUpdateGate' in runtime_guard
    assert 'MAINTENANCE_ACTIVE.store(false, Ordering::SeqCst)' in runtime_guard
    assert 'Silnik nadal ma otwarte pliki GGUF' in runtime_guard
    assert 'update_gate_rejects_live_model_then_blocks_new_opens_until_drop' in runtime_guard
    section = LIB.split('async fn install_downloaded_worker_apk(', 1)[1].split(
        'async fn ', 1
    )[0]
    assert 'let _native_update_gate =' in section
    assert 'android_model_runtime_guard::begin_exclusive_update()?' in section
    assert section.index('begin_exclusive_update()?') < section.index('check_update_safety(')
    assert section.index('begin_exclusive_update()?') < section.index('reserve_update(')
    assert 'android_update_recovery::load_pending()?.is_some()' in native_runtime
    assert 'android_update_install_journal::load()?.is_some()' in native_runtime
    assert native_runtime.index('load_pending()?.is_some()') < native_runtime.index('open_verified_session(')
    assert native_runtime.index('load()?.is_some()') < native_runtime.index('open_verified_session(')


def test_native_llama_stop_is_sticky_until_model_is_reopened():
    """Static regression contract: cancellation is independent of engine mutex."""
    cpp = (BASE / "remote_worker_gui/android/arena_llama_bridge.cpp").read_text()
    rust = (BASE / "remote_worker_gui/src-tauri/src/android_llama_runtime.rs").read_text()
    assert "#include <atomic>" in cpp
    assert "std::atomic<bool> stop_requested{false}" in cpp
    assert "std::atomic<bool> inference_active{false}" in cpp
    stop = cpp.split("int arena_llama_request_stop() noexcept {", 1)[1].split("}", 1)[0]
    assert "stop_requested.store(true, std::memory_order_release)" in stop
    assert "engine_mutex" not in stop
    assert "stop_requested.store(false, std::memory_order_release)" in cpp.split(
        "int arena_llama_model_open(", 1
    )[1].split("void arena_llama_model_close(", 1)[0]
    generate = cpp.split("int arena_llama_generate_greedy(", 1)[1]
    assert "inference_active.store(true, std::memory_order_release)" in generate
    assert "inference_active.store(false, std::memory_order_release)" in generate
    assert generate.count("stop_requested.load(std::memory_order_acquire)") >= 5
    assert "arena_llama_inference_quiesced" in cpp
    assert "pub(crate) fn request_embedded_inference_stop()" in rust
    assert "pub(crate) fn embedded_inference_quiesced()" in rust
    assert 'lib.symbol(c"arena_llama_request_stop")?' in rust
    assert 'lib.symbol(c"arena_llama_inference_quiesced")?' in rust
    assert "request_embedded_inference_stop(" not in LIB
    assert "embedded_inference_quiesced(" not in LIB
