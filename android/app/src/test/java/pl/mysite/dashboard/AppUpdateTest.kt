package pl.mysite.dashboard

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.security.MessageDigest

class AppUpdateTest {
    private val packageName = "com.macbilicki.domowydashboard"
    private val bytes = "Fake APK for hashing tests".toByteArray()
    private val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
    private val sha = digest.joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun metadata() = JSONObject().put("available", true).put("version_code", 3)
        .put("version_name", "1.2").put("package_name", packageName)
        .put("sha256", sha).put("size", bytes.size)
        .put("apk", "dashboard-3-${sha.take(12)}.apk")
        .put("download_path", "/api/v1/app/apk/dashboard-3-${sha.take(12)}.apk/")

    @Test fun comparesVersionsAndKeepsDownloadOnSameServer() {
        val update = AppUpdate.parse(metadata(), packageName)!!
        assertTrue(update.isNewerThan(2))
        assertFalse(update.isNewerThan(3))
        assertFalse(update.isNewerThan(4))
        assertEquals("https://example.com/prefix${update.downloadPath}", update.downloadUrl("https://example.com/prefix/"))
        assertNull(AppUpdate.parse(JSONObject().put("available", false), packageName))
    }

    @Test fun rejectsOtherHostsTraversalAndMismatchedPackages() {
        for ((key, value) in listOf(
            "download_path" to "https://other.example/app.apk",
            "download_path" to "/api/v1/app/apk/../../secret/",
            "download_path" to "/api/v1/app/apk/file.apk/?token=secret",
            "apk" to "../secret.apk", "package_name" to "other.package"
        )) {
            assertThrows(IllegalArgumentException::class.java) { AppUpdate.parse(metadata().put(key, value), packageName) }
        }
    }

    @Test fun rejectsInvalidVersionSizeAndDigest() {
        for ((key, value) in listOf("version_code" to 0, "version_code" to 3.5,
            "size" to 0, "size" to AppUpdate.MAX_SIZE + 1, "sha256" to "invalid", "version_name" to "")) {
            assertThrows(IllegalArgumentException::class.java) { AppUpdate.parse(metadata().put(key, value), packageName) }
        }
    }

    @Test fun verifiesFullFileAndRejectsTruncationOrWrongChecksum() {
        val update = AppUpdate.parse(metadata(), packageName)!!
        update.verify(bytes.size.toLong(), digest)
        assertThrows(IllegalArgumentException::class.java) { update.verify(bytes.size - 1L, digest) }
        assertThrows(IllegalArgumentException::class.java) { update.verify(bytes.size.toLong(), ByteArray(32)) }
    }
}
