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
    lib: NativeLibrary,
    _verified_file: ModelSession,
    _not_send: PhantomData<*mut ()>,
}

impl EmbeddedLlamaModel {
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
    // Require a virgin, single-owner model before starting a new one.
    if unsafe { is_open() } != 0 {
        return Err("Wbudowany silnik ma już otwarty model GGUF".into());
    }
    if unsafe { open(raw_path.as_ptr()) } != 0 {
        return Err("Wbudowany silnik nie załadował modelu GGUF".into());
    }
    Ok(EmbeddedLlamaModel {
        close, is_open, lib, _verified_file: verified,
        _not_send: PhantomData,
    })
}
