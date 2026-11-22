//! Read-only Android PackageInstaller session inventory.
//! No Session.create(), commit(), abandon(), intent or package installation.
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
