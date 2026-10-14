package pl.mysite.dashboard

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PushPayloadTest {
    private val data = mapOf("delivery_id" to "cfc32ec2-1c97-47f5-a9bf-9e8c497c482a",
        "recipient_id" to "7", "target" to "librus", "title" to "Librus", "body" to "Nowy wpis")
    private fun profile(id: Int, librus: Boolean, monitoring: Boolean) = JSONObject().put("id", id)
        .put("permissions", JSONObject().put("librus", librus).put("monitoring", monitoring))

    @Test fun currentRecipientAndGroupAreRequiredToShowNotification() {
        val message = PushPayload.parse(data)!!
        assertTrue(message.allowed(profile(7, true, false)))
        assertFalse(message.allowed(profile(8, true, true)))
        assertFalse(message.allowed(profile(7, false, true)))
    }
    @Test fun monitoringPushRequiresMonitoringPermission() {
        val message = PushPayload.parse(data + ("target" to "monitoring"))!!
        assertFalse(message.allowed(profile(7, true, false)))
        assertTrue(message.allowed(profile(7, false, true)))
    }
    @Test fun malformedMessagesAreIgnored() {
        assertNull(PushPayload.parse(emptyMap()))
        assertNull(PushPayload.parse(data + ("delivery_id" to "invalid")))
        assertNull(PushPayload.parse(data + ("recipient_id" to "-1")))
        assertNull(PushPayload.parse(data + ("target" to "admin")))
    }
}
