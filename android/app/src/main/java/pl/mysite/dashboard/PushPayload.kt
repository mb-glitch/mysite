package pl.mysite.dashboard

import org.json.JSONObject
import java.util.UUID

data class PushPayload(val deliveryId: String, val recipientId: Long, val target: Dashboard,
                       val title: String, val body: String) {
    fun allowed(profile: JSONObject): Boolean {
        if (profile.optLong("id", -1) != recipientId) return false
        val access = DashboardAccess.parse(profile)
        return if (target == Dashboard.LIBRUS) access.librus else access.monitoring
    }
    companion object {
        fun parse(data: Map<String, String>): PushPayload? = runCatching {
            val id = UUID.fromString(data.getValue("delivery_id")).toString()
            val user = data.getValue("recipient_id").toLong()
            require(user > 0)
            val target = when (data["target"]) {
                "librus" -> Dashboard.LIBRUS
                "monitoring" -> Dashboard.MONITORING
                else -> error("Unknown notification target")
            }
            PushPayload(id, user, target, data.getValue("title").take(120), data.getValue("body").take(255))
        }.getOrNull()
    }
}
