package net.huuhka.whisprmic

import android.Manifest
import android.accessibilityservice.AccessibilityService
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.PixelFormat
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowInsets
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.widget.Toast
import java.io.File
import java.util.concurrent.Executors
import kotlin.math.hypot

/**
 * Shows the mic button above the keyboard while a text field has focus, in
 * every app the settings allow. Tap to record, tap again to send the recording
 * to OpenWhispr and type the text it returns into that field. The ✕ beside the
 * button cancels a recording. Hold the button to pick it up and move it, or
 * drop it on "Hide in <app>" to hide it in that app.
 *
 * The server's answer is untrusted text. It only ever goes into the field the
 * dictation started in, and only if that field is still focused, isn't a
 * password field and is in an app the mic is allowed in.
 *
 * After a dictation goes in, the field is followed until it's sent, left or a
 * minute passes. If the user corrected words in it, OpenWhispr adds them to its
 * dictionary, as its desktop auto-learn does after a paste.
 */
class MicAccessibilityService : AccessibilityService(), RecorderService.Listener {
    private val handler = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var prefs: Prefs
    private lateinit var windowManager: WindowManager
    private lateinit var button: MicButton
    private lateinit var buttonWindow: Overlay
    private lateinit var chipWindow: Overlay
    private lateinit var hideTarget: HideTarget
    private lateinit var targetWindow: Overlay
    private var state = MicState.IDLE
    /** The field the current dictation goes into. */
    private var target: AccessibilityNodeInfo? = null
    /** The app of the focused field, when it was last seen. */
    private var fieldPackage: String? = null
    /** Keyboard top, measured up from the bottom of the screen, when it was last seen. */
    private var keyboardHeight = 0
    private var updateScheduled = false
    /** True while the user holds the button to move it. */
    private var carrying = false
    /** The app "Hide in <app>" applies to, while that target is showing. */
    private var carriedFrom: String? = null
    private lateinit var buttonTouch: ButtonTouch
    /** Where and when the recording waiting in RETRY was made; it's dropped when you move on. */
    private var retryPackage: String? = null
    private var retrySinceMs = 0L
    /** Bumped when a send is cancelled, so its late answer is ignored. */
    private var sendId = 0
    private var destroyed = false
    /** The field of the last dictation and its text, while following how the user edits it. */
    private var watchedField: AccessibilityNodeInfo? = null
    private var watch: EditWatch? = null
    private val endWatch = Runnable {
        followEdits()
        finishWatch()
    }
    private var lastToast: Toast? = null

    override fun onServiceConnected() {
        prefs = Prefs(this)
        windowManager = getSystemService(WindowManager::class.java)
        buttonTouch = ButtonTouch()
        button = MicButton(this).apply { setOnTouchListener(buttonTouch) }
        buttonWindow = Overlay(button, dp(MicButton.SIZE_DP), dp(MicButton.SIZE_DP), Gravity.BOTTOM or Gravity.RIGHT)
        val chip = CancelChip(this).apply { setOnClickListener { onCancelTap() } }
        chipWindow = Overlay(chip, dp(CancelChip.SIZE_DP), dp(CancelChip.SIZE_DP), Gravity.BOTTOM or Gravity.RIGHT)
        hideTarget = HideTarget(this)
        targetWindow = Overlay(
            hideTarget,
            WindowManager.LayoutParams.WRAP_CONTENT,
            dp(HideTarget.HEIGHT_DP),
            Gravity.TOP or Gravity.CENTER_HORIZONTAL,
            touchable = false,
        )
        RecorderService.listener = this
        scheduleUpdate()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = scheduleUpdate()

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        destroyed = true
        if (state == MicState.STARTING || state == MicState.RECORDING) recorderCommand(RecorderService.ACTION_CANCEL)
        RecorderService.listener = null
        // Android can destroy the service before it ever connected.
        if (::buttonWindow.isInitialized) hide()
        if (state != MicState.STARTING && state != MicState.RECORDING) deleteRecording()
        handler.removeCallbacksAndMessages(null)
        worker.shutdownNow()
        super.onDestroy()
    }

