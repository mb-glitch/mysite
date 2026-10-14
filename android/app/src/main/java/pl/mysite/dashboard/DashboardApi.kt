package pl.mysite.dashboard

import org.json.JSONObject
import java.net.URI
import javax.net.ssl.HttpsURLConnection

class ApiException(val statusCode: Int, message: String) : IllegalStateException(message)

object DashboardApi {
    fun normalizeUrl(value: String): String {
        val uri = URI(value.trim())
        require(uri.scheme == "https" && !uri.host.isNullOrBlank() && uri.rawUserInfo == null &&
            uri.rawQuery == null && uri.rawFragment == null) { "Podaj adres HTTPS serwera bez loginu, parametrów i fragmentu." }
        return uri.toASCIIString().trimEnd('/')
    }
    fun profile(config: Connection): JSONObject = request(config.baseUrl, "auth/me", config.token)

    fun login(baseUrl: String, username: String, password: String): Connection {
        require(username.isNotBlank() && password.isNotEmpty()) { "Wpisz nazwę użytkownika i hasło." }
        val normalized = normalizeUrl(baseUrl)
        val data = request(normalized, "auth/login", body = JSONObject().put("username", username.trim()).put("password", password))
        val token = data.getString("token")
        require(token.matches(Regex("[a-fA-F0-9]{40}"))) { "Serwer zwrócił nieprawidłową odpowiedź logowania." }
        return Connection(normalized, token)
    }
    fun load(config: Connection, dashboard: Dashboard): JSONObject =
        request(config.baseUrl, "${if (dashboard == Dashboard.MONITORING) "monitoring" else "librus"}/dashboard", config.token)

    private fun request(baseUrl: String, path: String, token: String? = null, body: JSONObject? = null): JSONObject {
        if (token != null) require(token.matches(Regex("[a-fA-F0-9]{40}"))) { "Nieprawidłowe dane logowania. Zaloguj się ponownie." }
        val connection = URI("${normalizeUrl(baseUrl)}/api/v1/$path/").toURL().openConnection() as HttpsURLConnection
        try {
            connection.requestMethod = if (body == null) "GET" else "POST"
            connection.connectTimeout = 10000
            connection.readTimeout = 15000
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            if (token != null) connection.setRequestProperty("Authorization", "Token $token")
            connection.setRequestProperty("Accept", "application/json")
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            when (status) {
                200 -> return connection.inputStream.bufferedReader(Charsets.UTF_8).use { JSONObject(it.readText()) }
                400 -> throw ApiException(status, "Sprawdź wpisane dane logowania.")
                401 -> throw ApiException(status, if (body != null) "Nieprawidłowa nazwa użytkownika lub hasło."
                    else "Sesja wygasła lub konto jest nieaktywne. Zaloguj się ponownie.")
                403 -> throw ApiException(status, "Brak dostępu. Konto wymaga grupy Librus lub Monitoring.")
                404 -> throw ApiException(status, "Nie znaleziono API. Sprawdź adres serwera i wdrożenie API v1.")
                429 -> throw ApiException(status, "Zbyt wiele prób logowania. Odczekaj minutę i spróbuj ponownie.")
                in 300..399 -> throw ApiException(status, "Serwer przekierowuje żądanie. Zaloguj się, podając docelowy adres HTTPS.")
                else -> throw ApiException(status, "Serwer jest niedostępny (HTTP $status). Spróbuj ponownie.")
            }
        } finally { connection.disconnect() }
    }
}
