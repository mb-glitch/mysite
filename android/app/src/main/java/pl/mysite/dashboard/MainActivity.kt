package pl.mysite.dashboard

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.app.AlertDialog
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.WindowManager
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.concurrent.Executors
import java.util.concurrent.Future
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import java.net.URI

class MainActivity : ComponentActivity() {
    private lateinit var store: TokenStore
    private var connection: Connection? = null
    private var selectedDashboard = Dashboard.LIBRUS
    private var access: DashboardAccess? = null
    private lateinit var appUpdater: AppUpdater
    private val installPermission = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        appUpdater.permissionResult()
    }
    private var pushStarted = false
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) configurePush()
    }
    private var generation = 0
    private val worker = Executors.newSingleThreadExecutor()
    private var pending: Future<*>? = null
    private lateinit var content: LinearLayout
    private lateinit var librusButton: Button
    private lateinit var monitoringButton: Button
    private lateinit var refreshButton: Button
    private lateinit var logoutButton: Button
    private val ink = Color.rgb(66, 54, 78)
    private val lavender = Color.rgb(229, 218, 250)
    private val mint = Color.rgb(213, 239, 225)
    private val peach = Color.rgb(255, 225, 211)
    private val scanner = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let { payload ->
            try {
                val scanned = LoginQr.parse(payload)
                AlertDialog.Builder(this).setTitle("Połącz z serwerem")
                    .setMessage("Zalogować aplikację na serwerze ${URI(scanned.baseUrl).host}?")
                    .setPositiveButton("Połącz") { _, _ ->
                        beginLogin { scanned }
                    }.setNegativeButton("Anuluj", null).show()
            } catch (_: Exception) {
                Toast.makeText(this, "Nieprawidłowy kod. Zeskanuj kod logowania do aplikacji z panelu zaproszeń.", Toast.LENGTH_LONG).show()
            }
        }
    }
    private val muted = Color.rgb(112, 93, 129)
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = TokenStore(this)
        connection = try { store.load() } catch (_: Exception) { store.clear(); null }
        selectedDashboard = if (savedInstanceState?.getBoolean("monitoring") == true) Dashboard.MONITORING else Dashboard.LIBRUS
        notificationTarget(intent)?.let { selectedDashboard = it }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(255, 248, 242))
            setPadding(dp(16), dp(12), dp(16), dp(12))
            setOnApplyWindowInsetsListener { view, insets ->
                view.setPadding(dp(16) + insets.systemWindowInsetLeft, dp(12) + insets.systemWindowInsetTop,
                    dp(16) + insets.systemWindowInsetRight, dp(12) + insets.systemWindowInsetBottom)
                insets
            }
        }
        text(root, "Domowy dashboard", 24, true)
        val tabs = LinearLayout(this)
        librusButton = button(tabs, "Librus") { selectedDashboard = Dashboard.LIBRUS; refresh() }
        monitoringButton = button(tabs, "Monitoring") { selectedDashboard = Dashboard.MONITORING; refresh() }
        root.addView(tabs)
        val actions = LinearLayout(this)
        refreshButton = button(actions, "Odśwież") { refresh() }
        logoutButton = button(actions, "Wyloguj") { confirmLogout() }
        root.addView(actions)
        val updateArea = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(updateArea)
        appUpdater = AppUpdater(this, updateArea, { connection }) {
            installPermission.launch(Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                android.net.Uri.parse("package:$packageName")))
        }
        content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        refresh()
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        notificationTarget(intent)?.let { selectedDashboard = it }
        refresh()
    }
    private fun notificationTarget(intent: Intent): Dashboard? = when (intent.getStringExtra("dashboard")) {
        "librus" -> Dashboard.LIBRUS
        "monitoring" -> Dashboard.MONITORING
        else -> null
    }
    private fun configurePush() {
        if (pushStarted || connection == null || !PushClient.configured(this) ||
            !(access?.librus == true || access?.monitoring == true)) return
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this,
                Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            val prefs = getSharedPreferences("push", Context.MODE_PRIVATE)
            if (!prefs.getBoolean("permission_requested", false)) {
                prefs.edit().putBoolean("permission_requested", true).apply()
                notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
            return
        }
        pushStarted = true
        val registeredConnection = connection
        PushClient.register(this) { success ->
            if (!isDestroyed && connection == registeredConnection) pushStarted = success
        }
    }
    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("monitoring", selectedDashboard == Dashboard.MONITORING)
        super.onSaveInstanceState(outState)
    }
    override fun onDestroy() {
        generation++
        worker.shutdownNow()
        appUpdater.close()
        super.onDestroy()
    }
    private fun text(parent: LinearLayout, value: String, size: Int = 16, bold: Boolean = false): TextView {
        return TextView(this).apply {
            text = value; textSize = size.toFloat(); setTextColor(ink)
            setPadding(0, dp(5), 0, dp(5))
            if (bold) setTypeface(null, Typeface.BOLD)
            parent.addView(this)
        }
    }
    private fun button(parent: LinearLayout, title: String, action: () -> Unit): Button {
        return Button(this).apply {
            text = title; isAllCaps = false
            setTextColor(ink)
            backgroundTintList = android.content.res.ColorStateList.valueOf(when (title) {
                "Monitoring", "Odśwież" -> mint
                "Kod QR", "Zeskanuj kod QR" -> peach
                else -> lavender
            })
            val params = if (parent.orientation == LinearLayout.HORIZONTAL)
                LinearLayout.LayoutParams(0, -2, 1f)
            else LinearLayout.LayoutParams(-1, -2)
            parent.addView(this, params)
            setOnClickListener { action() }
        }
    }
    private fun card(title: String, tint: Int = Color.WHITE): LinearLayout {
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(12), dp(16), dp(12))
            background = GradientDrawable().apply { setColor(tint); cornerRadius = dp(16).toFloat() }
            content.addView(this, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })
            text(this, title, 20, true)
        }
    }
    private fun updateControls() {
        librusButton.visibility = if (connection != null && access?.librus == true) View.VISIBLE else View.GONE
        monitoringButton.visibility = if (connection != null && access?.monitoring == true) View.VISIBLE else View.GONE
        librusButton.isEnabled = selectedDashboard != Dashboard.LIBRUS
        monitoringButton.isEnabled = selectedDashboard != Dashboard.MONITORING
        refreshButton.visibility = if (connection != null) View.VISIBLE else View.GONE
        logoutButton.visibility = if (connection != null) View.VISIBLE else View.GONE
    }
    private fun refresh() {
        val request = ++generation
        pending?.cancel(true)
        access = null
        updateControls()
        content.removeAllViews()
        val config = connection ?: run {
            val welcome = card("Witaj w domu", peach)
            text(welcome, "Zaloguj się kontem Django lub zeskanuj kod logowania z panelu zaproszeń.")
            button(welcome, "Zeskanuj kod QR") { scanQr() }
            button(welcome, "Zaloguj się hasłem") { loginDialog() }
            return
        }
        val current = selectedDashboard
        content.addView(ProgressBar(this))
        text(content, "Pobieranie podsumowania…")
        pending = worker.submit {
            val result = runCatching {
                val permissions = DashboardAccess.parse(DashboardApi.profile(config))
                val selected = permissions.choose(current)
                Triple(permissions, selected, selected?.let { view -> runCatching { DashboardApi.load(config, view) } })
            }
            runOnUiThread {
                if (isDestroyed || generation != request) return@runOnUiThread
                content.removeAllViews()
                result.fold(onSuccess = { (permissions, selected, dashboardResult) ->
                    access = permissions
                    if (selected != null) selectedDashboard = selected
                    updateControls()
                    configurePush()
                    if (permissions.librus || permissions.monitoring) appUpdater.check(config)
                    if (selected == null || dashboardResult == null) {
                        text(card("Brak dostępnych widoków"), "Poproś administratora o dodanie konta do grupy Librus lub Monitoring.")
                    } else dashboardResult.fold(onSuccess = { data ->
                        try {
                            if (selected == Dashboard.MONITORING) renderMonitoring(data) else renderLibrus(data)
                            text(content, "Odczyt: ${date(data.value("generated_at"))}", 12).setTextColor(muted)
                        } catch (_: Exception) {
                            content.removeAllViews()
                            text(card("Nie udało się wyświetlić danych"), "Serwer zwrócił niezgodną odpowiedź API.")
                        }
                    }, onFailure = { handleFailure(it) })
                }, onFailure = { handleFailure(it) })
            }
        }
    }
    private fun handleFailure(error: Throwable) {
        if (error is ApiException && error.statusCode == 401) {
            try {
                clearSession()
                Toast.makeText(this, error.message, Toast.LENGTH_LONG).show()
            } catch (_: Exception) { showError("Nie udało się usunąć zapisanej sesji. Spróbuj wylogować się ponownie.") }
        } else showError(errorMessage(error))
    }
    private fun errorMessage(error: Throwable): String = when (error) {
        is ApiException -> error.message ?: "Nie udało się wykonać żądania."
        is IllegalArgumentException -> "Sprawdź dane i adres serwera HTTPS."
        else -> "Nie udało się połączyć. Sprawdź internet, adres serwera i certyfikat HTTPS."
    }
    private fun showError(message: String) {
        text(card("Nie udało się pobrać danych"), message)
        button(content, "Spróbuj ponownie") { refresh() }
    }
    private fun beginLogin(fetchConnection: () -> Connection) {
        val request = ++generation
        pending?.cancel(true)
        content.removeAllViews()
        content.addView(ProgressBar(this))
        text(content, "Logowanie…")
        pending = worker.submit {
            val result = runCatching {
                val config = fetchConnection()
                DashboardAccess.parse(DashboardApi.profile(config))
                config
            }
            runOnUiThread {
                if (isDestroyed || generation != request) return@runOnUiThread
                result.fold(onSuccess = { config ->
                    try {
                        synchronized(PushClient.sessionLock) { store.save(config) }
                        pushStarted = false
                        connection = config
                        selectedDashboard = Dashboard.LIBRUS
                        refresh()
                    } catch (_: Exception) {
                        refresh()
                        Toast.makeText(this, "Nie udało się bezpiecznie zapisać połączenia.", Toast.LENGTH_LONG).show()
                    }
                }, onFailure = { error ->
                    refresh()
                    text(content, errorMessage(error))
                })
            }
        }
    }
    private fun clearSession() {
        appUpdater.reset()
        PushClient.logout(this, connection) { store.clear() }
        pushStarted = false
        generation++
        pending?.cancel(true)
        connection = null
        access = null
        selectedDashboard = Dashboard.LIBRUS
        refresh()
    }
    private fun confirmLogout() {
        AlertDialog.Builder(this).setTitle("Wylogować się?")
            .setMessage("Dane logowania zostaną usunięte z tego telefonu. Aby wrócić, zaloguj się ponownie lub zeskanuj kod QR.")
            .setPositiveButton("Wyloguj") { _, _ ->
                try { clearSession() } catch (_: Exception) {
                    Toast.makeText(this, "Nie udało się usunąć danych logowania. Spróbuj ponownie.", Toast.LENGTH_LONG).show()
                }
            }.setNegativeButton("Anuluj", null).show()
    }
    private fun renderMonitoring(data: JSONObject) {
        val summary = data.getJSONObject("summary")
        text(card("Monitoring", mint), "Sprawne: ${summary.getInt("healthy")} / ${summary.getInt("total")}")
        val services = data.getJSONArray("services")
        if (services.length() == 0) text(card("Brak usług"), "Nie ma usług dostępnych dla tego tokenu.")
        services.objects { item ->
            val box = card(item.value("description").ifBlank { item.getString("name") }, lavender)
            val status = item.getString("status")
            text(box, status, 18, true).setTextColor(when (item.getInt("status_code")) {
                0 -> Color.rgb(21, 128, 61); 3 -> muted; else -> Color.rgb(185, 28, 28)
            })
            text(box, "${item.getString("owner")} · ${item.getString("name")}")
            val report = item.optJSONObject("latest_report")
            if (report == null) text(box, "Brak raportu.", 14)
            else {
                text(box, "Ostatni raport: ${date(report.value("checked_at"))}", 14, true)
                val code = report.optInt("status_code", -1)
                text(box, "Wynik raportu: ${when (code) { 0 -> "OK"; 1 -> "Ostrzeżenie"; 2 -> "Błąd"; else -> code.toString() }}", 14)
                text(box, report.value("summary").ifBlank { "Raport nie zawiera podsumowania." }, 14)
            }
        }
    }
    private fun renderLibrus(data: JSONObject) {
        val report = data.optJSONObject("latest_report")
        val reportBox = card("Ostatnie pobranie Librusa", mint)
        text(reportBox, if (report == null) "Brak raportu synchronizacji." else
            "${date(report.value("checked_at"))}\n${if (report.optInt("status_code", -1) == 0) "Bez zgłoszonych błędów" else "Zgłoszono błąd synchronizacji"}")
        val children = data.getJSONArray("children")
        val entries = RecentActivity.merge(children)
        val activityBox = card("Wiadomości i ogłoszenia · ostatnie 10", peach)
        if (entries.isEmpty()) text(activityBox, "Brak wiadomości i ogłoszeń.")
        RecentActivity.groupAdjacent(entries).forEachIndexed { index, items ->
            if (index > 0) {
                activityBox.addView(View(this).apply { setBackgroundColor(Color.rgb(139, 103, 165)) },
                    LinearLayout.LayoutParams(-1, 1))
            }
            val tile = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(0, dp(2), 0, dp(2))
                isFocusable = true
                activityBox.addView(this, LinearLayout.LayoutParams(-1, -2))
            }
            val names = items.map { it.childName }.distinct().joinToString(", ")
            val kinds = items.map { it.entry.getString("kind") }.distinct().joinToString(", ")
            val subjects = items.map { it.entry.value("title").ifBlank { "(bez tytułu)" } }
                .distinct().joinToString("\n")
            text(tile, "$names · $kinds", 14, true)
            val metadata = items.map {
                "${it.childName} · ${date(it.entry.value("date"))} · ${it.entry.value("sender")}".trimEnd(' ', '·')
            }.distinct().joinToString("\n")
            text(tile, metadata, 12).setTextColor(muted)
            val title = text(tile, "▸ $subjects", 16, true)
            val body = text(tile, items.first().entry.value("content").ifBlank { "Brak treści" }, 14)
            body.visibility = View.GONE
            for (childIndex in 0 until tile.childCount) {
                tile.getChildAt(childIndex).setPadding(0, dp(2), 0, dp(2))
            }
            tile.contentDescription = "$names. $kinds. $metadata. $subjects. Dotknij, aby rozwinąć treść"
            tile.setOnClickListener {
                val expanded = body.visibility == View.GONE
                body.visibility = if (expanded) View.VISIBLE else View.GONE
                title.text = "${if (expanded) "▾" else "▸"} $subjects"
                tile.setBackgroundColor(if (expanded) Color.rgb(236, 212, 199) else Color.TRANSPARENT)
                tile.contentDescription = "$names. $kinds. $metadata. $subjects. " +
                    if (expanded) "${body.text}. Dotknij, aby zwinąć treść" else "Dotknij, aby rozwinąć treść"
            }
        }
        if (children.length() == 0) text(card("Dzieci"), "Brak danych Librusa.")
        children.objects { child ->
            val box = card("${child.getString("name")} · bieżące oceny", lavender)
            text(box, "Pobranie ocen: ${date(child.value("grades_synced_at"))}", 12)
            val grades = child.getJSONArray("recent_grades")
            if (grades.length() == 0) text(box, "Brak aktualnych ocen.")
            grades.objects {
                text(box, "${it.getString("value")} · ${it.getString("subject")}", 16, true)
                text(box, "${it.getString("date")}  ${it.value("description")}", 14)
            }
        }
        children.objects { child ->
            val box = card("${child.getString("name")} · średnie", mint)
            val summary = child.getJSONObject("grade_summary")
            val subjects = summary.getJSONArray("subjects")
            if (subjects.length() == 0) text(box, "Brak ocen liczonych do średniej.")
            subjects.objects {
                text(box, "${it.getString("name")}: ${it.getString("mean")} (${it.getInt("count")} ocen)")
            }
            text(box, "Szacowana średnia: ${summary.value("projected").ifBlank { "—" }}", 18, true)
        }
    }
    private fun scanQr() {
        scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            .setCaptureActivity(SecureCaptureActivity::class.java)
            .setPrompt("Zeskanuj kod logowania do aplikacji z panelu zaproszeń")
            .setBeepEnabled(false).setBarcodeImageEnabled(false).setOrientationLocked(false))
    }
    private fun loginDialog() {
        val fields = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(8), dp(20), dp(8)) }
        fun field(label: String, type: Int): EditText {
            text(fields, label, 14)
            return EditText(this).apply {
                inputType = type
                isSingleLine = true
                isSaveEnabled = false
                importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO
                fields.addView(this)
            }
        }
        val url = field("Adres serwera HTTPS", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        val username = field("Nazwa użytkownika", InputType.TYPE_CLASS_TEXT)
        val password = field("Hasło", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        val dialog = AlertDialog.Builder(this).setTitle("Logowanie")
            .setView(ScrollView(this).apply { addView(fields) })
            .setPositiveButton("Zaloguj", null).setNegativeButton("Anuluj", null).create()
        dialog.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                try {
                    val baseUrl = DashboardApi.normalizeUrl(url.text.toString())
                    val login = username.text.toString().trim()
                    val secret = password.text.toString()
                    require(login.isNotBlank() && secret.isNotEmpty())
                    password.text.clear()
                    dialog.dismiss()
                    beginLogin { DashboardApi.login(baseUrl, login, secret) }
                } catch (_: Exception) {
                    Toast.makeText(this, "Wpisz adres HTTPS, nazwę użytkownika i hasło.", Toast.LENGTH_LONG).show()
                }
            }
        }
        dialog.show()
    }
    private fun JSONObject.value(key: String) = if (isNull(key)) "" else optString(key, "")
    private fun JSONArray.objects(action: (JSONObject) -> Unit) { for (i in 0 until length()) action(getJSONObject(i)) }
    private fun date(value: String): String = if (value.isBlank()) "brak" else try {
        OffsetDateTime.parse(value).atZoneSameInstant(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm"))
    } catch (_: Exception) { value }
}