    // --- Showing and placing the button ---

    private fun scheduleUpdate() {
        if (updateScheduled) return
        updateScheduled = true
        handler.postDelayed({
            updateScheduled = false
            update()
        }, UPDATE_DELAY_MS)
    }

    private fun update() {
        if (destroyed) return
        followEdits()
        val keyboard = windows.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
        if (keyboard != null) {
            val bounds = Rect().also(keyboard::getBoundsInScreen)
            keyboardHeight = screen().height() - bounds.top
        }
        val field = if (keyboard != null) focusedField() else null
        fieldPackage = field?.packageName?.toString()
        // A failed recording belongs to where it was made; don't offer to send it into another app later.
        if (state == MicState.RETRY) {
            // Whispr Mic's own settings don't count, so the server URL can be fixed in between.
            val elsewhere = fieldPackage != null && fieldPackage != retryPackage && fieldPackage != packageName
            if (elsewhere || System.currentTimeMillis() - retrySinceMs > RETRY_KEEP_MS) setState(MicState.IDLE)
        }
        val wanted = field != null && !field.isPassword && prefs.showsMicIn(fieldPackage.orEmpty())
        // Keep the button while dictating, even if the keyboard closed meanwhile.
        val busy = state == MicState.STARTING || state == MicState.RECORDING || state == MicState.WORKING
        if (wanted || busy || carrying) show() else hide()
    }

    private fun show() {
        if (!buttonWindow.shown) {
            placeButton()
            // Added first and kept hidden until needed, so the carried button stays above it.
            hideTarget.visibility = View.GONE
            targetWindow.show()
            buttonWindow.show()
            if (buttonWindow.shown && !prefs.hintShown) {
                prefs.hintShown = true
                toast("Tap the mic to dictate. Hold it to move it.")
            }
        } else if (!carrying) {
            placeButton()
        }
        updateChip()
    }

    /** Puts the button at its saved spot: a distance from the right edge and above the keyboard. */
    private fun placeButton() {
        val screen = screen()
        val size = dp(MicButton.SIZE_DP)
        val right = prefs.buttonRightPx.takeIf { it >= 0 } ?: dp(DEFAULT_RIGHT_DP)
        val above = prefs.buttonAboveKeyboardPx.takeIf { it >= 0 } ?: dp(DEFAULT_ABOVE_KEYBOARD_DP)
        buttonWindow.moveTo(
            right.coerceIn(0, screen.width() - size),
            (keyboardHeight + above).coerceIn(0, screen.height() - size),
        )
    }

    /** Shows the ✕ beside the button while there's a recording to cancel or discard. */
    private fun updateChip() {
        val needed = state != MicState.IDLE
        if (!needed || !buttonWindow.shown || carrying) return chipWindow.hide()
        val size = dp(MicButton.SIZE_DP)
        val chip = dp(CancelChip.SIZE_DP)
        val gap = dp(8)
        val button = buttonWindow.params
        // Left of the button, or right of it when the button sits at the left edge.
        var x = button.x + size + gap
        if (x + chip > screen().width()) x = button.x - gap - chip
        (chipWindow.view as CancelChip).describe(state)
        chipWindow.moveTo(x, button.y + (size - chip) / 2)
        chipWindow.show()
    }

    private fun hide() {
        // The view gets no further touch events once removed, so end any hold here.
        buttonTouch.reset()
        if (carrying) endCarry()
        buttonWindow.hide()
        chipWindow.hide()
        targetWindow.hide()
    }

    // --- Moving the button ---

    private fun startCarry() {
        if (!buttonWindow.shown) return
        carrying = true
        button.carried = true
        button.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
        chipWindow.hide()
        // Hiding the mic mid-dictation or with an unsent recording would lose it, so only offer it when idle.
        carriedFrom = fieldPackage.takeIf { state == MicState.IDLE }
        carriedFrom?.let { app ->
            hideTarget.setApp(appLabel(this, app))
            hideTarget.armed = false
            val statusBar = windowManager.currentWindowMetrics.windowInsets.getInsets(WindowInsets.Type.statusBars()).top
            targetWindow.moveTo(0, statusBar + dp(16))
            hideTarget.visibility = View.VISIBLE
        }
    }

