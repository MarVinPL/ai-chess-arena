//! In-process GGUF model lifetime using a pinned Android llama.cpp shim.
//!
//! Not a Tauri command. Must only be called by a trusted coordinator after
//! an authenticated assignment, approved digest and STOP/ACK check. No
//! auto-dispatch, token generation, or cache eviction is enabled here.
//! A tracked file descriptor is held until the native model has been freed.
#![cfg(target_os = "android")]

use crate::android_embedded_llama::{
    native_library_dir, probe_embedded_llama_library,
};
use crate::android_model_cache::model_path;
use crate::android_model_runtime_guard::{open_verified_session, ModelSession};
use std::ffi::{CStr, CString};
use std::fs;
use std::marker::PhantomData;
use std::os::raw::{c_char, c_int, c_void};
use std::os::unix::ffi::OsStrExt;
use std::ptr::NonNull;

const RTLD_NOW: c_int = 2;
const RTLD_LOCAL: c_int = 0;

#[link(name = "dl")]
unsafe extern "C" {
    fn dlopen(path: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, name: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
}
type OpenModel = unsafe extern "C" fn(*const c_char) -> c_int;
type CloseModel = unsafe extern "C" fn();
type IsModelOpen = unsafe extern "C" fn() -> c_int;
type GenerateGreedy = unsafe extern "C" fn(*const c_char, c_int, c_int, *mut c_char, c_int) -> c_int;

struct NativeLibrary(NonNull<c_void>);
impl Drop for NativeLibrary {
    fn drop(&mut self) {
        unsafe { dlclose(self.0.as_ptr()); }
    }
}
impl NativeLibrary {
    fn symbol(&self, name: &CStr) -> Result<*mut c_void, String> {
        let address = unsafe { dlsym(self.0.as_ptr(), name.as_ptr()) };
        if address.is_null() {
            Err("Brak wymaganej funkcji native llama bridge".into())
        } else {
            Ok(address)
        }
    }
}

/// A future embedded inference session owns the native model AND verified
/// file descriptor, never a user-picked path. The Drop order is explicit:
/// unload the C++ model before releasing the verified cache reference.
pub(crate) struct EmbeddedLlamaModel {
    close: CloseModel,
    is_open: IsModelOpen,
    generate: GenerateGreedy,
    lib: NativeLibrary,
    _verified_file: ModelSession,
    _not_send: PhantomData<*mut ()>,
}

impl EmbeddedLlamaModel {
    /// Internal experimental token generation; no dispatch, no remote
    /// authorization, and no chess move is accepted without a legal-list
    /// membership check. No wallclock abort yet: do not enable jobs.
    #[allow(dead_code)]
    pub(crate) fn propose_uci(
        &self, fen: &str, legal_moves: &[String],
    ) -> Result<String, String> {
        if !self.is_open() || fen.len() > 120 || fen.split(' ').count() != 6 ||
           !fen.bytes().all(|b| b.is_ascii_alphanumeric() ||
              matches!(b, b'/' | b' ' | b'-')) ||
           legal_moves.is_empty() || legal_moves.len() > 256 {
            return Err("Nieprawidłowa pozycja lub lista ruchów".into());
        }
        for mv in legal_moves {
            crate::android_local_llama::parse_exact_uci_answer(mv)?;
        }
        let prompt = format!(
            "You are playing chess. Output only ONE move from the legal list in UCI notation. \
FEN: {fen}\nLegal UCI moves: {}\nUCI:",
            legal_moves.join(", "),
        );
        if prompt.len() > 8192 {
            return Err("Zbyt długi prompt do silnika".into());
        }
        let input = CString::new(prompt)
            .map_err(|_| "Nieprawidłowy prompt")?;
        let mut output = [0u8; 128];
        let written = unsafe {
            (self.generate)(input.as_ptr(), input.as_bytes().len() as c_int,
                            16, output.as_mut_ptr().cast(), output.len() as c_int)
        };
        if written < 0 || written as usize >= output.len() {
            return Err("Wbudowany silnik nie wygenerował ruchu".into());
        }
        let text = std::str::from_utf8(&output[..written as usize])
            .map_err(|_| "Niewłaściwy kod UTF-8 modelu")?;
        let proposed = crate::android_local_llama::parse_exact_uci_answer(text)?;
        if !legal_moves.iter().any(|mv| mv == &proposed) {
            return Err("Ruch jest poza listą legalnych ruchów Areny".into());
        }
        Ok(proposed)
    }

    #[allow(dead_code)]
    pub(crate) fn is_open(&self) -> bool {
        unsafe { (self.is_open)() == 1 }
    }
}

impl Drop for EmbeddedLlamaModel {
    fn drop(&mut self) {
        // Inference contexts must be destroyed BEFORE this model is dropped.
        unsafe { (self.close)(); }
        // NativeLibrary and ModelSession are dropped afterwards by Rust.
    }
}

/// Synchronous/possibly expensive: never call from Android UI thread.
/// Caller must retain this object for the *entire* inference lifetime.
#[allow(dead_code)]
pub(crate) fn load_verified_embedded_model(
    digest: &str,
    expected_size: u64,
) -> Result<EmbeddedLlamaModel, String> {
    // A submitted Android installation outlives the temporary update gate.
    // Persisted journals block native models across app restarts and while
    // PackageInstaller is waiting for user consent.
    if crate::android_update_recovery::load_pending()?.is_some()
        || crate::android_update_install_journal::load()?.is_some() {
        return Err("Aktualizacja APK blokuje uruchomienie modelu".into());
    }
    probe_embedded_llama_library()?;
    let root = crate::android_model_quic::android_gguf_cache_root()?;
    let verified = open_verified_session(&root, digest, expected_size)?;
    let path = model_path(&root, digest)?;
    let raw_path = CString::new(path.as_os_str().as_bytes())
        .map_err(|_| "Nieprawidłowa ścieżka GGUF".to_owned())?;
    let library_path = native_library_dir()?.join("libarena_llama_bridge.so");
    let meta = fs::symlink_metadata(&library_path)
        .map_err(|_| "APK nie zawiera natywnego mostu llama.cpp".to_owned())?;
    if !meta.file_type().is_file() || meta.len() < 64 {
        return Err("Niezaufana biblioteka mostu llama.cpp".into());
    }
    let raw_library = CString::new(library_path.as_os_str().as_bytes())
        .map_err(|_| "Nieprawidłowy katalog natywny".to_owned())?;
    let lib = NonNull::new(unsafe {
        dlopen(raw_library.as_ptr(), RTLD_NOW | RTLD_LOCAL)
    }).map(NativeLibrary)
        .ok_or("Nie można załadować natywnego mostu llama.cpp")?;
    let open: OpenModel = unsafe {
        std::mem::transmute(lib.symbol(c"arena_llama_model_open")?)
    };
    let close: CloseModel = unsafe {
        std::mem::transmute(lib.symbol(c"arena_llama_model_close")?)
    };
    let is_open: IsModelOpen = unsafe {
        std::mem::transmute(lib.symbol(c"arena_llama_model_is_open")?)
    };
    let generate: GenerateGreedy = unsafe {
        std::mem::transmute(lib.symbol(c"arena_llama_generate_greedy")?)
    };
    // Require a virgin, single-owner model before starting a new one.
    if unsafe { is_open() } != 0 {
        return Err("Wbudowany silnik ma już otwarty model GGUF".into());
    }
    if unsafe { open(raw_path.as_ptr()) } != 0 {
        return Err("Wbudowany silnik nie załadował modelu GGUF".into());
    }
    Ok(EmbeddedLlamaModel {
        close, is_open, generate, lib, _verified_file: verified,
        _not_send: PhantomData,
    })
}
