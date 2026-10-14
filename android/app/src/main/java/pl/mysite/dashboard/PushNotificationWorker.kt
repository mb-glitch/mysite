package pl.mysite.dashboard

import android.Manifest
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.security.MessageDigest

class PushNotificationWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val data = listOf("delivery_id", "recipient_id", "target", "title", "body")
            .associateWith { inputData.getString(it) ?: "" }
        val payload = PushPayload.parse(data) ?: return Result.failure()
        val store = TokenStore(applicationContext)
        val config = runCatching { store.load() }.getOrNull() ?: return Result.success()
        if (sessionHash(config) != inputData.getString("session_hash")) return Result.success()
        try {
            val profile = DashboardApi.profile(config)
            if (!payload.allowed(profile)) return Result.success()
            val manager = applicationContext.getSystemService(NotificationManager::class.java)
            if (!manager.areNotificationsEnabled()) return Result.success()
            if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(applicationContext,
                    Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return Result.success()
            val intent = Intent(applicationContext, MainActivity::class.java)
                .putExtra("dashboard", payload.target.name.lowercase())
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            val tap = PendingIntent.getActivity(applicationContext, payload.deliveryId.hashCode(), intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            val notification = NotificationCompat.Builder(applicationContext, payload.target.name.lowercase())
                .setSmallIcon(R.drawable.ic_notification_home).setContentTitle(payload.title).setContentText(payload.body)
                .setContentIntent(tap).setAutoCancel(true).setOnlyAlertOnce(true)
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE).build()
            synchronized(PushClient.sessionLock) {
                if (store.load() != config) return Result.success()
                manager.notify(payload.deliveryId, 0, notification)
            }
            return Result.success()
        } catch (error: Exception) {
            if (error is ApiException && error.statusCode in listOf(401, 403, 404)) return Result.success()
            return if (runAttemptCount < 2) Result.retry() else Result.failure()
        }
    }
    companion object {
        fun sessionHash(config: Connection): String = MessageDigest.getInstance("SHA-256")
            .digest("${config.baseUrl}\n${config.token}".toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}