    private fun carryTo(x: Int, y: Int, fingerX: Float, fingerY: Float) {
        val screen = screen()
        val size = dp(MicButton.SIZE_DP)
        buttonWindow.moveTo(x.coerceIn(0, screen.width() - size), y.coerceIn(0, screen.height() - size))
        if (hideTarget.visibility != View.VISIBLE) return
        val location = IntArray(2).also(hideTarget::getLocationOnScreen)
        val bounds = Rect(location[0], location[1], location[0] + hideTarget.width, location[1] + hideTarget.height)
        bounds.inset(-dp(24), -dp(24))
        val over = bounds.contains(fingerX.toInt(), fingerY.toInt())
        if (over != hideTarget.armed) {
            hideTarget.armed = over
            if (over) {
                val tick = if (Build.VERSION.SDK_INT >= 34) HapticFeedbackConstants.SEGMENT_TICK else HapticFeedbackConstants.CLOCK_TICK
                button.performHapticFeedback(tick)
            }
        }
    }

    /**
     * Drops the button: hides it in the app if it's on "Hide in <app>", else keeps it where it is.
     * A gesture Android cancelled (screen off, a call) never hides it.
     */
    private fun drop(allowHide: Boolean) {
        val hideIn = carriedFrom?.takeIf { hideTarget.armed && allowHide }
        endCarry()
        if (hideIn != null) {
            prefs.hideMicIn(hideIn)
            hide()
            toast("Mic hidden in ${appLabel(this, hideIn)}. You can bring it back in Whispr Mic → Apps.")
            return
        }
        val p = buttonWindow.params
        prefs.buttonRightPx = p.x
        // Dropped onto the keyboard: it settles just above it.
        prefs.buttonAboveKeyboardPx = (p.y - keyboardHeight).coerceAtLeast(0)
        placeButton()
        updateChip()
    }

    private fun endCarry() {
        carrying = false
        carriedFrom = null
        button.carried = false
        hideTarget.visibility = View.GONE
        scheduleUpdate()
    }

    // --- Dictation ---

    private fun onTap() {
        when (state) {
            MicState.IDLE -> startDictation()
            MicState.RECORDING -> {
                setState(MicState.WORKING)
                recorderCommand(RecorderService.ACTION_STOP)
            }
            MicState.RETRY -> sendAgain()
            MicState.STARTING, MicState.WORKING -> Unit
        }
    }

    private fun onCancelTap() {
        when (state) {
            MicState.STARTING, MicState.RECORDING -> {
                recorderCommand(RecorderService.ACTION_CANCEL)
                setState(MicState.IDLE)
            }
            MicState.WORKING -> {
                // Stop waiting; the answer, if it comes, is ignored.
                sendId++
                setState(MicState.IDLE)
            }
            MicState.RETRY -> setState(MicState.IDLE)
            MicState.IDLE -> Unit
        }
    }

    private fun startDictation() {
        if (!readyOrOpenSettings()) return
        val field = focusedField() ?: return toast("Tap into a text field first")
        if (!mayTypeInto(field)) return toast("The mic can't type into this field")
        target = field
        setState(MicState.STARTING)
        try {
            startForegroundService(Intent(this, RecorderService::class.java).setAction(RecorderService.ACTION_START))
        } catch (e: RuntimeException) {
            onRecordingFailed("Android didn't allow recording right now: ${e.message}")
        }
    }

    private fun sendAgain() {
        if (!readyOrOpenSettings()) return
        val file = RecorderService.recordingFile(this)
        if (file.length() <= WAV_HEADER_BYTES) {
            setState(MicState.IDLE)
            return toast("The recording is gone, so there's nothing to send")
        }
        val field = focusedField() ?: return toast("Tap into a text field first")
        if (!mayTypeInto(field)) return toast("The mic can't type into this field")
        target = field
        send(file)
    }

    override fun onRecordingStarted() {
        if (state != MicState.STARTING) return
        setState(MicState.RECORDING)
        button.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
    }

