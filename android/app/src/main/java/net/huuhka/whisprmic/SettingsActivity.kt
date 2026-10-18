package net.huuhka.whisprmic

import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import java.util.concurrent.Executors
import org.json.JSONObject

/** Permissions, the OpenWhispr server, which apps get the mic, and the button itself. */
class SettingsActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var serverUrl: EditText
    private lateinit var token: EditText
    private lateinit var appFilter: RadioGroup
    private lateinit var appsSummary: TextView
    private lateinit var chooseApps: Button
    private lateinit var status: TextView
    private lateinit var connection: TextView
    private lateinit var microphoneButton: Button
    private lateinit var accessibilityButton: Button
    private val worker = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestWindowFeature(Window.FEATURE_NO_TITLE)
        prefs = Prefs(this)

        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = dp(20)
            setPadding(pad, 0, pad, pad)
        }
        column.addView(
            TextView(this).apply {
                text = getString(R.string.app_name)
                textSize = 26f
                setPadding(0, dp(20), 0, 0)
            },
        )
        column.addView(heading("Setup"))
        status = TextView(this).apply { textSize = 15f }
        column.addView(status)
        microphoneButton = button("Allow microphone and notifications") {
            val wanted = listOf(Manifest.permission.RECORD_AUDIO) +
                if (Build.VERSION.SDK_INT >= 33) listOf(Manifest.permission.POST_NOTIFICATIONS) else emptyList()
            requestPermissions(wanted.toTypedArray(), 1)
        }
        column.addView(microphoneButton)
        accessibilityButton = button("Turn on the mic button in Accessibility") {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
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

        column.addView(heading("Where the mic shows"))
        appFilter = RadioGroup(this).apply {
            addView(RadioButton(context).apply {
                id = View.generateViewId()
                text = "In every app"
                tag = AppFilter.ALL_EXCEPT_HIDDEN
            })
            addView(RadioButton(context).apply {
                id = View.generateViewId()
                text = "Only in apps I choose"
                tag = AppFilter.ONLY_SHOWN
            })
        }
        column.addView(appFilter)
        appsSummary = TextView(this).apply {
            textSize = 14f
            alpha = 0.8f
            setPadding(0, dp(4), 0, 0)
        }
        column.addView(appsSummary)
        chooseApps = button("") { startActivity(Intent(this, AppPickerActivity::class.java)) }
        column.addView(chooseApps)
        column.addView(hint("It never shows in password fields."))

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

        column.addView(heading("Dictionary"))
        column.addView(hint("OpenWhispr's word list, and learning from your corrections."))
        column.addView(button("Edit the dictionary") { startActivity(Intent(this, DictionaryActivity::class.java)) })

        column.addView(heading("How to use it"))
        column.addView(
            hint(
                "Tap the mic to start, and tap it again to send. Tap ✕ beside it to cancel.\n\n" +
                    "If sending fails, the mic turns orange: tap it to try again, or ✕ to discard the recording.\n\n" +
                    "To move the mic, hold it until it turns blue, then drag. It stays where you drop it. " +
                    "Drop it on \"Hide in…\" at the top of the screen to hide it in that app.",
            ),
        )
        setContentView(
            ScrollView(this).apply {
                // Takes the initial focus, so the screen doesn't open scrolled to a field with the keyboard up.
                isFocusableInTouchMode = true
                descendantFocusability = ViewGroup.FOCUS_BEFORE_DESCENDANTS
                clipToPadding = false
                padForSystemBars()
                addView(column)
            },
        )
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
        refreshApps()
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

    private fun refreshStatus() {
        val microphone = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        // Android hides toasts from background apps without this, so errors would go unseen.
        // Before Android 13, notifications need no permission.
        val notifications = Build.VERSION.SDK_INT < 33 ||
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        val accessibility = accessibilityEnabled()
        microphoneButton.visibility = if (microphone && notifications) View.GONE else View.VISIBLE
        accessibilityButton.visibility = if (accessibility) View.GONE else View.VISIBLE
        status.text = listOf(
            (if (microphone) "✓" else "✗") + " Microphone allowed",
            (if (notifications) "✓" else "✗") + " Notifications allowed (needed to see errors)",
            (if (accessibility) "✓" else "✗") + " Mic button turned on in Accessibility",
            (if (prefs.isConfigured) "✓" else "✗") + " Server and token set",
        ).joinToString("\n")
    }

    private fun refreshApps() {
        val filter = prefs.appFilter
        appFilter.setOnCheckedChangeListener(null)
        appFilter.check(appFilter.findViewWithTag<RadioButton>(filter).id)
        appFilter.setOnCheckedChangeListener { group, id ->
            prefs.appFilter = group.findViewById<RadioButton>(id).tag as AppFilter
            refreshApps()
        }
        val names = prefs.filterApps.map { AppPickerActivity.appLabel(this, it) }.sorted()
        val list = when (names.size) {
            0 -> null
            in 1..3 -> names.joinToString(", ")
            else -> names.take(2).joinToString(", ") + " and ${names.size - 2} more"
        }
        if (filter == AppFilter.ONLY_SHOWN) {
            appsSummary.text = if (list == null) "No apps chosen yet, so the mic doesn't show anywhere." else "Shows in $list."
            chooseApps.text = "Choose apps"
        } else {
            appsSummary.text = if (list == null) "Not hidden in any app." else "Hidden in $list."
            chooseApps.text = "Choose apps to hide it in"
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
}
