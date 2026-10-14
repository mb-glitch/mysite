package pl.mysite.dashboard

import org.json.JSONObject
import java.net.URI
import javax.net.ssl.HttpsURLConnection

object DashboardApi {
    fun normalizeUrl(value: String): String {
        val uri = URI(value.trim())
        require(uri.scheme == "https" && !uri.host.isNullOrBlank() && uri.rawUserInfo == null &&
            uri.rawQuery == null && uri.rawFragment == null) { "Podaj adres HTTPS serwera bez loginu, parametrów i fragmentu." }
        return uri.toASCIIString().trimEnd('/')
    }
    fun load(config: Connection, backup: Boolean): JSONObject {
        val path = if (backup) "backup" else "librus"
        val token = if (backup) config.backupToken.ifBlank { config.token } else config.token
        require(token.matches(Regex("[a-fA-F0-9]{40}"))) { "Uzupełnij token dla tego widoku w ustawieniach." }
        val connection = URI("${normalizeUrl(config.baseUrl)}/api/v1/$path/dashboard/").toURL()
            .openConnection() as HttpsURLConnection
        try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 10000
            connection.readTimeout = 15000
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.setRequestProperty("Authorization", "Token $token")
            connection.setRequestProperty("Accept", "application/json")
            when (connection.responseCode) {
                200 -> return connection.inputStream.bufferedReader(Charsets.UTF_8).use {
                    JSONObject(it.readText())
                }
                401 -> error("Token jest nieprawidłowy lub konto jest nieaktywne.")
                403 -> error("Brak uprawnień do tego widoku. Librus wymaga tokenu użytkownika z grupy Librus.")
                404 -> error("Nie znaleziono API. Sprawdź adres serwera i wdrożenie API v1.")
                in 300..399 -> error("Serwer przekierowuje żądanie. Wpisz docelowy adres HTTPS w ustawieniach.")
                else -> error("Serwer jest niedostępny (HTTP ${connection.responseCode}). Spróbuj ponownie.")
            }
        } finally { connection.disconnect() }
    }
}