    override fun onRecordingFailed(message: String) {
        setState(MicState.IDLE)
        toast(message)
    }

    override fun onRecorded(recording: WavRecorder.Recording) {
        // Cancelled with ✕ while the recorder was finishing.
        if (state != MicState.WORKING) return
        when {
            recording.durationMs < MIN_RECORDING_MS -> {
                setState(MicState.IDLE)
                toast("Too short, nothing sent")
            }
            // A blocked microphone records silence instead of failing.
            recording.peak == 0 -> {
                setState(MicState.IDLE)
                toast("The recording was silent. Android may have blocked the microphone.")
            }
            else -> send(recording.file)
        }
    }

    private fun send(wav: File) {
        setState(MicState.WORKING)
        val client = DictationClient(prefs.serverUrl, prefs.token)
        val id = ++sendId
        worker.execute {
            val result = runCatching { client.dictate(wav) }
            handler.post { if (!destroyed && id == sendId) onResult(result) }
        }
    }

    private fun onResult(result: Result<DictationResult>) {
        val node = target
        target = null
        result.onFailure {
            val reason = it.message.orEmpty().trim().let { m -> if (m.endsWith('?') || m.endsWith('.')) m else "$m." }
            return keepForRetry(node, "Not sent: $reason Tap ↻ to retry.")
        }
        val dictation = result.getOrThrow()
        if (dictation.text.isBlank()) {
            setState(MicState.IDLE)
            return toast("No speech heard")
        }
        // Learn from the last dictation before this one changes the field.
        followEdits()
        finishWatch()
        // Checked again now: the user may have moved on while the server worked.
        // The text then goes nowhere, not even the clipboard, but the recording is kept to send again.
        if (node == null || !mayTypeInto(node)) {
            return keepForRetry(node, "You left the text field, so nothing was typed. Tap ↻ to send it to the field you're in.")
        }
        setState(MicState.IDLE)
        if (!insert(node, dictation.text)) {
            copyToClipboard(dictation.text)
            toast("Couldn't type into the field. The text is on the clipboard.")
        } else if (dictation.cleanupFailed) {
            toast("Cleanup failed, so this is the raw transcript")
        }
    }

    private fun keepForRetry(node: AccessibilityNodeInfo?, message: String) {
        retryPackage = node?.packageName?.toString() ?: fieldPackage
        retrySinceMs = System.currentTimeMillis()
        setState(MicState.RETRY)
        toast(message)
    }

    /**
     * Whether dictated text may go into [node]: it's the focused field, editable,
     * not a password field, and in an app the mic is allowed in. Checked when a
     * dictation starts or is sent again, and just before its text goes in.
     */
    private fun mayTypeInto(node: AccessibilityNodeInfo): Boolean =
        node.refresh() && node.isVisibleToUser && node.isEditable && !node.isPassword &&
            prefs.showsMicIn(node.packageName?.toString().orEmpty()) && focusedField() == node

