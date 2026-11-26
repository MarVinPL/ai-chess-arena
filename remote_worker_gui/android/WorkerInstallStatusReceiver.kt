package pl.marvin.aichessarena.worker

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build

/**
 * Explicit, non-exported callback for PackageInstaller.Session.commit().
 *
 * Android's install consent UI is always launched by the OS; no silent install.
 * These private preferences are diagnostics, NOT proof of an installed update.
 * Rust re-reads installed versionCode and signer through PackageManager.
 */
class WorkerInstallStatusReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_INSTALL_STATUS) return
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, STATUS_UNKNOWN)
        val session = intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1)
        if (session < 0) return
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
            .putInt("session_id", session)
            .putInt("installer_status", status)
            .commit()

        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            @Suppress("DEPRECATION")
            val confirmation = if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
            } else {
                intent.getParcelableExtra(Intent.EXTRA_INTENT)
            }
            if (confirmation != null) {
                confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(confirmation)
            }
        }
    }

    companion object {
        const val ACTION_INSTALL_STATUS =
            "pl.marvin.aichessarena.worker.INSTALL_STATUS"
        const val PREFERENCES = "worker_android_installer_status_v1"
        const val STATUS_UNKNOWN = -1000
    }
}
