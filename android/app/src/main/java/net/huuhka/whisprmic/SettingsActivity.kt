package net.huuhka.whisprmic

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.text.SpannableString
import android.text.style.ForegroundColorSpan
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import java.util.concurrent.Executors
import org.json.JSONObject

/**
 * Whispr Mic's settings, in three tabs: the dictionary and the apps the mic
 * shows in, which you come back to, and the setup, which you mostly don't.
 * Opens on the dictionary, or on the setup until that's complete.
 */
class SettingsActivity : Activity() {
    enum class Tab(val label: String) { DICTIONARY("Dictionary"), APPS("Apps"), SETUP("Setup") }

    private lateinit var prefs: Prefs
    private lateinit var serverUrl: EditText
    private lateinit var token: EditText
    private lateinit var status: TextView
    private lateinit var connection: TextView
    private lateinit var microphoneButton: Button
    private lateinit var accessibilityButton: Button
    private lateinit var dictionaryPage: DictionaryPage
    private lateinit var appsPage: AppsPage
    private lateinit var setupPage: View
    private val tabLabels = mutableMapOf<Tab, TextView>()
    private val tabMarks = mutableMapOf<Tab, View>()
    private var tab = Tab.DICTIONARY
    private val worker = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestWindowFeature(Window.FEATURE_NO_TITLE)
        prefs = Prefs(this)

