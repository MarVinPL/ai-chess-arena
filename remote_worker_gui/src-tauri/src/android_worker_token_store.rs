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
    let result = (|| {
        let context = unsafe { JObject::from_raw(android.context().cast()) };
        let loader = env
            .call_method(&context, "getClassLoader", "()Ljava/lang/ClassLoader;", &[])
            .map_err(java_failure)?
            .l()
            .map_err(java_failure)?;
        let name = env
            .new_string("pl.marvin.aichessarena.worker.WorkerCredentialVault")
            .map_err(java_failure)?;
        // Attached Rust threads may not have the application class loader; resolve
        // the bundled Kotlin helper through the Android Context explicitly.
        let clazz = env
            .call_method(
                &loader,
                "loadClass",
                "(Ljava/lang/String;)Ljava/lang/Class;",
                &[JValue::Object(name.as_ref())],
            )
            .map_err(java_failure)?
            .l()
            .map_err(java_failure)?;
        if clazz.is_null() {
            return Err("Brak natywnego magazynu tokenu Android".into());
        }
        operation(&mut env, &JClass::from(clazz), &context)
    })();
    if env.exception_check().map_err(java_failure)? {
        env.exception_clear().map_err(java_failure)?;
        return Err("Android Keystore jest niedostępny albo token jest uszkodzony".into());
    }
    result
}

impl AndroidTokenEntry {
    pub(crate) fn read_optional(&self) -> Result<Option<String>, String> {
        with_vault(|env, class, context| {
            let result = env
                .call_static_method(
                    class,
                    "load",
                    "(Landroid/content/Context;)Ljava/lang/String;",
                    &[JValue::Object(context)],
                )
                .map_err(java_failure)?
                .l()
                .map_err(java_failure)?;
            if result.is_null() {
                return Ok(None);
            }
            let token = env
                .get_string(&JString::from(result))
                .map_err(java_failure)?
                .to_string_lossy()
                .into_owned();
            if !(24..=512).contains(&token.len())
                || !token.is_ascii()
                || !token
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
            {
                return Err("Nieprawidłowe dane magazynu poświadczeń".into());
            }
            Ok(Some(token))
        })
    }

    pub(crate) fn set_password(&self, token: &str) -> Result<(), String> {
        with_vault(|env, class, context| {
            let value = env.new_string(token).map_err(java_failure)?;
            env.call_static_method(
                class,
                "save",
                "(Landroid/content/Context;Ljava/lang/String;)V",
                &[JValue::Object(context), JValue::Object(value.as_ref())],
            )
            .map_err(java_failure)?;
            Ok(())
        })
    }

    pub(crate) fn delete_credential(&self) -> Result<(), String> {
        with_vault(|env, class, context| {
            env.call_static_method(
                class,
                "clear",
                "(Landroid/content/Context;)V",
                &[JValue::Object(context)],
            )
            .map_err(java_failure)?;
            Ok(())
        })
    }
}

pub(crate) fn journal_root() -> Result<std::path::PathBuf, String> {
    with_vault(|env, class, context| {
        let value = env
            .call_static_method(
                class,
                "journalRoot",
                "(Landroid/content/Context;)Ljava/lang/String;",
                &[JValue::Object(context)],
            )
            .map_err(java_failure)?
            .l()
            .map_err(java_failure)?;
        let path = env
            .get_string(&JString::from(value))
            .map_err(java_failure)?
            .to_string_lossy()
            .into_owned();
        Ok(std::path::PathBuf::from(path))
    })
}

pub(crate) fn journal_read(slot: &str) -> Result<Option<String>, String> {
    with_vault(|env, class, context| {
        let name = env.new_string(slot).map_err(java_failure)?;
        let value = env
            .call_static_method(
                class,
                "journalLoad",
                "(Landroid/content/Context;Ljava/lang/String;)Ljava/lang/String;",
                &[JValue::Object(context), JValue::Object(name.as_ref())],
            )
            .map_err(java_failure)?
            .l()
            .map_err(java_failure)?;
        if value.is_null() {
            return Ok(None);
        }
        let raw = env
            .get_string(&JString::from(value))
            .map_err(java_failure)?
            .to_string_lossy()
            .into_owned();
        Ok(Some(raw))
    })
}

pub(crate) fn journal_mutate(method: &str, slot: &str, raw: &str) -> Result<(), String> {
    with_vault(|env, class, context| {
        let name = env.new_string(slot).map_err(java_failure)?;
        let value = env.new_string(raw).map_err(java_failure)?;
        env.call_static_method(
            class,
            method,
            "(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;)V",
            &[
                JValue::Object(context),
                JValue::Object(name.as_ref()),
                JValue::Object(value.as_ref()),
            ],
        )
        .map_err(java_failure)?;
        Ok(())
    })
}
