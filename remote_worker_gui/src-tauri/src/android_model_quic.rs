//! Android OS HttpEngine QUIC/HTTP3 bridge for a *trusted* Arena-approved GGUF.
//! Not a Tauri command and deliberately not invoked by automatic dispatch yet.
//! Caller must independently verify current lease/STOP before and after transfer.
//! Kotlin stages, hashes and publishes; Rust hashes exact bytes again and
//! refuses to trust protocol or filename as proof of model integrity.
#![cfg(target_os = "android")]

use crate::android_model_cache::{model_path, verify_model, DEFAULT_FREE_RESERVE_BYTES};
use crate::android_model_source::{validate_model_source, ApprovedGguf};
use jni::objects::{JClass, JObject, JString, JValue};
use jni::{JavaVM, NativeMethod};
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};
use std::fs::{self, File};
use std::path::PathBuf;

type PreparationCheck = Box<dyn FnMut() -> Result<(), String> + Send>;
static PREPARATION: Mutex<Option<Arc<Mutex<PreparationCheck>>>> = Mutex::new(None);
static PREPARATION_REVOKED: AtomicBool = AtomicBool::new(true);

unsafe extern "system" fn native_preparation_allowed(
    _env: jni::JNIEnv<'_>, _class: JClass<'_>,
) -> jni::sys::jboolean {
    if PREPARATION_REVOKED.load(Ordering::Acquire) { return jni::sys::JNI_FALSE; }
    let allowed = std::panic::catch_unwind(|| {
        let callback = PREPARATION.lock().ok().and_then(|slot| slot.clone());
        callback.is_some_and(|check| check.lock().ok().is_some_and(|mut f| f().is_ok()))
    }).unwrap_or(false);
    if !allowed { PREPARATION_REVOKED.store(true, Ordering::Release); }
    if allowed && !PREPARATION_REVOKED.load(Ordering::Acquire) { jni::sys::JNI_TRUE } else { jni::sys::JNI_FALSE }
}

struct PreparationScope;
impl PreparationScope {
    fn enter(check: Arc<Mutex<PreparationCheck>>) -> Result<Self, String> {
        let mut slot = PREPARATION.lock().map_err(|_| "Nieustalona dzierżawa GGUF")?;
        if slot.is_some() { return Err("Transfer GGUF już trwa".into()); }
        *slot = Some(check);
        PREPARATION_REVOKED.store(false, Ordering::Release);
        Ok(Self)
    }
}
impl Drop for PreparationScope {
    fn drop(&mut self) {
        PREPARATION_REVOKED.store(true, Ordering::Release);
        if let Ok(mut slot) = PREPARATION.lock() { *slot = None; }
    }
}

