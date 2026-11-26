package pl.marvin.aichessarena.worker

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Persistent Android-only machine-token vault. The AES key never leaves
 * AndroidKeyStore; private SharedPreferences contain only AES-GCM ciphertext.
 *
 * Never fall back to clear-text preferences, Rust keyring mocks or localStorage.
 * A missing or corrupt key is NOT a valid token and must not be silently reset.
 * Same Android package + original signing certificate preserves this on update.
 */
object WorkerCredentialVault {
    private const val KEY_ALIAS = "pl.marvin.aichessarena.worker.credential.v1"
    private const val PREFERENCES = "worker_machine_credential_v1"
    private const val PREF_TOKEN = "worker_token_aes_gcm"
    private const val FORMAT: Byte = 1
    private const val TAG_BITS = 128
    private const val GCM_IV_LENGTH = 12

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore")
        store.load(null)
        val existing = store.getKey(KEY_ALIAS, null)
        if (existing != null) {
            return existing as? SecretKey ?: throw IllegalStateException("Wrong Android Keystore key")
        }
        val generator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore"
        )
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    @JvmStatic
    @Synchronized
    fun save(context: Context, token: String) {
        require(token.length in 24..512 && token.all {
            it.isLetterOrDigit() && it.code < 128 || it == '_' || it == '-'
        }) { "Invalid worker token" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val iv = cipher.iv
        require(iv.size == GCM_IV_LENGTH) { "Unexpected GCM IV" }
        val encrypted = cipher.doFinal(token.toByteArray(StandardCharsets.US_ASCII))
        val data = byteArrayOf(FORMAT) + iv + encrypted
        val encoded = Base64.encodeToString(data, Base64.NO_WRAP)
        if (!context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .edit().putString(PREF_TOKEN, encoded).commit()) {
            throw IllegalStateException("Android failed to persist token")
        }
    }

    @JvmStatic
    @Synchronized
    fun load(context: Context): String? {
        val encoded = context.applicationContext
            .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .getString(PREF_TOKEN, null) ?: return null
        val data = Base64.decode(encoded, Base64.NO_WRAP)
        require(data.size >= 1 + GCM_IV_LENGTH + TAG_BITS / 8 + 1
                && data[0] == FORMAT) { "Corrupted token envelope" }
        val iv = data.copyOfRange(1, 1 + GCM_IV_LENGTH)
        val ciphertext = data.copyOfRange(1 + GCM_IV_LENGTH, data.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, iv))
        // A wrong key, tampered data or restored ciphertext MUST fail closed.
        val bytes = cipher.doFinal(ciphertext)
        val plaintext = String(bytes, StandardCharsets.US_ASCII)
        require(plaintext.length in 24..512 && plaintext.all {
            (it in 'a'..'z') || (it in 'A'..'Z') ||
                (it in '0'..'9') || it == '_' || it == '-'
        }) { "Corrupted token after decryption" }
        bytes.fill(0)
        return plaintext
    }

    @JvmStatic
    @Synchronized
    fun clear(context: Context) {
        if (!context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .edit().remove(PREF_TOKEN).commit()) {
            throw IllegalStateException("Android failed to remove token")
        }
    }
}
