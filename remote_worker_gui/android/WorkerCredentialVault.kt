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
    // SharedPreferences can update memory even when disk commit fails.
    private var uncertain = false

    private fun requireCertain() {
        check(!uncertain) { "Credential commit outcome uncertain; restart and reconcile" }
    }

    private fun key(allowCreate: Boolean): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore")
        store.load(null)
        val existing = store.getKey(KEY_ALIAS, null)
        if (existing != null) {
            return existing as? SecretKey ?: throw IllegalStateException("Wrong Android Keystore key")
        }
        check(allowCreate) { "Android Keystore key missing" }
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
        requireCertain()
        require(token.length in 24..512 && token.all {
            it.isLetterOrDigit() && it.code < 128 || it == '_' || it == '-'
        }) { "Invalid worker token" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        val prefs = context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        // Verify existing ciphertext before replacing it; corruption is not absence.
        if (prefs.contains(PREF_TOKEN)) check(load(context) != null)
        cipher.init(Cipher.ENCRYPT_MODE, key(!prefs.contains(PREF_TOKEN)))
        val iv = cipher.iv
        require(iv.size == GCM_IV_LENGTH) { "Unexpected GCM IV" }
        val raw = token.toByteArray(StandardCharsets.US_ASCII)
        val encrypted = try { cipher.doFinal(raw) } finally { raw.fill(0) }
        val data = byteArrayOf(FORMAT) + iv + encrypted
        val encoded = Base64.encodeToString(data, Base64.NO_WRAP)
        uncertain = true
        if (!prefs.edit().putString(PREF_TOKEN, encoded).commit()) {
            throw IllegalStateException("Android failed to persist token")
        }
        uncertain = false
    }

    @JvmStatic
    @Synchronized
    fun load(context: Context): String? {
        requireCertain()
        val prefs = context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        if (!prefs.contains(PREF_TOKEN)) return null
        val encoded = checkNotNull(prefs.getString(PREF_TOKEN, null))
        require(encoded.length <= 724) { "Corrupted token envelope" }
        val data = Base64.decode(encoded, Base64.NO_WRAP)
        require(data.size in (1 + GCM_IV_LENGTH + TAG_BITS / 8 + 24)..(1 + GCM_IV_LENGTH + TAG_BITS / 8 + 512)
                && data[0] == FORMAT) { "Corrupted token envelope" }
        val iv = data.copyOfRange(1, 1 + GCM_IV_LENGTH)
        val ciphertext = data.copyOfRange(1 + GCM_IV_LENGTH, data.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(false), GCMParameterSpec(TAG_BITS, iv))
        // A wrong key, tampered data or restored ciphertext MUST fail closed.
        val bytes = cipher.doFinal(ciphertext)
        val plaintext = try { String(bytes, StandardCharsets.US_ASCII) } finally { bytes.fill(0) }
        require(plaintext.length in 24..512 && plaintext.all {
            (it in 'a'..'z') || (it in 'A'..'Z') ||
                (it in '0'..'9') || it == '_' || it == '-'
        }) { "Corrupted token after decryption" }
        return plaintext
    }

    @JvmStatic
    @Synchronized
    fun clear(context: Context) {
        // Do not discard an unreadable credential via normal UI operations.
        if (load(context) == null) return
        uncertain = true
        if (!context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .edit().remove(PREF_TOKEN).commit()) {
            throw IllegalStateException("Android failed to remove token")
        }
        uncertain = false
    }

    // Separate journal namespace and AES key per slot; never reuse token storage.
    private fun journalSlot(slot: String): String {
        require(slot == "pending-hold-v1" || slot == "pending-install-v1" || slot == "native-stop-v1")
        return slot
    }

    private fun journalKey(slot: String, create: Boolean): SecretKey {
        val alias = KEY_ALIAS + "." + journalSlot(slot)
        val store = KeyStore.getInstance("AndroidKeyStore")
        store.load(null)
        val existing = store.getKey(alias, null)
        if (existing != null) return existing as? SecretKey ?: error("Invalid journal key")
        check(create) { "Missing journal key" }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(KeyGenParameterSpec.Builder(alias,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256).build())
        return generator.generateKey()
    }

    @JvmStatic
    @Synchronized
    fun journalRoot(context: Context): String = context.applicationContext.noBackupFilesDir.absolutePath

    @JvmStatic
    @Synchronized
    fun journalLoad(context: Context, slot: String): String? {
        requireCertain()
        val name = journalSlot(slot)
        val prefs = context.applicationContext.getSharedPreferences("worker_update_journals_v1", Context.MODE_PRIVATE)
        if (!prefs.contains(name)) return null
        val encoded = checkNotNull(prefs.getString(name, null))
        require(encoded.length <= 5500)
        val data = Base64.decode(encoded, Base64.NO_WRAP)
        require(data.size in 30..4125 && data[0] == FORMAT)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, journalKey(name, false),
            GCMParameterSpec(TAG_BITS, data.copyOfRange(1, 13)))
        cipher.updateAAD(name.toByteArray(StandardCharsets.US_ASCII))
        val raw = cipher.doFinal(data.copyOfRange(13, data.size))
        return try { String(raw, StandardCharsets.UTF_8) } finally { raw.fill(0) }
    }

    @JvmStatic
    @Synchronized
    fun journalInsert(context: Context, slot: String, value: String) {
        val name = journalSlot(slot)
        check(journalLoad(context, name) == null)
        val raw = value.toByteArray(StandardCharsets.UTF_8)
        val encoded = try {
            require(raw.size in 1..4096)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, journalKey(name, true))
            require(cipher.iv.size == GCM_IV_LENGTH)
            cipher.updateAAD(name.toByteArray(StandardCharsets.US_ASCII))
            Base64.encodeToString(byteArrayOf(FORMAT) + cipher.iv + cipher.doFinal(raw), Base64.NO_WRAP)
        } finally { raw.fill(0) }
        uncertain = true
        check(context.applicationContext.getSharedPreferences("worker_update_journals_v1", Context.MODE_PRIVATE)
            .edit().putString(name, encoded).commit())
        uncertain = false
    }

    @JvmStatic
    @Synchronized
    fun journalDelete(context: Context, slot: String, expected: String) {
        require(slot != "native-stop-v1")
        val name = journalSlot(slot)
        check(journalLoad(context, name) == expected)
        uncertain = true
        check(context.applicationContext.getSharedPreferences("worker_update_journals_v1", Context.MODE_PRIVATE)
            .edit().remove(name).commit())
        uncertain = false
    }
}
