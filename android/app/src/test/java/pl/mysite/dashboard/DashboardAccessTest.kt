package pl.mysite.dashboard

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class DashboardAccessTest {
    @Test fun librusOnlyCannotSelectMonitoring() {
        val access = DashboardAccess(true, false)
        assertEquals(Dashboard.LIBRUS, access.choose(Dashboard.MONITORING))
        assertEquals(Dashboard.LIBRUS, access.choose(Dashboard.LIBRUS))
    }
    @Test fun monitoringOnlyOpensMonitoringAsTheFirstAvailableView() {
        assertEquals(Dashboard.MONITORING, DashboardAccess(false, true).choose(Dashboard.LIBRUS))
    }
    @Test fun bothGroupsPreserveTheSelectedViewAndNoGroupsHaveNoView() {
        val access = DashboardAccess(true, true)
        assertEquals(Dashboard.LIBRUS, access.choose(Dashboard.LIBRUS))
        assertEquals(Dashboard.MONITORING, access.choose(Dashboard.MONITORING))
        assertNull(DashboardAccess(false, false).choose(Dashboard.LIBRUS))
        assertNull(DashboardAccess(false, false).choose(Dashboard.MONITORING))
    }
    @Test fun permissionsComeFromTheServerRatherThanAccountName() {
        val profile = JSONObject().put("name", "admin").put("permissions",
            JSONObject().put("librus", false).put("monitoring", true))
        assertEquals(DashboardAccess(false, true), DashboardAccess.parse(profile))
    }
}