fn with_android_files<T>(
    action: impl FnOnce(&mut jni::JNIEnv<'_>, &JObject<'_>, &JObject<'_>) -> Result<T, String>,
) -> Result<T, String> {
    let ctx = ndk_context::android_context();
    let vm = unsafe { JavaVM::from_raw(ctx.vm().cast()) }
        .map_err(|_| "Brak Android JavaVM")?;
    let mut env = vm.attach_current_thread()
        .map_err(|_| "Nie można podłączyć wątku Android")?;
    let context = unsafe { JObject::from_raw(ctx.context().cast()) };
    let files = env.call_method(&context, "getFilesDir", "()Ljava/io/File;", &[])
        .map_err(|_| "Brak katalogu plików Android")?
        .l().map_err(|_| "Nieprawidłowy katalog aplikacji")?;
    if files.is_null() {
        return Err("Katalog prywatny Android jest niedostępny".into());
    }
    let result = action(&mut env, &context, &files);
    if env.exception_check().unwrap_or(true) {
        let _ = env.exception_clear();
        return Err("Android JNI zgłosił błąd".into());
    }
    result
}

/// Canonical app-private root shared with WorkerQuicTransport.kt.
/// Unlike Tauri app_data_dir(), this resolves exactly Context.filesDir.
pub(crate) fn android_gguf_cache_root() -> Result<PathBuf, String> {
    with_android_files(|env, _, files| {
        let path = env.call_method(files, "getAbsolutePath", "()Ljava/lang/String;", &[])
            .map_err(|_| "Nie można odczytać katalogu GGUF")?
            .l().map_err(|_| "Nieprawidłowa ścieżka Android")?;
        if path.is_null() {
            return Err("Pusta ścieżka cache Android".into());
        }
        let root: String = env.get_string(&JString::from(path))
            .map_err(|_| "Nie można odczytać ścieżki aplikacji")?.into();
        if !root.starts_with('/') {
            return Err("Nieprawidłowy prywatny katalog aplikacji".into());
        }
        Ok(PathBuf::from(root).join("worker-gguf-models"))
    })
}

/// Synchronous JNI call: must run on a background worker thread, never the UI.
/// Return only after independent Rust GGUF magic/length/SHA-256 verification.
/// NO caller is wired yet, because the live lease/STOP boundary is pending.
#[allow(dead_code)]
fn download_verified_gguf_via_http3(
    approved: &ApprovedGguf,
    check: Arc<Mutex<PreparationCheck>>,
) -> Result<(PathBuf, String), String> {
    validate_model_source(approved)?;
    let _scope = PreparationScope::enter(check)?;
    let root = android_gguf_cache_root()?;
    if !fs::symlink_metadata(&root)
        .map(|m| m.file_type().is_dir()).unwrap_or(false) && root.exists() {
        return Err("Katalog cache GGUF ma nieprawidłowy typ".into());
    }
    let target = model_path(&root, &approved.sha256)?;
    // The Kotlin transport refuses to overwrite any existing GGUF.
    let protocol = with_android_files(|env, context, _| {
        let url = env.new_string(&approved.source_url)
            .map_err(|_| "Błąd źródła GGUF Android")?;
        let sha = env.new_string(&approved.sha256)
            .map_err(|_| "Błąd identyfikatora GGUF Android")?;
        let loader = env.call_method(context, "getClassLoader", "()Ljava/lang/ClassLoader;", &[])
            .map_err(|_| "Brak classloadera Android")?.l().map_err(|_| "Brak classloadera Android")?;
        let name = env.new_string("pl.marvin.aichessarena.worker.WorkerQuicTransport")
            .map_err(|_| "Brak nazwy mostu QUIC")?;
        let class = env.call_method(&loader, "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;",
            &[JValue::Object(name.as_ref())])
            .map_err(|_| "Brak mostu QUIC")?.l().map_err(|_| "Brak mostu QUIC")?;
        if class.is_null() { return Err("Brak mostu QUIC".into()); }
        let class = JClass::from(class);
        env.register_native_methods(&class, &[NativeMethod {
            name: "nativePreparationAllowed".into(), sig: "()Z".into(),
            fn_ptr: native_preparation_allowed as *mut std::ffi::c_void,
        }]).map_err(|_| "Nie można zarejestrować kontroli dzierżawy")?;
        let result = env.call_static_method(
            &class,
            "downloadApproved",
            "(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;JJ)Ljava/lang/String;",
            &[
                JValue::Object(context),
                JValue::Object(&JObject::from(url)),
                JValue::Object(&JObject::from(sha)),
                JValue::Long(i64::try_from(approved.size_bytes)
                    .map_err(|_| "GGUF przekracza limit Android")?),
                JValue::Long(i64::try_from(DEFAULT_FREE_RESERVE_BYTES)
                    .map_err(|_| "Nieprawidłowa rezerwa pamięci")?),
            ],
        ).map_err(|_| "Android HttpEngine przerwał pobieranie GGUF")?
            .l().map_err(|_| "Android zwrócił błędny wynik transferu")?;
        if result.is_null() {
            return Err("Brak protokołu odpowiedzi transferu".into());
        }
        let protocol: String = env.get_string(&JString::from(result))
            .map_err(|_| "Nie można odczytać użytego protokołu")?.into();
        if !matches!(protocol.as_str(), "h3" | "h2" | "http/1.1") {
            return Err("Nieznany protokół pobierania GGUF".into());
        }
        Ok(protocol)
    })?;
    let metadata = fs::symlink_metadata(&target)
        .map_err(|_| "Android nie zapisał zatwierdzonego GGUF")?;
    if !metadata.file_type().is_file() || metadata.len() != approved.size_bytes {
        return Err("Nieprawidłowy plik GGUF po transferze Android".into());
    }
    let mut file = File::open(&target)
        .map_err(|_| "Nie można odczytać GGUF po transferze")?;
    if !verify_model(&mut file, approved.size_bytes, &approved.sha256)
        .map_err(|_| "Nie udało się przeliczyć SHA-256 GGUF")? {
        return Err("GGUF po transferze nie przeszedł weryfikacji Rust".into());
    }
    Ok((target, protocol))
}

/// Internal transport selection. A separate verified preparation lease is mandatory.
/// HttpEngine negotiates HTTPS itself on supported OS versions. On older OS
/// versions use the Rust HTTPS implementation; never retry an uncertain transfer.
#[allow(dead_code)]
pub(crate) async fn download_with_controlled_fallback<P, L>(
    approved: &ApprovedGguf, progress: P, mut check_preparation_lease: L,
) -> Result<(PathBuf, String), String>
where P: FnMut(u64, u64), L: FnMut() -> Result<(), String> + Send + 'static {
    validate_model_source(approved)?;
    let _gate = crate::android_model_runtime_guard::begin_exclusive_update()?;
    check_preparation_lease()?;
    if crate::android_update_recovery::load_pending()?.is_some()
        || crate::android_update_install_journal::load()?.is_some() {
        return Err("Aktualizacja blokuje transfer GGUF".into());
    }
    let sdk = with_android_files(|env, _, _| {
        env.get_static_field("android/os/Build$VERSION", "SDK_INT", "I")
            .map_err(|_| "Brak wersji Android")?.i().map_err(|_| "Brak wersji Android".into())
    })?;
    if sdk >= 34 {
        let check: Arc<Mutex<PreparationCheck>> = Arc::new(Mutex::new(Box::new(move || {
            if crate::android_update_recovery::load_pending()?.is_some()
                || crate::android_update_install_journal::load()?.is_some() {
                return Err("Aktualizacja blokuje transfer GGUF".into());
            }
            check_preparation_lease()
        })));
        // Caller must execute on a background worker; no automatic retry
        // after any HttpEngine failure (including revoked lease or bad bytes).
        let result = download_verified_gguf_via_http3(approved, check.clone())?;
        check.lock().map_err(|_| "Nieustalona dzierżawa GGUF")?()?;
        return Ok(result);
    }
    let root = android_gguf_cache_root()?;
    match fs::create_dir(&root) {
        Ok(()) => (),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
        Err(_) => return Err("Nie można utworzyć cache GGUF".into()),
    }
    let path = crate::android_model_download::download_approved_model(
        &root, approved, DEFAULT_FREE_RESERVE_BYTES,
        crate::android_model_cache::android_private_free_bytes, progress,
        || {
            if crate::android_update_recovery::load_pending()?.is_some()
                || crate::android_update_install_journal::load()?.is_some() {
                return Err("Aktualizacja blokuje transfer GGUF".into());
            }
            check_preparation_lease()
        },
    ).await?;
    check_preparation_lease()?;
    Ok((path, "https".into()))
}