    /** Types the text at the cursor of [node], which [mayTypeInto] just allowed. */
    private fun insert(node: AccessibilityNodeInfo, text: String): Boolean {
        val field = FieldText(fieldText(node), node.textSelectionStart, node.textSelectionEnd)
        val insertion = insertDictation(field, text)
        val setText = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, insertion.text)
        }
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, setText)) return false
        val cursor = Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, insertion.cursor)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, insertion.cursor)
        }
        node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, cursor)
        if (prefs.learnCorrections) {
            watchedField = node
            watch = EditWatch(text.trim(), insertion)
            handler.postDelayed(endWatch, WATCH_MS)
        }
        return true
    }

    /** The field's real text, without a placeholder it reports as text. */
    private fun fieldText(node: AccessibilityNodeInfo): String = realFieldText(
        node.text,
        node.hintText,
        node.isShowingHintText,
        hasCursor = node.textSelectionStart >= 0 ||
            node.actionList.contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_SELECTION),
    )

    // --- Learning from corrections ---

    /** Reads the watched field's text, and ends the watch once it's sent, cleared or left. */
    private fun followEdits() {
        val node = watchedField ?: return
        val left = !node.refresh() || !node.isFocused
        if (left || watch?.update(fieldText(node)) == false) finishWatch()
    }

    /** Sends the user's corrections, if any, and shows the words OpenWhispr learned from them. */
    private fun finishWatch() {
        handler.removeCallbacks(endWatch)
        val done = watch ?: return
        watch = null
        watchedField = null
        val edited = done.edited() ?: return
        if (!prefs.isConfigured) return
        val client = DictationClient(prefs.serverUrl, prefs.token)
        worker.execute {
            // Learning is a bonus, so a failure only goes to the log.
            val learned = runCatching { client.learn(done.original, edited) }
                .onFailure { Log.w(TAG, "Couldn't send corrections", it) }
                .getOrNull().orEmpty()
            if (learned.isNotEmpty()) {
                handler.post {
                    if (destroyed) return@post
                    toast("Added ${LearnedWords.quote(learned)} to the dictionary")
                    LearnedWords.notify(this, learned)
                }
            }
        }
    }

    // --- Helpers ---

    private fun focusedField(): AccessibilityNodeInfo? {
        val roots = listOfNotNull(rootInActiveWindow) +
            windows.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }.mapNotNull { it.root }
        for (root in roots) {
            root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.takeIf { it.isEditable }?.let { return it }
            // Some apps report a stale container as input focus; look for the focused field instead.
            focusedEditable(root)?.let { return it }
        }
        return null
    }

    private fun focusedEditable(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val queue = ArrayDeque<AccessibilityNodeInfo>().apply { add(root) }
        var visited = 0
        while (queue.isNotEmpty() && visited++ < MAX_NODES_SEARCHED) {
            val node = queue.removeFirst()
            if (node.isFocused && node.isEditable) return node
            for (i in 0 until node.childCount) node.getChild(i)?.let(queue::addLast)
        }
        return null
    }

    private fun readyOrOpenSettings(): Boolean {
        val problem = when {
            !prefs.isConfigured -> "Set the server URL and token in Whispr Mic"
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED ->
                "Allow microphone access in Whispr Mic"
            else -> return true
        }
        toast(problem)
        startActivity(
            Intent(this, SettingsActivity::class.java)
                .putExtra(SettingsActivity.EXTRA_TAB, SettingsActivity.Tab.SETUP.name)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        )
        return false
    }

    private fun setState(newState: MicState) {
        // Nothing will send the recording again, so it needn't stay on the phone.
        // A cancelled one is deleted by RecorderService once it stops writing.
        if (newState == MicState.IDLE && state != MicState.STARTING && state != MicState.RECORDING) deleteRecording()
        state = newState
        button.render(newState)
        // A screen lock mid-dictation leaves no field to type the text into.
        buttonWindow.keepScreenOn(newState == MicState.STARTING || newState == MicState.RECORDING || newState == MicState.WORKING)
        updateChip()
        if (newState == MicState.IDLE || newState == MicState.RETRY) scheduleUpdate()
    }

    private fun deleteRecording() {
        RecorderService.recordingFile(this).delete()
    }

    private fun recorderCommand(action: String) {
        startService(Intent(this, RecorderService::class.java).setAction(action))
    }

    private fun copyToClipboard(text: String) {
        getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Dictation", text))
    }

    /** Replaces any message still showing, so they don't queue up. */
    private fun toast(message: String) {
        lastToast?.cancel()
        lastToast = Toast.makeText(this, message, Toast.LENGTH_LONG).also { it.show() }
    }

    private fun screen(): Rect = windowManager.currentWindowMetrics.bounds

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    /**
     * Tap to dictate; hold to pick the button up and drag it. A quick swipe does
     * neither, so the button can't be moved or pressed by accident.
     */
    private inner class ButtonTouch : View.OnTouchListener {
        private val slop = ViewConfiguration.get(this@MicAccessibilityService).scaledTouchSlop
        private var downX = 0f
        private var downY = 0f
        private var lastX = 0f
        private var lastY = 0f
        private var startX = 0
        private var startY = 0
        /** The finger moved before the hold was long enough, so this touch does nothing. */
        private var slid = false
        private var held = false
        private var hinted = false
        private val pickUp = Runnable {
            held = true
            if (prefs.buttonLocked) {
                button.performHapticFeedback(HapticFeedbackConstants.REJECT)
                toast("The mic is locked in place. You can unlock it in Whispr Mic.")
            } else {
                startCarry()
                // Carry from where the button and finger are now; the button may have moved during the hold.
                downX = lastX
                downY = lastY
                startX = buttonWindow.params.x
                startY = buttonWindow.params.y
            }
        }

        /** Forgets the current touch, for when the button disappears under the finger. */
        fun reset() {
            handler.removeCallbacks(pickUp)
            held = true
        }

        override fun onTouch(view: View, event: MotionEvent): Boolean {
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX
                    downY = event.rawY
                    lastX = downX
                    lastY = downY
                    startX = buttonWindow.params.x
                    startY = buttonWindow.params.y
                    slid = false
                    held = false
                    hinted = false
                    handler.postDelayed(pickUp, ViewConfiguration.getLongPressTimeout().toLong())
                }
                MotionEvent.ACTION_MOVE -> {
                    lastX = event.rawX
                    lastY = event.rawY
                    val dx = event.rawX - downX
                    val dy = event.rawY - downY
                    val distance = hypot(dx, dy)
                    if (carrying) {
                        // Gravity is bottom-right, so offsets grow leftwards and upwards.
                        carryTo((startX - dx).toInt(), (startY - dy).toInt(), event.rawX, event.rawY)
                    } else if (!held && distance > slop) {
                        slid = true
                        handler.removeCallbacks(pickUp)
                        if (!hinted && distance > slop * 4 && !prefs.buttonLocked) {
                            hinted = true
                            toast("Hold the mic for a moment to move it")
                        }
                    }
                }
                MotionEvent.ACTION_UP -> {
                    handler.removeCallbacks(pickUp)
                    if (carrying) {
                        drop(allowHide = true)
                    } else if (!slid && !held) {
                        view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                        onTap()
                    }
                }
                MotionEvent.ACTION_CANCEL -> {
                    handler.removeCallbacks(pickUp)
                    if (carrying) drop(allowHide = false)
                }
            }
            return true
        }
    }

    /** A view in its own overlay window. Not focusable, so the text field and keyboard stay as they are. */
    private inner class Overlay(val view: View, width: Int, height: Int, gravity: Int, touchable: Boolean = true) {
        val params = WindowManager.LayoutParams(
            width,
            height,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                if (touchable) 0 else WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            this.gravity = gravity
            fitInsetsTypes = 0
        }
        var shown = false
            private set

        fun show() {
            if (shown) return
            try {
                windowManager.addView(view, params)
                shown = true
            } catch (e: RuntimeException) {
                Log.w(TAG, "Couldn't show an overlay", e)
            }
        }

        fun moveTo(x: Int, y: Int) {
            if (params.x == x && params.y == y) return
            params.x = x
            params.y = y
            // The system may have removed the view already.
            if (shown) runCatching { windowManager.updateViewLayout(view, params) }
        }

        /** Holds off the screen timeout while this window is visible. */
        fun keepScreenOn(on: Boolean) {
            val flags = if (on) {
                params.flags or WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            } else {
                params.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON.inv()
            }
            if (flags == params.flags) return
            params.flags = flags
            if (shown) runCatching { windowManager.updateViewLayout(view, params) }
        }

        fun hide() {
            if (!shown) return
            runCatching { windowManager.removeView(view) }
            shown = false
        }
    }

    companion object {
        private const val TAG = "WhisprMic"
        private const val UPDATE_DELAY_MS = 80L
        private const val DEFAULT_RIGHT_DP = 12
        private const val DEFAULT_ABOVE_KEYBOARD_DP = 64
        private const val MIN_RECORDING_MS = 300L
        private const val WAV_HEADER_BYTES = 44L
        private const val MAX_NODES_SEARCHED = 400
        private const val RETRY_KEEP_MS = 10 * 60 * 1000L
        /** How long after a dictation its corrections count, if it isn't sent or left before. */
        private const val WATCH_MS = 60 * 1000L
    }
}