        dictionaryPage = DictionaryPage(this, prefs, worker)
        appsPage = AppsPage(this, prefs, worker) { refreshStatus() }
        setupPage = setupPage()
        val pages = FrameLayout(this).apply {
            addView(dictionaryPage)
            addView(appsPage)
            addView(setupPage)
        }
        setContentView(
            LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                // Takes the initial focus, so no tab opens with the keyboard up.
                isFocusableInTouchMode = true
                descendantFocusability = ViewGroup.FOCUS_BEFORE_DESCENDANTS
                padForSystemBars()
                addView(
                    TextView(context).apply {
                        text = getString(R.string.app_name)
                        textSize = 24f
                        setPadding(dp(16), dp(16), dp(16), dp(8))
                    },
                )
                addView(tabBar())
                addView(pages, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
            },
        )
        val saved = savedInstanceState?.getString(EXTRA_TAB)
        // onResume loads the dictionary next.
        val opening = when {
            !setupComplete() -> Tab.SETUP
            !prefs.hasApps -> Tab.APPS
            else -> Tab.DICTIONARY
        }
        show(tabFrom(saved) ?: tabFrom(intent.getStringExtra(EXTRA_TAB)) ?: opening, load = false)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        tabFrom(intent.getStringExtra(EXTRA_TAB))?.let(::show)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(EXTRA_TAB, tab.name)
    }

    private fun tabFrom(name: String?) = Tab.entries.firstOrNull { it.name == name }

    private fun tabBar() = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        val accent = TypedValue().also { theme.resolveAttribute(android.R.attr.colorAccent, it, true) }.data
        for (t in Tab.entries) {
            val label = TextView(context).apply {
                text = t.label
                textSize = 15f
                gravity = Gravity.CENTER
                setPadding(0, dp(12), 0, dp(10))
            }
            val mark = View(context).apply { setBackgroundColor(accent) }
            tabLabels[t] = label
            tabMarks[t] = mark
            addView(
                LinearLayout(context).apply {
                    orientation = LinearLayout.VERTICAL
                    isClickable = true
                    setOnClickListener { show(t) }
                    addView(label)
                    addView(mark, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(3)))
                },
                LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
            )
        }
    }

    private fun show(newTab: Tab, load: Boolean = true) {
        tab = newTab
        // A field on the tab being left would otherwise keep the keyboard up.
        currentFocus?.let {
            getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(it.windowToken, 0)
            it.clearFocus()
        }
        dictionaryPage.visibility = if (tab == Tab.DICTIONARY) View.VISIBLE else View.GONE
        appsPage.visibility = if (tab == Tab.APPS) View.VISIBLE else View.GONE
        setupPage.visibility = if (tab == Tab.SETUP) View.VISIBLE else View.GONE
        for (t in Tab.entries) {
            val selected = t == tab
            tabLabels.getValue(t).apply {
                alpha = if (selected) 1f else 0.6f
                setTypeface(null, if (selected) android.graphics.Typeface.BOLD else android.graphics.Typeface.NORMAL)
            }
            tabMarks.getValue(t).visibility = if (selected) View.VISIBLE else View.INVISIBLE
        }
        if (load && tab == Tab.DICTIONARY) dictionaryPage.reload()
    }

    /** The Setup tab: permissions, the server, the mic button, and how to use it. */
    private fun setupPage(): View {
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = dp(16)
            setPadding(pad, 0, pad, pad)
        }
        column.addView(heading("Status"))
        status = TextView(this).apply { textSize = 15f }
        column.addView(status)
        microphoneButton = button("Allow microphone and notifications") {
            disclose {
                val wanted = listOf(Manifest.permission.RECORD_AUDIO) +
                    if (Build.VERSION.SDK_INT >= 33) listOf(Manifest.permission.POST_NOTIFICATIONS) else emptyList()
                requestPermissions(wanted.toTypedArray(), 1)
            }
        }
        column.addView(microphoneButton)
        accessibilityButton = button("Turn on the mic button in Accessibility") {
            disclose { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        }
        column.addView(accessibilityButton)

        column.addView(heading("OpenWhispr"))
        column.addView(label("Server URL"))
        serverUrl = field(prefs.serverUrl, "https://m1.your-tailnet.ts.net:8450", InputType.TYPE_TEXT_VARIATION_URI)
        column.addView(serverUrl)
        column.addView(label("Token"))
        token = field(prefs.token, "From enable-mobile-dictation.sh", InputType.TYPE_TEXT_VARIATION_PASSWORD)
        column.addView(token)
        column.addView(button("Save and test connection") { saveAndTest() })
        connection = TextView(this).apply {
            textSize = 14f
            setPadding(0, dp(8), 0, 0)
        }
        column.addView(connection)

        column.addView(heading("Mic button"))
        column.addView(
            Switch(this).apply {
                text = "Lock it in place"
                isChecked = prefs.buttonLocked
                setPadding(0, dp(8), 0, dp(8))
                setOnCheckedChangeListener { _, checked -> prefs.buttonLocked = checked }
            },
        )
        column.addView(
            button("Move it back to the default spot") {
                prefs.resetButtonPosition()
                Toast.makeText(this, "The mic will be back above the keyboard on the right", Toast.LENGTH_SHORT).show()
            },
        )

        column.addView(heading("How to use it"))
        column.addView(
            hint(
                "Tap the mic to start, and tap it again to send. Tap ✕ beside it to cancel.\n\n" +
                    "If sending fails, the mic turns orange: tap it to try again, or ✕ to discard the recording.\n\n" +
                    "To move the mic, hold it until it turns blue, then drag. It stays where you drop it. " +
                    "Drop it on \"Hide in…\" at the top of the screen to hide it in that app. " +
                    "Bring it back in the Apps tab.",
            ),
        )
        return ScrollView(this).apply { addView(column) }
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
        // The mic's "Hide in" and new learned words change these while the app is in the background.
        appsPage.refresh()
        if (tab == Tab.DICTIONARY) dictionaryPage.reload()
    }

    override fun onPause() {
        super.onPause()
        prefs.serverUrl = serverUrl.text.toString()
        prefs.token = token.text.toString()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        refreshStatus()
    }

    override fun onDestroy() {
        worker.shutdownNow()
        super.onDestroy()
    }

    /**
     * What the app reads and sends, shown right before each permission request, as
     * Google Play requires. Only "Agree" goes on; back, tapping outside or
     * "Not now" leave the permission alone.
     */
    private fun disclose(onAgree: () -> Unit) {
        AlertDialog.Builder(this)
            .setTitle(R.string.disclosure_title)
            .setMessage(R.string.disclosure)
            .setPositiveButton("Agree") { _, _ -> onAgree() }
            .setNegativeButton("Not now", null)
            .show()
    }

    private fun microphoneAllowed() = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    // Android hides toasts from background apps without this, so errors would go unseen.
    // Before Android 13, notifications need no permission.
    private fun notificationsAllowed() = Build.VERSION.SDK_INT < 33 ||
        checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun setupComplete() = microphoneAllowed() && notificationsAllowed() && accessibilityEnabled() && prefs.isConfigured

    private fun refreshStatus() {
        val microphone = microphoneAllowed()
        val notifications = notificationsAllowed()
        val accessibility = accessibilityEnabled()
        microphoneButton.visibility = if (microphone && notifications) View.GONE else View.VISIBLE
        accessibilityButton.visibility = if (accessibility) View.GONE else View.VISIBLE
        status.text = listOf(
            (if (microphone) "✓" else "✗") + " Microphone allowed",
            (if (notifications) "✓" else "✗") + " Notifications allowed (needed to see errors)",
            (if (accessibility) "✓" else "✗") + " Mic button turned on in Accessibility",
            (if (prefs.isConfigured) "✓" else "✗") + " Server and token set",
            (if (prefs.hasApps) "✓" else "✗") + " Apps chosen for the mic, in the Apps tab",
        ).joinToString("\n")
        markTab(Tab.SETUP, !setupComplete())
        markTab(Tab.APPS, !prefs.hasApps)
    }

    /** A dot on the tab while something there is missing, so it's found from the other tabs. */
    private fun markTab(t: Tab, missing: Boolean) {
        tabLabels.getValue(t).text = if (!missing) {
            t.label
        } else {
            SpannableString("${t.label} ●").apply {
                setSpan(ForegroundColorSpan(0xFFE5484D.toInt()), length - 1, length, 0)
            }
        }
    }

    private fun accessibilityEnabled(): Boolean {
        val enabled = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
        val ours = ComponentName(this, MicAccessibilityService::class.java)
        return enabled.split(':').any { ComponentName.unflattenFromString(it) == ours }
    }

    private fun saveAndTest() {
        prefs.serverUrl = serverUrl.text.toString()
        prefs.token = token.text.toString()
        serverUrl.setText(prefs.serverUrl)
        refreshStatus()
        if (!prefs.isConfigured) {
            connection.text = "Set the server URL and token."
            return
        }
        connection.text = "Connecting…"
        val client = DictationClient(prefs.serverUrl, prefs.token)
        worker.execute {
            val result = runCatching { describe(client.health()) }
            runOnUiThread {
                connection.text = result.getOrElse { "Not connected: ${it.message}" }
            }
        }
    }

    /** Turns OpenWhispr's settings summary into a few readable lines. */
    private fun describe(health: JSONObject): String {
        val stt = health.optJSONObject("transcription")
        val cleanup = health.optJSONObject("cleanup")
        val lines = mutableListOf("Connected to OpenWhispr.")
        if (stt != null) {
            lines += "Speech to text: " + listOf(stt.optString("mode"), stt.optString("model"))
                .filter { it.isNotEmpty() && it != "null" }.joinToString(", ")
        }
        if (cleanup != null) {
            lines += if (cleanup.optBoolean("enabled")) {
                "Cleanup: " + cleanup.optString("model").takeUnless { it == "null" }.orEmpty() +
                    if (cleanup.optBoolean("customPrompt")) " with your own prompt" else " with the default prompt"
            } else {
                "Cleanup: off"
            }
        }
        lines += "Language: ${health.optString("language")}"
        lines += "Dictionary: ${count(health.optInt("dictionaryWords"), "word")}, ${count(health.optInt("snippets"), "snippet")}"
        return lines.joinToString("\n")
    }

    private fun count(n: Int, noun: String) = if (n == 1) "1 $noun" else "$n ${noun}s"

    private fun heading(text: String) = TextView(this).apply {
        this.text = text
        textSize = 18f
        setTypeface(typeface, android.graphics.Typeface.BOLD)
        setPadding(0, dp(28), 0, dp(4))
    }

    private fun hint(text: String) = TextView(this).apply {
        this.text = text
        textSize = 13f
        alpha = 0.7f
        setPadding(0, dp(4), 0, 0)
    }

    private fun label(text: String) = TextView(this).apply {
        this.text = text
        textSize = 13f
        setPadding(0, dp(16), 0, 0)
    }

    private fun field(value: String, hint: String, variation: Int) = EditText(this).apply {
        // Before inputType: single-line mode would otherwise undo the password masking.
        isSingleLine = true
        inputType = InputType.TYPE_CLASS_TEXT or variation
        setText(value)
        this.hint = hint
    }

    private fun button(text: String, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        setOnClickListener { onClick() }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    companion object {
        /** A [Tab] name, to open on that tab. */
        const val EXTRA_TAB = "tab"
    }
}
