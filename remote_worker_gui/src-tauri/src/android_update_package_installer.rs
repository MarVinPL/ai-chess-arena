//! Android PackageInstaller session inventory and uncommitted APK staging.
//! No Session.commit(), intent or package installation.
//! Sessions can disappear after completion; absence is NEVER success or
//! evidence that the coordinator hold may be cleared.

use jni::{objects::{JObject, JString}, JavaVM, JNIEnv};
use crate::android_update_identity::WORKER_PACKAGE_ID;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum SessionObservation {
    NoVisibleSession,
    Uncommitted,
    CommittedOrAwaitingConsent,
    Ambiguous,
}

fn jni_error(_: jni::errors::Error) -> String {
    "Nie można odczytać własnych sesji instalatora Android".into()
}

fn java_string(env: &mut JNIEnv<'_>, object: JObject<'_>) -> Result<Option<String>, String> {
    if object.is_null() { return Ok(None); }
    let value = env.get_string(&JString::from(object))
        .map_err(jni_error)?.to_string_lossy().into_owned();
    Ok(Some(value))
}

/// Classify only sessions belonging to this installed app and naming Worker
/// as target; unexpected owners/targets fail closed as Ambiguous.
pub(crate) fn observe_own_session() -> Result<SessionObservation, String> {
    let ctx = ndk_context::android_context();
    let vm = unsafe { JavaVM::from_raw(ctx.vm().cast()) }.map_err(jni_error)?;
    let mut env = vm.attach_current_thread().map_err(jni_error)?;
    let context = unsafe { JObject::from_raw(ctx.context().cast()) };
    let package = env.call_method(&context, "getPackageName", "()Ljava/lang/String;", &[])
        .map_err(jni_error)?.l().map_err(jni_error)?;
    let own_package = java_string(&mut env, package)?
        .ok_or("Android nie udostępnił nazwy własnej aplikacji")?;
    if own_package != WORKER_PACKAGE_ID {
        return Err("Uruchomiono inną aplikację niż Worker".into());
    }

    let pm = env.call_method(&context, "getPackageManager",
        "()Landroid/content/pm/PackageManager;", &[])
        .map_err(jni_error)?.l().map_err(jni_error)?;
    if pm.is_null() { return Err("Brak Android PackageManager".into()); }
    let installer = env.call_method(&pm, "getPackageInstaller",
        "()Landroid/content/pm/PackageInstaller;", &[])
        .map_err(jni_error)?.l().map_err(jni_error)?;
    if installer.is_null() { return Err("Brak Android PackageInstaller".into()); }
    let sessions = env.call_method(&installer, "getMySessions",
        "()Ljava/util/List;", &[])
        .map_err(jni_error)?.l().map_err(jni_error)?;
    if sessions.is_null() { return Err("Brak listy własnych sesji Android".into()); }
    let count = env.call_method(&sessions, "size", "()I", &[])
        .map_err(jni_error)?.i().map_err(jni_error)?;
    if count < 0 || count > 1 {
        // Do not choose an arbitrary session if another install is ongoing.
        return Ok(SessionObservation::Ambiguous);
    }
    if count == 0 { return Ok(SessionObservation::NoVisibleSession); }

    let info = env.call_method(&sessions, "get", "(I)Ljava/lang/Object;",
        &[jni::objects::JValue::Int(0)])
        .map_err(jni_error)?.l().map_err(jni_error)?;
    if info.is_null() { return Err("Nieprawidłowa sesja instalatora".into()); }
    let package = env.call_method(&info, "getAppPackageName",
        "()Ljava/lang/String;", &[])
        .map_err(jni_error)?.l().map_err(jni_error)?;
    let target = java_string(&mut env, package)?;
    let installer_name = env.call_method(&info, "getInstallerPackageName",
        "()Ljava/lang/String;", &[])
        .map_err(jni_error)?.l().map_err(jni_error)?;
    let owner = java_string(&mut env, installer_name)?;
    if target.as_deref() != Some(WORKER_PACKAGE_ID)
        || owner.as_deref() != Some(WORKER_PACKAGE_ID) {
        return Ok(SessionObservation::Ambiguous);
    }
    let session_id = env.call_method(&info, "getSessionId", "()I", &[])
        .map_err(jni_error)?.i().map_err(jni_error)?;
    if session_id < 0 { return Err("Nieprawidłowy identyfikator sesji".into()); }
    let committed = env.call_method(&info, "isCommitted", "()Z", &[])
        .map_err(jni_error)?.z().map_err(jni_error)?;
    Ok(if committed {
        SessionObservation::CommittedOrAwaitingConsent
    } else {
        SessionObservation::Uncommitted
    })
}


/* Native PackageInstaller staging. This is deliberately internal: the
 * caller must have checked the downloaded bytes and the installed + staged
 * certificates, package and versionCode immediately beforehand. The session
 * remains uncommitted until a durable Android status receiver exists.
 */
