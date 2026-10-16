package net.huuhka.whisprmic

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView

/** RETRY: the last recording wasn't sent; tap to send it again. */
enum class MicState { IDLE, STARTING, RECORDING, WORKING, RETRY }

/** The round floating button: mic when idle, stop while recording, spinner while working. */
class MicButton(context: Context) : FrameLayout(context) {
    private val circle = GradientDrawable().apply { shape = GradientDrawable.OVAL }
    private val icon = ImageView(context)
    private val spinner = ProgressBar(context).apply {
        isIndeterminate = true
        indeterminateTintList = ColorStateList.valueOf(WHITE)
    }
    private var state = MicState.IDLE

    /** True while the user holds the button to move it. */
    var carried = false
        set(value) {
            field = value
            render(state)
        }

    init {
        background = circle
        elevation = dp(4).toFloat()
        val iconSize = dp(26)
        addView(icon, LayoutParams(iconSize, iconSize, Gravity.CENTER))
        addView(spinner, LayoutParams(dp(28), dp(28), Gravity.CENTER))
        render(MicState.IDLE)
    }

    fun render(state: MicState) {
        this.state = state
        circle.setColor(
            when {
                carried -> CARRIED_COLOR
                state == MicState.STARTING || state == MicState.RECORDING -> RECORDING_COLOR
                state == MicState.RETRY -> RETRY_COLOR
                state == MicState.WORKING -> WORKING_COLOR
                else -> IDLE_COLOR
            },
        )
        icon.setImageResource(
            when {
                carried -> R.drawable.ic_move
                state == MicState.STARTING || state == MicState.RECORDING -> R.drawable.ic_stop
                state == MicState.RETRY -> R.drawable.ic_retry
                else -> R.drawable.ic_mic
            },
        )
        val working = state == MicState.WORKING && !carried
        icon.visibility = if (working) GONE else VISIBLE
        spinner.visibility = if (working) VISIBLE else GONE
        alpha = if (state == MicState.STARTING) 0.6f else 1f
        contentDescription = when (state) {
            MicState.IDLE -> "Dictate"
            MicState.STARTING, MicState.RECORDING -> "Stop and send"
            MicState.WORKING -> "Transcribing"
            MicState.RETRY -> "Send the recording again"
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    companion object {
        const val SIZE_DP = 52
        const val WHITE = 0xFFFFFFFF.toInt()
        const val IDLE_COLOR = 0xEE2B2D31.toInt()
        const val RECORDING_COLOR = 0xFFD93025.toInt()
        const val WORKING_COLOR = 0xEE3C4043.toInt()
        const val RETRY_COLOR = 0xFFE37400.toInt()
        const val CARRIED_COLOR = 0xFF1A73E8.toInt()
    }
}

/** The small ✕ next to the mic: cancels a recording, or drops one that failed to send. */
class CancelChip(context: Context) : FrameLayout(context) {
    init {
        background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(MicButton.IDLE_COLOR)
        }
        elevation = dp(3).toFloat()
        addView(ImageView(context).apply { setImageResource(R.drawable.ic_close) }, LayoutParams(dp(18), dp(18), Gravity.CENTER))
    }

    fun describe(state: MicState) {
        contentDescription = when (state) {
            MicState.RETRY -> "Discard the recording"
            MicState.WORKING -> "Stop waiting for the text"
            else -> "Cancel recording"
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    companion object {
        const val SIZE_DP = 34
    }
}

/** Shown at the top of the screen while the mic is carried: drop the mic here to hide it in this app. */
class HideTarget(context: Context) : LinearLayout(context) {
    private val pill = GradientDrawable().apply { cornerRadius = dp(24).toFloat() }
    private val label = TextView(context).apply {
        setTextColor(MicButton.WHITE)
        textSize = 15f
        maxLines = 1
        ellipsize = TextUtils.TruncateAt.END
    }

    var armed = false
        set(value) {
            field = value
            pill.setColor(if (value) MicButton.RECORDING_COLOR else MicButton.IDLE_COLOR)
        }

    init {
        orientation = HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        background = pill
        elevation = dp(4).toFloat()
        setPadding(dp(18), 0, dp(20), 0)
        addView(ImageView(context).apply { setImageResource(R.drawable.ic_hide) }, LayoutParams(dp(20), dp(20)))
        addView(label, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply { marginStart = dp(10) })
        armed = false
    }

    fun setApp(appName: String) {
        label.text = "Hide in $appName"
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    companion object {
        const val HEIGHT_DP = 48
    }
}
