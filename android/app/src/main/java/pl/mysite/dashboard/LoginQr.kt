package pl.mysite.dashboard

import org.json.JSONObject

/** Versioned local QR format. Credentials are never sent to a QR decoding service. */
object LoginQr {
    fun parse(payload: String): Connection {
        require(payload.length <= 4096) { "Kod QR jest zbyt długi." }
        val json = try { JSONObject(payload) } catch (_: Exception) {
            throw IllegalArgumentException("To nie jest kod logowania do Domowego dashboardu.")
        }
        require(json.optString("type") == "mysite-dashboard" && json.optInt("version") == 1) {
            "Nieobsługiwany kod QR. Zeskanuj kod logowania z panelu zaproszeń."
        }
        val token = json.optString("token")
        require(token.matches(Regex("[a-fA-F0-9]{40}"))) { "Kod QR zawiera nieprawidłowy token." }
        val url = try { DashboardApi.normalizeUrl(json.optString("server_url")) } catch (_: Exception) {
            throw IllegalArgumentException("Kod QR musi zawierać poprawny adres serwera HTTPS.")
        }
        return Connection(url, token, "")
    }
}
