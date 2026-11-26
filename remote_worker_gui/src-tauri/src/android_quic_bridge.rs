//! Native Android QUIC downloader boundary for trusted, pinned Arena GGUF.
//!
//! Deliberately dormant: no Tauri command or automatic dispatcher uses it.
//! Only a trusted coordinator holding a current verified execution lease may
//! eventually call this from a BACKGROUND worker thread. The WebView cannot
//! provide a URL, SHA, cache path or trigger downloads.
//! Kotlin's HttpEngine streams to the app-private store; Rust independently
//! reopens the resulting bytes and checks GGUF signature, size and SHA-256.
use crate::android_model_cache::{
    model_path, verify_model, DEFAULT_FREE_RESERVE_BYTES,
};
use crate::android_model_source::{validate_model_source, ApprovedGguf};
use jni::{
    objects::{JClass, JObject, JString, JValue},
    JNIEnv, JavaVM,
};
use std::fs::{self, File};
use std::path::{Path, PathBuf};

fn java_error(_: jni::errors::Error) -> String {
    // Do not leak Java exceptions, signed redirect query parameters or tokens.
    "Android QUIC/HTTP3 jest niedostępny lub transfer się nie powiódł".into()
}

fn private_cache_root(env: &mut JNIEnv<'_>, context: &JObject<'_>)
    -> Result<PathBuf, String>
{
    let dir = env.call_method(context, "getFilesDir", "()Ljava/io/File;", &[])
        .map_err(java_error)?.l().map_err(java_error)?;
    if dir.is_null() {
        return Err("Brak prywatnej pamięci aplikacji Android".into());
    }
    let value = env.call_method(&dir, "getAbsolutePath", "()Ljava/lang/String;", &[])
        .map_err(java_error)?.l().map_err(java_error)?;
    if value.is_null() {
        return Err("Brak ścieżki prywatnych danych Android".into());
    }
    let path = env.get_string(&JString::from(value)).map_err(java_error)?
        .to_string_lossy().into_owned();
    let base = Path::new(&path);
    if !base.is_absolute() {
        return Err("Nieprawidłowy katalog prywatny Android".into());
    }
    Ok(base.join("worker-gguf-models"))
}

fn verify_private_download(root: &Path, source: &ApprovedGguf) -> Result<(), String> {
    let metadata = fs::symlink_metadata(root)
        .map_err(|_| "Brak prywatnego cache GGUF".to_owned())?;
    if !metadata.file_type().is_dir() {
        return Err("Nieprawidłowy katalog cache GGUF".into());
    }
    let target = model_path(root, &source.sha256)?;
    let metadata = fs::symlink_metadata(&target)
        .map_err(|_| "Nie odnaleziono pobranego GGUF".to_owned())?;
    if !metadata.file_type().is_file() || metadata.len() != source.size_bytes {
        return Err("Nieprawidłowy typ lub rozmiar GGUF".into());
    }
    let mut file = File::open(&target)
        .map_err(|_| "Nie można otworzyć pobranego GGUF".to_owned())?;
    if !verify_model(&mut file, source.size_bytes, &source.sha256)
        .map_err(|_| "Nie można zweryfikować GGUF".to_owned())?
    {
        return Err("Suma SHA-256 lub nagłówek GGUF nie zgadzają się".into());
    }
    Ok(())
}

/// Returns the negotiated Android transport only after an independent,
/// byte-for-byte rehash. Never runs on the Android UI thread. IMPORTANT:
/// this primitive alone does NOT prove a current Arena lease or STOP/ACK;
/// activation must happen in a separate trusted coordinator integration.
#[allow(dead_code)]
pub(crate) fn download_pinned_gguf_with_android_quic(
    source: &ApprovedGguf,
) -> Result<String, String> {
    validate_model_source(source)?;
    let android = ndk_context::android_context();
    let vm = unsafe { JavaVM::from_raw(android.vm().cast()) }.map_err(java_error)?;
    let mut env = vm.attach_current_thread().map_err(java_error)?;
    let context = unsafe { JObject::from_raw(android.context().cast()) };
    let loader = env.call_method(
        &context, "getClassLoader", "()Ljava/lang/ClassLoader;", &[],
    ).map_err(java_error)?.l().map_err(java_error)?;
    let name = env.new_string("pl.marvin.aichessarena.worker.WorkerQuicTransport")
        .map_err(java_error)?;
    let class = env.call_method(
        &loader, "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;",
        &[JValue::Object(name.as_ref())],
    ).map_err(java_error)?.l().map_err(java_error)?;
    if class.is_null() {
        return Err("Most QUIC nie został dołączony do APK".into());
    }
    let class = JClass::from(class);
    let url = env.new_string(&source.source_url).map_err(java_error)?;
    let sha = env.new_string(&source.sha256).map_err(java_error)?;
    let size = i64::try_from(source.size_bytes)
        .map_err(|_| "Model GGUF przekracza limit Androida".to_owned())?;
    let reserve = i64::try_from(DEFAULT_FREE_RESERVE_BYTES)
        .map_err(|_| "Nieprawidłowa rezerwa przestrzeni".to_owned())?;
    let negotiated = env.call_static_method(
        &class,
        "downloadApproved",
        "(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;JJ)Ljava/lang/String;",
        &[
            JValue::Object(&context),
            JValue::Object(url.as_ref()),
            JValue::Object(sha.as_ref()),
            JValue::Long(size),
            JValue::Long(reserve),
        ],
    ).map_err(java_error)?.l().map_err(java_error)?;
    if negotiated.is_null() {
        return Err("Android nie zgłosił użytego protokołu".into());
    }
    let protocol = env.get_string(&JString::from(negotiated))
        .map_err(java_error)?.to_string_lossy().into_owned();
    // Avoid propagating arbitrary transport strings from an unexpected stack.
    if protocol.len() > 32 || !protocol.is_ascii()
        || protocol.bytes().any(|b| b < 0x20 || b > 0x7e)
    {
        return Err("Nieprawidłowa odpowiedź transportu".into());
    }
    let root = private_cache_root(&mut env, &context)?;
    verify_private_download(&root, source)?;
    Ok(protocol)
}
