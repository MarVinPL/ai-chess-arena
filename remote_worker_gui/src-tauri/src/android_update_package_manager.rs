//! Android PackageManager evidence for a staged APK. Read-only; no install intent.
//! Java/Kotlin generated directories are intentionally not modified: Tauri
//! creates them per build, while JNI accesses the installed Android runtime.

use std::path::Path;
use jni::{objects::{JByteArray, JObject, JObjectArray, JString, JValue}, JNIEnv, JavaVM};
use sha2::{Digest, Sha256};
use crate::android_update_identity::{
    check_platform_apk_identity, IdentityCheck, PlatformApkIdentity, WORKER_PACKAGE_ID,
};

const GET_SIGNING_CERTIFICATES: i32 = 0x08000000; // PackageManager API 28+

fn java_error(_: jni::errors::Error) -> String {
    "Android PackageManager nie udostępnił zweryfikowanych danych APK".into()
}

fn read_android_identity(
    env: &mut JNIEnv<'_>,
    package_info: &JObject<'_>,
) -> Result<PlatformApkIdentity, String> {
    if package_info.is_null() {
        return Err("Android nie znalazł informacji o pakiecie".into());
    }
    let name_obj = env.get_field(package_info, "packageName", "Ljava/lang/String;")
        .map_err(java_error)?.l().map_err(java_error)?;
    if name_obj.is_null() {
        return Err("Brak nazwy pakietu".into());
    }
    let package_name = env.get_string(&JString::from(name_obj))
        .map_err(java_error)?.to_string_lossy().into_owned();
    let raw_version = env.call_method(package_info, "getLongVersionCode", "()J", &[])
        .map_err(java_error)?.j().map_err(java_error)?;
    if raw_version <= 0 { return Err("Nieprawidłowy versionCode APK".into()); }

    let signing_info = env.get_field(package_info, "signingInfo",
        "Landroid/content/pm/SigningInfo;").map_err(java_error)?
        .l().map_err(java_error)?;
    if signing_info.is_null() { return Err("Android nie odczytał podpisu APK".into()); }
    let multiple = env.call_method(&signing_info, "hasMultipleSigners", "()Z", &[])
        .map_err(java_error)?.z().map_err(java_error)?;
    if multiple { return Err("APK podpisany wieloma certyfikatami".into()); }
    let signers = env.call_method(&signing_info, "getApkContentsSigners",
        "()[Landroid/content/pm/Signature;", &[])
        .map_err(java_error)?.l().map_err(java_error)?;
    if signers.is_null() { return Err("Brak podpisujących APK".into()); }
    let array = JObjectArray::from(signers);
    let count = env.get_array_length(&array).map_err(java_error)?;
    if count != 1 { return Err("Niejednoznaczny podpis APK".into()); }
    let signer = env.get_object_array_element(&array, 0).map_err(java_error)?;
    let bytes = env.call_method(&signer, "toByteArray", "()[B", &[])
        .map_err(java_error)?.l().map_err(java_error)?;
    if bytes.is_null() { return Err("Brak certyfikatu APK".into()); }
    let der = env.convert_byte_array(&JByteArray::from(bytes)).map_err(java_error)?;
    if der.is_empty() || der.len() > 128 * 1024 {
        return Err("Nieprawidłowy certyfikat podpisującego".into());
    }
    let sha256 = format!("{:x}", Sha256::digest(der));
    Ok(PlatformApkIdentity {
        package_name,
        long_version_code: raw_version as u64,
        certificate_sha256: sha256,
        platform_signing_verified: true,
        current_signer_count: 1,
    })
}

/// Obtain *both* identities via Android PackageManager. The caller supplies
/// only a private staged path, never a JavaScript URL or a user-chosen file.
/// On any API error, ambiguity, missing cert or downgrade return a denial.
pub(crate) fn inspect_staged_worker_apk(archive: &Path) -> Result<IdentityCheck, String> {
    if !archive.is_absolute() { return Err("Nieprawidłowa ścieżka APK".into()); }
    let archive_path = archive.to_str().ok_or("Nieprawidłowa nazwa APK")?;
    let ctx = ndk_context::android_context();
    let vm = unsafe { JavaVM::from_raw(ctx.vm().cast()) }.map_err(java_error)?;
    let mut env = vm.attach_current_thread().map_err(java_error)?;
    let context = unsafe { JObject::from_raw(ctx.context().cast()) };
    let pm = env.call_method(&context, "getPackageManager",
        "()Landroid/content/pm/PackageManager;", &[])
        .map_err(java_error)?.l().map_err(java_error)?;
    if pm.is_null() { return Err("Brak Android PackageManager".into()); }

    let worker_name = env.new_string(WORKER_PACKAGE_ID).map_err(java_error)?;
    let installed = env.call_method(&pm, "getPackageInfo",
        "(Ljava/lang/String;I)Landroid/content/pm/PackageInfo;",
        &[JValue::Object(worker_name.as_ref()), JValue::Int(GET_SIGNING_CERTIFICATES)])
        .map_err(java_error)?.l().map_err(java_error)?;
    let installed_identity = read_android_identity(&mut env, &installed)?;

    let archive_name = env.new_string(archive_path).map_err(java_error)?;
    let candidate = env.call_method(&pm, "getPackageArchiveInfo",
        "(Ljava/lang/String;I)Landroid/content/pm/PackageInfo;",
        &[JValue::Object(archive_name.as_ref()), JValue::Int(GET_SIGNING_CERTIFICATES)])
        .map_err(java_error)?.l().map_err(java_error)?;
    let candidate_identity = read_android_identity(&mut env, &candidate)?;
    Ok(check_platform_apk_identity(&installed_identity, &candidate_identity))
}
