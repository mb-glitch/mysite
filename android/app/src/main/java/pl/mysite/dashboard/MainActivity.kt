package pl.mysite.dashboard

import android.app.Activity
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

class MainActivity : Activity() {
    private lateinit var store: TokenStore
    private var connection: Connection? = null
    private var backup = false
    private var generation = 0
    private val worker = Executors.newSingleThreadExecutor()
    private var pending: Future<*>? = null
    private lateinit var content: LinearLayout
    private lateinit var librusButton: Button
    private lateinit var backupButton: Button
    private val ink = Color.rgb(30, 41, 59)
    private val muted = Color.rgb(100, 116, 139)
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        store = TokenStore(this)
        connection = try { store.load() } catch (_: Exception) { store.clear(); null }
        backup = savedInstanceState?.getBoolean("backup") ?: false
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(244, 247, 251))
            setPadding(dp(16), dp(12), dp(16), dp(12))
            setOnApplyWindowInsetsListener { view, insets ->
                view.setPadding(dp(16) + insets.systemWindowInsetLeft, dp(12) + insets.systemWindowInsetTop,
                    dp(16) + insets.systemWindowInsetRight, dp(12) + insets.systemWindowInsetBottom)
                insets
            }
        }
        text(root, "Domowy dashboard", 24, true)
        val tabs = LinearLayout(this)
        librusButton = button(tabs, "Librus") { backup = false; refresh() }
        backupButton = button(tabs, "Backup") { backup = true; refresh() }
        root.addView(tabs)
        val actions = LinearLayout(this)
        button(actions, "Odśwież") { refresh() }
        button(actions, "Ustawienia") { settings() }
        root.addView(actions)
        content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        refresh()
        if (connection == null) settings()
    }
    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("backup", backup)
        super.onSaveInstanceState(outState)
    }
    override fun onDestroy() {
        generation++
        worker.shutdownNow()
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
            parent.addView(this, LinearLayout.LayoutParams(0, -2, 1f))
            setOnClickListener { action() }
        }
    }
    private fun card(title: String): LinearLayout {
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(12), dp(16), dp(12))
            background = GradientDrawable().apply { setColor(Color.WHITE); cornerRadius = dp(16).toFloat() }
            content.addView(this, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })
            text(this, title, 20, true)
        }
    }
    private fun refresh() {
        val request = ++generation
        pending?.cancel(true)
        librusButton.isEnabled = backup
        backupButton.isEnabled = !backup
        content.removeAllViews()
        val config = connection ?: run { text(card("Witaj"), "Wpisz adres serwera i token w ustawieniach."); return }
        val requestedBackup = backup
        content.addView(ProgressBar(this))
        text(content, "Pobieranie podsumowania…")
        pending = worker.submit {
            val result = runCatching { DashboardApi.load(config, requestedBackup) }
            runOnUiThread {
                if (isDestroyed || generation != request) return@runOnUiThread
                content.removeAllViews()
                result.fold(onSuccess = { data ->
                    try {
                        if (requestedBackup) renderBackup(data) else renderLibrus(data)
                        text(content, "Odczyt: ${date(data.value("generated_at"))}", 12).setTextColor(muted)
                    } catch (_: Exception) {
                        content.removeAllViews()
                        text(card("Nie udało się wyświetlić danych"), "Serwer zwrócił niezgodną odpowiedź API.")
                    }
                }, onFailure = { error ->
                    val message = if (error is IllegalStateException || error is IllegalArgumentException)
                        error.message ?: "Nieprawidłowa konfiguracja."
                    else "Nie udało się połączyć. Sprawdź internet, adres serwera i certyfikat HTTPS."
                    text(card("Nie udało się pobrać danych"), message)
                    button(content, "Spróbuj ponownie") { refresh() }
                })
            }
        }
    }
    private fun renderBackup(data: JSONObject) {
        val summary = data.getJSONObject("summary")
        text(card("Podsumowanie backupu"), "Sprawne: ${summary.getInt("healthy")} / ${summary.getInt("total")}")
        val services = data.getJSONArray("services")
        if (services.length() == 0) text(card("Brak usług"), "Nie ma usług dostępnych dla tego tokenu.")
        services.objects { item ->
            val box = card(item.value("description").ifBlank { item.getString("name") })
            val status = item.getString("status")
            text(box, status, 18, true).setTextColor(when (item.getInt("status_code")) {
                0 -> Color.rgb(21, 128, 61); 3 -> muted; else -> Color.rgb(185, 28, 28)
            })
            text(box, "${item.getString("owner")} · ${item.getString("name")}")
            text(box, "Ostatni raport: ${date(item.value("last_check_at"))}", 14)
        }
    }
    private fun renderLibrus(data: JSONObject) {
        val report = data.optJSONObject("latest_report")
        val reportBox = card("Ostatnie pobranie Librusa")
        text(reportBox, if (report == null) "Brak raportu synchronizacji." else
            "${date(report.value("checked_at"))}\n${if (report.optInt("status_code", -1) == 0) "Bez zgłoszonych błędów" else "Zgłoszono błąd synchronizacji"}")
        val children = data.getJSONArray("children")
        if (children.length() == 0) text(card("Dzieci"), "Brak danych Librusa.")
        children.objects { child ->
            val box = card(child.getString("name"))
            val summary = child.getJSONObject("grade_summary")
            text(box, "Szacowana średnia: ${summary.value("projected").ifBlank { "—" }}", 18, true)
            text(box, "Pobranie ocen: ${date(child.value("grades_synced_at"))}", 12)
            text(box, "Średnie ważone", 17, true)
            val subjects = summary.getJSONArray("subjects")
            if (subjects.length() == 0) text(box, "Brak ocen liczonych do średniej.")
            subjects.objects { text(box, "${it.getString("name")}: ${it.getString("mean")} (${it.getInt("count")} ocen)") }
            text(box, "Ostatnie 10 ocen", 17, true)
            val grades = child.getJSONArray("recent_grades")
            if (grades.length() == 0) text(box, "Brak aktualnych ocen.")
            grades.objects {
                text(box, "${it.getString("value")} · ${it.getString("subject")}", 16, true)
                text(box, "${it.getString("date")}  ${it.value("description")}", 14)
            }
            text(box, "Wiadomości i ogłoszenia · ostatnie 10", 17, true)
            val activity = child.getJSONArray("activity")
            if (activity.length() == 0) text(box, "Brak wiadomości i ogłoszeń.")
            activity.objects {
                text(box, "${it.getString("kind")} · ${date(it.value("date"))}\n${it.value("sender")}", 12).setTextColor(muted)
                val title = text(box, "▸ ${it.value("title").ifBlank { "(bez tytułu)" }}", 16, true)
                val body = text(box, it.value("content").ifBlank { "Brak treści" }, 14)
                body.visibility = View.GONE
                title.isFocusable = true
                title.contentDescription = "${title.text}. Dotknij, aby rozwinąć lub zwinąć treść"
                title.setOnClickListener { body.visibility = if (body.visibility == View.GONE) View.VISIBLE else View.GONE }
            }
        }
    }
    private fun settings() {
        val fields = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(8), dp(20), dp(8)) }
        fun field(label: String, value: String, secret: Boolean = false): EditText {
            text(fields, label, 14)
            return EditText(this).apply {
                inputType = if (secret) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
                    else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
                isSingleLine = true; setText(value); isSaveEnabled = false
                importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO
                fields.addView(this)
            }
        }
        val url = field("Adres serwera HTTPS", connection?.baseUrl ?: "")
        val token = field("Token użytkownika (DRF)", connection?.token ?: "", true)
        val backupToken = field("Osobny token backupu (opcjonalnie)", connection?.backupToken ?: "", true)
        text(fields, "Librus: konto z grupy Librus. Token telefonu pokazuje tylko jego usługę. Puste pole backupu używa tokenu użytkownika.", 12)
        val dialog = AlertDialog.Builder(this).setTitle("Połączenie")
            .setView(ScrollView(this).apply { addView(fields) })
            .setPositiveButton("Zapisz", null).setNegativeButton("Anuluj", null)
            .setNeutralButton("Wyloguj") { _, _ -> store.clear(); connection = null; refresh() }.create()
        dialog.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                try {
                    val newConnection = Connection(DashboardApi.normalizeUrl(url.text.toString()),
                        token.text.toString().trim(), backupToken.text.toString().trim())
                    require(newConnection.token.isNotBlank() || newConnection.backupToken.isNotBlank()) { "Wpisz przynajmniej jeden token." }
                    require(listOf(newConnection.token, newConnection.backupToken).all {
                        it.isBlank() || it.matches(Regex("[a-fA-F0-9]{40}"))
                    }) { "Token DRF powinien mieć 40 znaków szesnastkowych." }
                    store.save(newConnection); connection = newConnection
                    dialog.dismiss(); refresh()
                } catch (error: IllegalArgumentException) {
                    Toast.makeText(this, error.message ?: "Sprawdź adres i token.", Toast.LENGTH_LONG).show()
                } catch (_: Exception) {
                    Toast.makeText(this, "Nie udało się bezpiecznie zapisać ustawień.", Toast.LENGTH_LONG).show()
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
