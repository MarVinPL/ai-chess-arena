//! Machine-token persistence using AndroidKeyStore + AES/GCM protected
//! SharedPreferences. No mock store or unencrypted file fallback.
use jni::{
    objects::{JClass, JObject, JString, JValue},
    JNIEnv, JavaVM,
};

pub(crate) struct AndroidTokenEntry;

fn java_failure(_: jni::errors::Error) -> String {
    // Do not forward Java exception messages because they might contain
    // credential metadata. Errors are intentionally generic and fail closed.
    "Android Keystore jest niedostępny albo token jest uszkodzony".to_owned()
}

fn with_vault<T>(
    operation: impl FnOnce(&mut JNIEnv<'_>, &JClass<'_>, &JObject<'_>) -> Result<T, String>,
) -> Result<T, String> {
    let android = ndk_context::android_context();
    let vm = unsafe { JavaVM::from_raw(android.vm().cast()) }.map_err(java_failure)?;
    let mut env = vm.attach_current_thread().map_err(java_failure)?;
    let context = unsafe { JObject::from_raw(android.context().cast()) };
    let loader = env.call_method(
        &context, "getClassLoader", "()Ljava/lang/ClassLoader;", &[],
    ).map_err(java_failure)?.l().map_err(java_failure)?;
    let name = env.new_string("pl.marvin.aichessarena.worker.WorkerCredentialVault")
        .map_err(java_failure)?;
    // Attached Rust threads may not have the application class loader; resolve
    // the bundled Kotlin helper through the Android Context explicitly.
    let clazz = env.call_method(
        &loader, "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;",
        &[JValue::Object(name.as_ref())],
    ).map_err(java_failure)?.l().map_err(java_failure)?;
    if clazz.is_null() {
        return Err("Brak natywnego magazynu tokenu Android".into());
    }
    operation(&mut env, &JClass::from(clazz), &context)
}

impl AndroidTokenEntry {
    pub(crate) fn get_password(&self) -> Result<String, String> {
        with_vault(|env, class, context| {
            let result = env.call_static_method(
                class, "load", "(Landroid/content/Context;)Ljava/lang/String;",
                &[JValue::Object(context)],
            ).map_err(java_failure)?.l().map_err(java_failure)?;
            if result.is_null() {
                return Err("Brak zapisanego tokenu Workera na tym telefonie".into());
            }
            let token = env.get_string(&JString::from(result))
                .map_err(java_failure)?.to_string_lossy().into_owned();
            if !(24..=512).contains(&token.len())
                || !token.is_ascii()
                || !token.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
            {
                return Err("Nieprawidłowe dane magazynu poświadczeń".into());
            }
            Ok(token)
        })
    }

    pub(crate) fn set_password(&self, token: &str) -> Result<(), String> {
        with_vault(|env, class, context| {
            let value = env.new_string(token).map_err(java_failure)?;
            env.call_static_method(
                class, "save", "(Landroid/content/Context;Ljava/lang/String;)V",
                &[JValue::Object(context), JValue::Object(value.as_ref())],
            ).map_err(java_failure)?;
            Ok(())
        })
    }

    pub(crate) fn delete_credential(&self) -> Result<(), String> {
        with_vault(|env, class, context| {
            env.call_static_method(
                class, "clear", "(Landroid/content/Context;)V",
                &[JValue::Object(context)],
            ).map_err(java_failure)?;
            Ok(())
        })
    }
}
