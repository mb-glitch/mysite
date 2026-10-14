package pl.mysite.dashboard

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.OffsetDateTime

data class ChildActivity(val childName: String, val childId: Int, val entry: JSONObject)

object RecentActivity {
    fun merge(children: JSONArray): List<ChildActivity> {
        val entries = mutableListOf<ChildActivity>()
        for (i in 0 until children.length()) {
            val child = children.getJSONObject(i)
            val activity = child.getJSONArray("activity")
            for (j in 0 until activity.length()) {
                entries.add(ChildActivity(child.getString("name"), child.getInt("id"), activity.getJSONObject(j)))
            }
        }
        return entries.sortedWith(compareByDescending<ChildActivity> {
            try { OffsetDateTime.parse(it.entry.getString("date")).toInstant() } catch (_: Exception) { Instant.MIN }
        }.thenByDescending { it.entry.optInt("id") }
            .thenByDescending { it.childId }
            .thenByDescending { it.entry.optString("kind") }).take(10)
    }
}