/// Explicit fixed-host HEAD diagnostic, independent of model transfer.
pub(crate) fn probe_protocol() -> Result<String, String> {
    with_android_files(|env, context, _| {
        let loader = env.call_method(context, "getClassLoader", "()Ljava/lang/ClassLoader;", &[])
            .map_err(|_| "Brak classloadera")?.l().map_err(|_| "Brak classloadera")?;
        let name = env.new_string("pl.marvin.aichessarena.worker.WorkerQuicTransport")
            .map_err(|_| "Brak nazwy mostu")?;
        let class = env.call_method(&loader, "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;",
            &[JValue::Object(name.as_ref())])
            .map_err(|_| "Brak mostu QUIC")?.l().map_err(|_| "Brak mostu QUIC")?;
        if class.is_null() { return Err("Brak mostu QUIC".into()); }
        let result = env.call_static_method(JClass::from(class), "probeProtocol",
            "(Landroid/content/Context;)Ljava/lang/String;", &[JValue::Object(context)])
            .map_err(|_| "HttpEngine niedostępny")?.l().map_err(|_| "Brak odpowiedzi HTTP")?;
        if result.is_null() { return Err("Brak protokołu".into()); }
        let protocol: String = env.get_string(&JString::from(result))
            .map_err(|_| "Nieprawidłowy protokół")?.into();
        if !matches!(protocol.as_str(), "h3" | "h2" | "http/1.1") {
            return Err("Nieznany protokół".into());
        }
        Ok(protocol)
    })
}
