package pl.mysite.dashboard

import org.json.JSONObject

enum class Dashboard { LIBRUS, MONITORING }

data class DashboardAccess(val librus: Boolean, val monitoring: Boolean) {
    fun choose(current: Dashboard): Dashboard? = when {
        current == Dashboard.LIBRUS && librus -> Dashboard.LIBRUS
        current == Dashboard.MONITORING && monitoring -> Dashboard.MONITORING
        librus -> Dashboard.LIBRUS
        monitoring -> Dashboard.MONITORING
        else -> null
    }
    companion object {
        fun parse(profile: JSONObject): DashboardAccess {
            val permissions = profile.getJSONObject("permissions")
            return DashboardAccess(permissions.getBoolean("librus"), permissions.getBoolean("monitoring"))
        }
    }
}
