package pl.mysite.dashboard

import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.core.content.FileProvider
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.Future

class AppUpdater(private val activity: ComponentActivity, private val area: LinearLayout,
                 private val connection: () -> Connection?, private val requestInstallPermission: () -> Unit) {
    private val worker = Executors.newSingleThreadExecutor()
    private var task: Future<*>? = null
    private var checked: Connection? = null
    private var release: AppUpdate? = null
    private var releaseConnection: Connection? = null
    private var updateButton: Button? = null
    private var waitingForPermission = false
    private var downloading = false
    private var generation = 0

    fun check(config: Connection) {
        if (checked == config || downloading) return
        checked = config
        val request = generation
        task = worker.submit {
            val result = runCatching { AppUpdate.parse(DashboardApi.update(config), activity.packageName) }
            activity.runOnUiThread {
                if (activity.isDestroyed || generation != request || connection() != config) return@runOnUiThread
                result.fold(onSuccess = { update ->
                    release = update?.takeIf { it.isNewerThan(BuildConfig.VERSION_CODE.toLong()) }
                    releaseConnection = config
                    render()
                }, onFailure = { checked = null }) // Older backends and offline checks do not block the dashboard.
            }
        }
    }

    private fun render() {
        area.removeAllViews()
        val update = release ?: return
        val density = activity.resources.displayMetrics.density
        area.setPadding((12 * density).toInt(), (8 * density).toInt(), (12 * density).toInt(), (8 * density).toInt())
        area.background = GradientDrawable().apply {
            setColor(Color.rgb(213, 239, 225)); cornerRadius = 16 * density
        }
        area.addView(TextView(activity).apply {
            text = "Dostępna aktualizacja: ${update.versionName}"
            setTextColor(Color.rgb(66, 54, 78)); textSize = 16f
        })
        updateButton = Button(activity).apply {
            text = "Pobierz i zainstaluj"; isAllCaps = false
            setTextColor(Color.rgb(66, 54, 78))
            backgroundTintList = android.content.res.ColorStateList.valueOf(Color.rgb(229, 218, 250))
            setOnClickListener { beginUpdate() }
            area.addView(this)
        }
    }

    private fun beginUpdate() {
        if (downloading || release == null || connection() != releaseConnection) return
        if (!activity.packageManager.canRequestPackageInstalls()) {
            AlertDialog.Builder(activity).setTitle("Zezwól na aktualizacje")
                .setMessage("W ustawieniach Androida zezwól Domowemu dashboardowi na instalowanie aplikacji. Potem pobierzemy aktualizację.")
                .setPositiveButton("Otwórz ustawienia") { _, _ ->
                    waitingForPermission = true
                    requestInstallPermission()
                }.setNegativeButton("Anuluj", null).show()
            return
        }
        download()
    }

    fun permissionResult() {
        if (!waitingForPermission) return
        waitingForPermission = false
        if (activity.packageManager.canRequestPackageInstalls()) beginUpdate()
        else message("Bez zgody Androida aktualizacja nie zostanie zainstalowana.")
    }

    private fun download() {
        val update = release ?: return
        val config = releaseConnection ?: return
        val request = generation
        downloading = true
        updateButton?.isEnabled = false
        updateButton?.text = "Pobieranie…"
        task = worker.submit {
            val result = runCatching {
                val directory = File(activity.cacheDir, "updates").apply { mkdirs() }
                val temporary = File(directory, "${UUID.randomUUID()}.part")
                try {
                    update.download(config, temporary) { percent ->
                        activity.runOnUiThread {
                            if (!activity.isDestroyed && generation == request)
                                updateButton?.text = "Pobieranie… $percent%"
                        }
                    }
                    validateApk(temporary, update)
                    check(connection() == config && generation == request) { "Pobieranie anulowane." }
                    val apk = File(directory, update.apkName)
                    if (apk.exists()) check(apk.delete())
                    check(temporary.renameTo(apk)) { "Nie udało się zapisać aktualizacji." }
                    apk
                } finally { temporary.delete() }
            }
            activity.runOnUiThread {
                if (activity.isDestroyed || generation != request || connection() != config) return@runOnUiThread
                downloading = false
                updateButton?.isEnabled = true
                updateButton?.text = "Pobierz i zainstaluj"
                result.fold(onSuccess = { apk ->
                    try {
                        val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.updates", apk)
                        activity.startActivity(Intent(Intent.ACTION_VIEW).apply {
                            setDataAndType(uri, "application/vnd.android.package-archive")
                            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        })
                    } catch (_: Exception) { message("Nie udało się otworzyć instalatora Androida.") }
                }, onFailure = { error -> message(when (error) {
                    is IllegalArgumentException, is IllegalStateException -> error.message ?: "Nieprawidłowa aktualizacja."
                    else -> "Nie udało się pobrać aktualizacji. Sprawdź internet i spróbuj ponownie."
                }) })
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun validateApk(file: File, update: AppUpdate) {
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val installed = activity.packageManager.getPackageInfo(activity.packageName, flags)
        val downloaded = activity.packageManager.getPackageArchiveInfo(file.absolutePath, flags)
            ?: throw IllegalArgumentException("Pobrany plik nie jest poprawnym APK.")
        val version = if (Build.VERSION.SDK_INT >= 28) downloaded.longVersionCode else downloaded.versionCode.toLong()
        require(downloaded.packageName == activity.packageName && version == update.versionCode &&
            update.isNewerThan(BuildConfig.VERSION_CODE.toLong())) { "APK nie pasuje do tej aplikacji lub wersji." }
        fun signatures(info: PackageInfo): Set<String> {
            val certificates = if (Build.VERSION.SDK_INT >= 28) info.signingInfo?.apkContentsSigners else info.signatures
            return certificates?.map { it.toCharsString() }?.toSet() ?: emptySet()
        }
        val expected = signatures(installed)
        require(expected.isNotEmpty() && expected == signatures(downloaded)) {
            "Aktualizacja jest podpisana innym kluczem niż zainstalowana aplikacja."
        }
    }

    fun reset() {
        generation++
        task?.cancel(true)
        checked = null; release = null; releaseConnection = null
        downloading = false; waitingForPermission = false; updateButton = null
        area.removeAllViews()
        area.background = null
        area.setPadding(0, 0, 0, 0)
    }

    fun close() { reset(); worker.shutdownNow() }
    private fun message(text: String) { Toast.makeText(activity, text, Toast.LENGTH_LONG).show() }
}
