package pl.mysite.dashboard

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class RecentActivityTest {
    private fun entry(id: Int, date: String, kind: String = "Wiadomość") =
        JSONObject().put("id", id).put("date", date).put("kind", kind).put("title", "Entry $id")
    private fun child(id: Int, name: String, entries: List<JSONObject>) =
        JSONObject().put("id", id).put("name", name).put("activity", JSONArray(entries))

    @Test fun mergesChildrenAndSortsByActualTimeAcrossTimezones() {
        val children = JSONArray().put(child(1, "Ala", listOf(entry(1, "2026-10-14T12:00:00+02:00"))))
            .put(child(2, "Ola", listOf(entry(2, "2026-10-14T10:30:00Z", "Ogłoszenie"))))
        val result = RecentActivity.merge(children)
        assertEquals(listOf("Ola", "Ala"), result.map { it.childName })
        assertEquals("Ogłoszenie", result.first().entry.getString("kind"))
    }
    @Test fun limitsTheCombinedFeedToTenEntries() {
        val children = JSONArray().put(child(1, "Ala", (1..10).map { entry(it, "2026-10-14T09:00:00Z") }))
            .put(child(2, "Ola", (11..20).map { entry(it, "2026-10-14T10:00:00Z") }))
        val result = RecentActivity.merge(children)
        assertEquals(10, result.size)
        assertTrue(result.all { it.childName == "Ola" })
        assertEquals(20, result.first().entry.getInt("id"))
    }
    @Test fun emptyFeedIsAllowed() {
        assertTrue(RecentActivity.merge(JSONArray()).isEmpty())
        assertTrue(RecentActivity.merge(JSONArray().put(child(1, "Ala", emptyList()))).isEmpty())
    }
}
