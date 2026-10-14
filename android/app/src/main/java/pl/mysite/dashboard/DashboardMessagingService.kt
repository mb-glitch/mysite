package pl.mysite.dashboard

import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

class DashboardMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        PushClient.register(this)
    }
    override fun onMessageReceived(message: RemoteMessage) {
        val payload = PushPayload.parse(message.data) ?: return
        val config = runCatching { TokenStore(this).load() }.getOrNull() ?: return
        val work = OneTimeWorkRequestBuilder<PushNotificationWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setInputData(workDataOf("delivery_id" to payload.deliveryId, "recipient_id" to payload.recipientId.toString(),
                "target" to payload.target.name.lowercase(), "title" to payload.title, "body" to payload.body,
                // Bind to the original login without storing the DRF token in WorkManager's database.
                "session_hash" to PushNotificationWorker.sessionHash(config)))
            .addTag(PushClient.WORK_TAG).build()
        WorkManager.getInstance(this).enqueueUniqueWork("push-${payload.deliveryId}", ExistingWorkPolicy.KEEP, work)
    }
}
