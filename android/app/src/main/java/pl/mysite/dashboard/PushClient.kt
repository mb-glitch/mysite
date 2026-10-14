package pl.mysite.dashboard

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import androidx.work.WorkManager
import com.google.android.gms.tasks.Tasks
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

object PushClient {
    internal val sessionLock = Any()
    private val worker = Executors.newSingleThreadExecutor()
    const val WORK_TAG = "push-notification"
    fun configured(context: Context): Boolean = BuildConfig.FIREBASE_CONFIGURED && FirebaseApp.getApps(context).isNotEmpty()

    fun channels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("librus", "Librus", NotificationManager.IMPORTANCE_DEFAULT))
        manager.createNotificationChannel(NotificationChannel("monitoring", "Monitoring", NotificationManager.IMPORTANCE_DEFAULT))
    }
    fun register(context: Context, onFinished: (Boolean) -> Unit = {}) {
        val app = context.applicationContext
        if (!configured(app) || !app.getSystemService(NotificationManager::class.java).areNotificationsEnabled()) {
            onFinished(false)
            return
        }
        worker.execute {
            val result = runCatching {
                val config = TokenStore(app).load() ?: error("No signed-in session")
                val prefs = app.getSharedPreferences("push", Context.MODE_PRIVATE)
                val deviceId = prefs.getString("installation_id", null) ?: UUID.randomUUID().toString().also {
                    check(prefs.edit().putString("installation_id", it).commit())
                }
                val firebase = FirebaseMessaging.getInstance()
                firebase.isAutoInitEnabled = true
                val token = Tasks.await(firebase.token, 15, TimeUnit.SECONDS)
                check(TokenStore(app).load() == config)
                DashboardApi.registerPush(config, deviceId, token)
            }
            Handler(Looper.getMainLooper()).post { onFinished(result.isSuccess) }
        }
    }
    fun logout(context: Context, previous: Connection?, clearCredentials: () -> Unit) {
        val app = context.applicationContext
        synchronized(sessionLock) {
            clearCredentials()
            WorkManager.getInstance(app).cancelAllWorkByTag(WORK_TAG)
            app.getSystemService(NotificationManager::class.java).cancelAll()
        }
        val deviceId = app.getSharedPreferences("push", Context.MODE_PRIVATE).getString("installation_id", null)
        worker.execute {
            if (previous != null && deviceId != null) runCatching { DashboardApi.unregisterPush(previous, deviceId) }
            if (configured(app)) runCatching {
                val firebase = FirebaseMessaging.getInstance()
                firebase.isAutoInitEnabled = false
                Tasks.await(firebase.deleteToken(), 15, TimeUnit.SECONDS)
            }
        }
    }
}
