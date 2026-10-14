package pl.mysite.dashboard

import org.json.JSONObject
import java.io.File
import java.net.URI
import java.security.MessageDigest
import javax.net.ssl.HttpsURLConnection

/** Immutable release metadata; APK requests can only target the authenticated server. */
data class AppUpdate(val versionCode: Long, val versionName: String, val packageName: String,
                     val apkName: String, val sha256: String, val size: Long, val downloadPath: String) {
    fun isNewerThan(installed: Long): Boolean = versionCode > installed
    fun downloadUrl(baseUrl: String): String = "${DashboardApi.normalizeUrl(baseUrl)}$downloadPath"

    fun download(config: Connection, target: File, progress: (Int) -> Unit = {}) {
        val connection = URI(downloadUrl(config.baseUrl)).toURL().openConnection() as HttpsURLConnection
        try {
            connection.connectTimeout = 10000
            connection.readTimeout = 15000
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.setRequestProperty("Authorization", "Token ${config.token}")
            connection.setRequestProperty("Accept", "application/vnd.android.package-archive")
            check(connection.responseCode == 200) { "Nie udało się pobrać aktualizacji. Spróbuj ponownie." }
            require(connection.contentLengthLong == -1L || connection.contentLengthLong == size) {
                "Plik aktualizacji ma nieprawidłowy rozmiar."
            }
            val digest = MessageDigest.getInstance("SHA-256")
            var total = 0L
            var lastPercent = -1
            connection.inputStream.use { input ->
                target.outputStream().use { output ->
                    val buffer = ByteArray(65536)
                    while (true) {
                        check(!Thread.currentThread().isInterrupted) { "Pobieranie anulowane." }
                        val count = input.read(buffer)
                        if (count < 0) break
                        total += count
                        require(total <= size) { "Plik aktualizacji jest większy niż oczekiwano." }
                        digest.update(buffer, 0, count)
                        output.write(buffer, 0, count)
                        val percent = (total * 100 / size).toInt()
                        if (percent != lastPercent) { progress(percent); lastPercent = percent }
                    }
                }
            }
            verify(total, digest.digest())
        } catch (error: Exception) {
            target.delete()
            throw error
        } finally { connection.disconnect() }
    }

    fun verify(bytes: Long, digest: ByteArray) {
        require(bytes == size && digest.joinToString("") { "%02x".format(it.toInt() and 255) } == sha256) {
            "Plik aktualizacji jest niekompletny lub uszkodzony. Pobierz go ponownie."
        }
    }

    companion object {
        const val MAX_SIZE = 100L * 1024 * 1024
        fun parse(data: JSONObject, expectedPackage: String): AppUpdate? {
            require(data.get("available") is Boolean)
            if (!data.getBoolean("available")) return null
            val codeValue = data.get("version_code")
            val sizeValue = data.get("size")
            require((codeValue is Int || codeValue is Long) && (sizeValue is Int || sizeValue is Long))
            val code = data.getLong("version_code")
            val name = data.getString("version_name")
            val packageName = data.getString("package_name")
            val sha = data.getString("sha256")
            val size = data.getLong("size")
            require(code in 1..2100000000L && name.isNotBlank() && name.length <= 100 &&
                packageName == expectedPackage && sha.matches(Regex("[a-f0-9]{64}")) && size in 1..MAX_SIZE)
            val apk = "dashboard-$code-${sha.take(12)}.apk"
            val path = "/api/v1/app/apk/$apk/"
            require(data.getString("apk") == apk && data.getString("download_path") == path)
            return AppUpdate(code, name, packageName, apk, sha, size, path)
        }
    }
}
