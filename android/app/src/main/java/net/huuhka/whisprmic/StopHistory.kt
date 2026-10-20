package net.huuhka.whisprmic

import android.app.ActivityManager
import android.app.Application
import android.app.ApplicationExitInfo
import android.content.Context
import android.text.format.DateUtils
import java.io.File

/** Starts [StopHistory.recordCrashes] once per process, before any activity or service. */
class WhisprMicApp : Application() {
    override fun onCreate() {
        super.onCreate()
        StopHistory.recordCrashes(this)
    }
}

/**
 * Why the app's process ended lately, for the Setup tab. A force stop turns the
 * mic's accessibility service off, and some phones force-stop apps on their
 * own, so this tells that apart from a crash or the system closing the app.
 */
object StopHistory {
    private const val CRASH_FILE = "last-crash.txt"
    private const val SHOWN = 3
    private const val CRASH_LINES = 8

    /** Keeps the start of a crash's stack trace, which Android's exit record doesn't. */
    fun recordCrashes(context: Context) {
        val file = File(context.filesDir, CRASH_FILE)
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            runCatching {
                val stack = error.stackTraceToString().lineSequence().take(CRASH_LINES).joinToString("\n")
                file.writeText("${System.currentTimeMillis()}\n$stack")
            }
            previous?.uncaughtException(thread, error)
        }
    }

    /** The last few times the app stopped and why, newest first, then the last crash's stack, if any. */
    fun describe(context: Context): String {
        val exits = context.getSystemService(ActivityManager::class.java)
            .getHistoricalProcessExitReasons(null, 0, SHOWN)
        if (exits.isEmpty()) return "No stops recorded since it was installed."
        val lines = exits.map { "${time(context, it.timestamp)}: ${reason(it)}" }
        val crash = File(context.filesDir, CRASH_FILE).takeIf { it.exists() }?.readText()?.let { text ->
            val at = text.substringBefore('\n').toLongOrNull() ?: return@let null
            "\n\nLast crash, ${time(context, at)}:\n${text.substringAfter('\n')}"
        }
        return lines.joinToString("\n") + crash.orEmpty()
    }

    private fun time(context: Context, millis: Long) = DateUtils.formatDateTime(
        context,
        millis,
        DateUtils.FORMAT_SHOW_DATE or DateUtils.FORMAT_SHOW_TIME or DateUtils.FORMAT_ABBREV_MONTH,
    )

    private fun reason(exit: ApplicationExitInfo): String {
        val why = when (exit.reason) {
            ApplicationExitInfo.REASON_USER_REQUESTED ->
                "force-stopped, from App info, a cleanup or swiping it away. This turns the mic off in Accessibility"
            ApplicationExitInfo.REASON_CRASH -> "crashed"
            ApplicationExitInfo.REASON_CRASH_NATIVE -> "crashed in native code"
            ApplicationExitInfo.REASON_ANR -> "stopped responding"
            ApplicationExitInfo.REASON_LOW_MEMORY -> "closed to free memory"
            ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE -> "closed for using too much battery or CPU"
            ApplicationExitInfo.REASON_PERMISSION_CHANGE -> "restarted after a permission change"
            ApplicationExitInfo.REASON_EXIT_SELF -> "exited"
            ApplicationExitInfo.REASON_FREEZER -> "closed while frozen in the background"
            ApplicationExitInfo.REASON_PACKAGE_UPDATED -> "updated"
            ApplicationExitInfo.REASON_SIGNALED -> "killed by the system"
            else -> "closed by the system"
        }
        // Phone makers often say which of their tools did it here.
        return exit.description?.takeIf { it.isNotBlank() }?.let { "$why ($it)" } ?: why
    }
}
