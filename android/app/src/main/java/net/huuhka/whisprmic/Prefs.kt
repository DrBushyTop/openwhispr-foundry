package net.huuhka.whisprmic

import android.content.Context

/** Which apps get the mic: every app minus [Prefs.hiddenApps], or only [Prefs.shownApps]. */
enum class AppFilter { ALL_EXCEPT_HIDDEN, ONLY_SHOWN }

/** Everything the app stores. The token sits in app-private storage, which isn't backed up. */
class Prefs(context: Context) {
    private val prefs = context.getSharedPreferences("whispr-mic", Context.MODE_PRIVATE)
    private val ownPackage = context.packageName

    var serverUrl: String
        get() = prefs.getString(SERVER_URL, "").orEmpty()
        set(value) = prefs.edit().putString(SERVER_URL, value.trim().trimEnd('/')).apply()

    var token: String
        get() = prefs.getString(TOKEN, "").orEmpty()
        set(value) = prefs.edit().putString(TOKEN, value.trim()).apply()

    var appFilter: AppFilter
        get() = if (prefs.getString(APP_FILTER, null) == AppFilter.ONLY_SHOWN.name) AppFilter.ONLY_SHOWN else AppFilter.ALL_EXCEPT_HIDDEN
        set(value) = prefs.edit().putString(APP_FILTER, value.name).apply()

    var hiddenApps: Set<String>
        get() = prefs.getStringSet(HIDDEN_APPS, null).orEmpty()
        set(value) = prefs.edit().putStringSet(HIDDEN_APPS, value).apply()

    /** Both lists are kept, so switching between the two filters doesn't lose either. */
    var shownApps: Set<String>
        get() = prefs.getStringSet(SHOWN_APPS, null) ?: DEFAULT_SHOWN_APPS
        set(value) = prefs.edit().putStringSet(SHOWN_APPS, value).apply()

    /** The list the current filter uses. */
    var filterApps: Set<String>
        get() = if (appFilter == AppFilter.ONLY_SHOWN) shownApps else hiddenApps
        set(value) {
            if (appFilter == AppFilter.ONLY_SHOWN) shownApps = value else hiddenApps = value
        }

    fun showsMicIn(packageName: String): Boolean = when {
        packageName == ownPackage -> false
        appFilter == AppFilter.ONLY_SHOWN -> packageName in shownApps
        else -> packageName !in hiddenApps
    }

    fun hideMicIn(packageName: String) {
        if (appFilter == AppFilter.ONLY_SHOWN) shownApps = shownApps - packageName else hiddenApps = hiddenApps + packageName
    }

    /** Button position: distance from the right edge, and height above the keyboard, in px. */
    var buttonRightPx: Int
        get() = prefs.getInt(BUTTON_RIGHT, -1)
        set(value) = prefs.edit().putInt(BUTTON_RIGHT, value).apply()

    var buttonAboveKeyboardPx: Int
        get() = prefs.getInt(BUTTON_ABOVE_KEYBOARD, -1)
        set(value) = prefs.edit().putInt(BUTTON_ABOVE_KEYBOARD, value).apply()

    fun resetButtonPosition() = prefs.edit().remove(BUTTON_RIGHT).remove(BUTTON_ABOVE_KEYBOARD).apply()

    var buttonLocked: Boolean
        get() = prefs.getBoolean(BUTTON_LOCKED, false)
        set(value) = prefs.edit().putBoolean(BUTTON_LOCKED, value).apply()

    /** Whether the "tap to dictate, hold to move" hint has been shown. */
    var hintShown: Boolean
        get() = prefs.getBoolean(HINT_SHOWN, false)
        set(value) = prefs.edit().putBoolean(HINT_SHOWN, value).apply()

    val isConfigured: Boolean
        get() = serverUrl.isNotEmpty() && token.isNotEmpty()

    companion object {
        private const val SERVER_URL = "serverUrl"
        private const val TOKEN = "token"
        private const val APP_FILTER = "appFilter"
        private const val HIDDEN_APPS = "hiddenApps"
        private const val SHOWN_APPS = "shownApps"
        private const val BUTTON_RIGHT = "buttonRightPx"
        private const val BUTTON_ABOVE_KEYBOARD = "buttonAboveKeyboardPx"
        private const val BUTTON_LOCKED = "buttonLocked"
        private const val HINT_SHOWN = "hintShown"

        /** A starting point for "only these apps": T3 Code's production, preview and development builds. */
        val DEFAULT_SHOWN_APPS = setOf(
            "com.t3tools.t3code",
            "com.t3tools.t3code.preview",
            "com.t3tools.t3code.dev",
        )
    }
}
