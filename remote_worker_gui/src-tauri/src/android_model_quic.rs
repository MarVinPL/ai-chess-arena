//! Android OS HttpEngine QUIC/HTTP3 bridge for a *trusted* Arena-approved GGUF.
//! Not a Tauri command and deliberately not invoked by automatic dispatch yet.
//! Caller must independently verify current lease/STOP before and after transfer.
//! Kotlin stages, hashes and publishes; Rust hashes exact bytes again and
//! refuses to trust protocol or filename as proof of model integrity.
#![cfg(target_os = "android")]

use crate::android_model_cache::{model_path, verify_model, DEFAULT_FREE_RESERVE_BYTES};
use crate::android_model_source::{validate_model_source, ApprovedGguf};
use jni::objects::{JClass, JObject, JString, JValue};
use jni::JavaVM;
use std::fs::{self, File};
use std::path::PathBuf;

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
pub(crate) fn download_verified_gguf_via_http3(
    approved: &ApprovedGguf,
) -> Result<(PathBuf, String), String> {
    validate_model_source(approved)?;
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
        let result = env.call_static_method(
            JClass::from(class),
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
