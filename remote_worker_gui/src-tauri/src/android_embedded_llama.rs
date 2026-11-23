//! Embedded llama.cpp library availability attestation on Android.
//!
//! This is a **read-only, fail-closed preflight**, not an inference engine.
//! Only Android's own ApplicationInfo.nativeLibraryDir is examined. A remote
//! job, JavaScript, downloaded GGUF, or environment variable cannot choose
//! the dynamic library. A localhost llama-server alias is not native evidence.
//!
//! Until a pinned upstream llama.cpp build is packaged and its ABI reviewed,
//! this will correctly report unavailable. NEVER activate production dispatch
//! based on this diagnostic alone.
#[cfg(target_os = "android")]
use jni::{objects::{JObject, JString}, JavaVM};
#[cfg(target_os = "android")]
use std::ffi::{CStr, CString};
#[cfg(target_os = "android")]
use std::os::raw::{c_char, c_int, c_void};
#[cfg(target_os = "android")]
use std::path::{Path, PathBuf};
#[cfg(target_os = "android")]
use std::fs;

#[cfg(target_os = "android")]
const RTLD_NOW: c_int = 2;
#[cfg(target_os = "android")]
const RTLD_LOCAL: c_int = 0;

#[cfg(target_os = "android")]
#[link(name = "dl")]
unsafe extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
}

#[cfg(target_os = "android")]
fn native_library_dir() -> Result<PathBuf, String> {
    let context = ndk_context::android_context();
    let vm = unsafe { JavaVM::from_raw(context.vm().cast()) }
        .map_err(|_| "Brak środowiska Android JNI")?;
    let mut env = vm.attach_current_thread()
        .map_err(|_| "Nie można odczytać bibliotek aplikacji")?;
    let context = unsafe { JObject::from_raw(context.context().cast()) };
    let app_info = env.call_method(
        &context, "getApplicationInfo",
        "()Landroid/content/pm/ApplicationInfo;", &[],
    ).map_err(|_| "Brak danych pakietu Android")?.l()
        .map_err(|_| "Brak danych pakietu Android")?;
    if app_info.is_null() {
        return Err("Nieznany pakiet Android".into());
    }
    let dir = env.get_field(
        &app_info, "nativeLibraryDir", "Ljava/lang/String;",
    ).map_err(|_| "Brak katalogu natywnych bibliotek")?.l()
        .map_err(|_| "Brak katalogu natywnych bibliotek")?;
    if dir.is_null() {
        return Err("Brak katalogu natywnych bibliotek".into());
    }
    let dir = env.get_string(&JString::from(dir))
        .map_err(|_| "Nieprawidłowy katalog natywny")?
        .to_string_lossy().into_owned();
    let root = PathBuf::from(dir);
    if !root.is_absolute() {
        return Err("Nieprawidłowa ścieżka natywnego katalogu".into());
    }
    Ok(root)
}

#[cfg(target_os = "android")]
fn inspect_library(path: &Path) -> Result<(), String> {
    if path.file_name().and_then(|name| name.to_str()) != Some("libllama.so") {
        return Err("Niedozwolona biblioteka modelu".into());
    }
    let info = fs::symlink_metadata(path)
        .map_err(|_| "APK nie zawiera wbudowanej libllama.so".to_string())?;
    if !info.file_type().is_file() || info.len() < 64 {
        return Err("Niewłaściwy natywny artefakt llama.cpp".into());
    }
    let raw = CString::new(path.to_string_lossy().as_bytes())
        .map_err(|_| "Nieprawidłowa ścieżka biblioteki".to_owned())?;
    // dlopen uses the platform Android linker. Never invoke inference here.
    let handle = unsafe { dlopen(raw.as_ptr(), RTLD_NOW | RTLD_LOCAL) };
    if handle.is_null() {
        return Err("Biblioteka llama.cpp nie ładuje się na tym Androidzie".into());
    }
    struct Library(*mut c_void);
    impl Drop for Library {
        fn drop(&mut self) { unsafe { dlclose(self.0); } }
    }
    let library = Library(handle);
    const SYMBOLS: [&CStr; 5] = [
        c"llama_backend_init",
        c"llama_model_load_from_file",
        c"llama_new_context_with_model",
        c"llama_decode",
        c"llama_sampler_init_greedy",
    ];
    for symbol in SYMBOLS {
        if unsafe { dlsym(library.0, symbol.as_ptr()) }.is_null() {
            return Err("Niekompatybilna biblioteka llama.cpp: brak funkcji ABI".into());
        }
    }
    Ok(())
}

/// This diagnostic alone NEVER authorizes a model or a chess assignment.
#[cfg(target_os = "android")]
#[allow(dead_code)]
pub(crate) fn probe_embedded_llama_library() -> Result<(), String> {
    let root = native_library_dir()?;
    inspect_library(&root.join("libllama.so"))
}

#[cfg(not(target_os = "android"))]
#[allow(dead_code)]
pub(crate) fn probe_embedded_llama_library() -> Result<(), String> {
    Err("Wbudowany llama.cpp jest dostępny wyłącznie w Androidzie".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn desktop_never_claims_embedded_android_llama() {
        #[cfg(not(target_os = "android"))]
        assert!(probe_embedded_llama_library().is_err());
    }
}
