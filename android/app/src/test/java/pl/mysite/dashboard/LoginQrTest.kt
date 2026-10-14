package pl.mysite.dashboard

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LoginQrTest {
    private val token = "a".repeat(40)
    private fun payload(url: String = "https://example.com/", key: String = token,
                        type: String = "mysite-dashboard", version: Int = 1) =
        JSONObject().put("type", type).put("version", version)
            .put("server_url", url).put("token", key).toString()

    @Test fun readsServerAndTokenTogether() {
        assertEquals(Connection("https://example.com", token), LoginQr.parse(payload()))
    }
    @Test fun rejectsInstallerLinksAndUnsupportedFormats() {
        for (value in listOf("https://example.com/backupapp/setup/$token/", "not json",
            payload(type = "other-app"), payload(version = 2), "x".repeat(4097))) {
            assertThrows(IllegalArgumentException::class.java) { LoginQr.parse(value) }
        }
    }
    @Test fun rejectsUnsafeAddressesAndInvalidCredentials() {
        for (url in listOf("http://example.com", "https://user:password@example.com",
            "https://example.com/?token=secret", "https://example.com/#token", "")) {
            assertThrows(IllegalArgumentException::class.java) { LoginQr.parse(payload(url = url)) }
        }
        for (key in listOf("", "a".repeat(39), "g".repeat(40), "$token\n")) {
            assertThrows(IllegalArgumentException::class.java) { LoginQr.parse(payload(key = key)) }
        }
    }
}