#[allow(dead_code)]
pub(crate) fn stage_verified_apk_session(
    apk: &std::path::Path,
    verified_identity: bool,
    coordinator_reserved: bool,
    expected_sha256: &str,
) -> Result<i32, String> {
    use std::io::Read;
    use jni::objects::JValue;
    use sha2::{Digest, Sha256};

    if !verified_identity || !coordinator_reserved {
        return Err("Brak zweryfikowanego podpisu lub rezerwacji aktualizacji".into());
    }
    if expected_sha256.len() != 64 || !expected_sha256.bytes().all(|c|
        c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        return Err("Nieprawidłowy SHA-256 APK".into());
    }
    let meta = std::fs::symlink_metadata(apk)
        .map_err(|_| "Brak pobranego APK")?;
    if !apk.is_absolute() || !meta.file_type().is_file()
        || !(1024..=200 * 1024 * 1024).contains(&meta.len()) {
        return Err("Nieprawidłowy plik APK do instalacji".into());
    }
    let ctx = ndk_context::android_context();
    let vm = unsafe { JavaVM::from_raw(ctx.vm().cast()) }.map_err(jni_error)?;
    let mut env = vm.attach_current_thread().map_err(jni_error)?;
    let context = unsafe { JObject::from_raw(ctx.context().cast()) };
    let pm = env.call_method(&context, "getPackageManager",
        "()Landroid/content/pm/PackageManager;", &[])
        .map_err(jni_error)?.l().map_err(jni_error)?;
    let allowed = env.call_method(&pm, "canRequestPackageInstalls", "()Z", &[])
        .map_err(jni_error)?.z().map_err(jni_error)?;
    if !allowed { return Err("Android nie zezwolił na instalację aplikacji".into()); }
    let installer = env.call_method(&pm, "getPackageInstaller",
        "()Landroid/content/pm/PackageInstaller;", &[])
        .map_err(jni_error)?.l().map_err(jni_error)?;
    if installer.is_null() { return Err("Brak Android PackageInstaller".into()); }
    if observe_own_session()? != SessionObservation::NoVisibleSession {
        return Err("Wcześniejsza sesja Android wymaga uzgodnienia".into());
    }
    let params = env.new_object("android/content/pm/PackageInstaller$SessionParams", "(I)V",
        &[JValue::Int(1)]).map_err(jni_error)?;
    let package_name = env.new_string(WORKER_PACKAGE_ID).map_err(jni_error)?;
    env.call_method(&params, "setAppPackageName",
        "(Ljava/lang/String;)V", &[JValue::Object(package_name.as_ref())])
        .map_err(jni_error)?;
    let id = env.call_method(&installer, "createSession",
        "(Landroid/content/pm/PackageInstaller$SessionParams;)I",
        &[JValue::Object(&params)]).map_err(jni_error)?.i().map_err(jni_error)?;
    if id < 0 { return Err("Android zwrócił nieprawidłową sesję".into()); }

    // On error abandon only our newly-created, uncommitted session. The
    // coordinator hold remains untouched for explicit reconciliation.
    let result = (|| -> Result<(), String> {
        let session = env.call_method(&installer, "openSession",
            "(I)Landroid/content/pm/PackageInstaller$Session;",
            &[JValue::Int(id)]).map_err(jni_error)?.l().map_err(jni_error)?;
        let result = (|| -> Result<(), String> {
            let entry = env.new_string("base.apk").map_err(jni_error)?;
            let stream = env.call_method(&session, "openWrite",
                "(Ljava/lang/String;JJ)Ljava/io/OutputStream;",
                &[JValue::Object(entry.as_ref()), JValue::Long(0),
                    JValue::Long(meta.len() as i64)])
                .map_err(jni_error)?.l().map_err(jni_error)?;
            let copy_result = (|| -> Result<(), String> {
                let mut file = std::fs::File::open(apk)
                    .map_err(|_| "Nie można odczytać zweryfikowanego APK")?;
                let mut buffer = [0u8; 64 * 1024];
                let mut written = 0u64;
                let mut digest = Sha256::new();
                loop {
                    let count = file.read(&mut buffer)
                        .map_err(|_| "Nie można odczytać APK")?;
                    if count == 0 { break; }
                    written = written.checked_add(count as u64)
                        .ok_or("Nieprawidłowy rozmiar APK")?;
                    if written > meta.len() { return Err("APK zmienił rozmiar".into()); }
                    digest.update(&buffer[..count]);
                    let bytes = env.byte_array_from_slice(&buffer[..count])
                        .map_err(jni_error)?;
                    env.call_method(&stream, "write", "([BII)V",
                        &[JValue::Object(bytes.as_ref()), JValue::Int(0),
                            JValue::Int(count as i32)]).map_err(jni_error)?;
                }
                if written != meta.len() { return Err("APK jest niekompletny".into()); }
                if format!("{:x}", digest.finalize()) != expected_sha256 {
                    return Err("APK zmienił zawartość podczas przekazania Androidowi".into());
                }
                env.call_method(&session, "fsync", "(Ljava/io/OutputStream;)V",
                    &[JValue::Object(&stream)]).map_err(jni_error)?;
                Ok(())
            })();
            let _ = env.call_method(&stream, "close", "()V", &[]);
            copy_result
        })();
        let _ = env.call_method(&session, "close", "()V", &[]);
        result
    })();
    if result.is_err() {
        if let Ok(session) = env.call_method(&installer, "openSession",
            "(I)Landroid/content/pm/PackageInstaller$Session;",
            &[JValue::Int(id)]).and_then(|v| v.l()) {
            let _ = env.call_method(&session, "abandon", "()V", &[]);
            let _ = env.call_method(&session, "close", "()V", &[]);
        }
    }
    result.map(|()| id)
}

pub(crate) fn observation_is_terminal(_observation: SessionObservation) -> bool {
    // Even "no visible session" cannot prove success or safe cancellation.
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn no_session_observation_can_release_hold_or_confirm_install() {
        for observation in [
            SessionObservation::NoVisibleSession,
            SessionObservation::Uncommitted,
            SessionObservation::CommittedOrAwaitingConsent,
            SessionObservation::Ambiguous,
        ] {
            assert!(!observation_is_terminal(observation));
        }
    }
}
