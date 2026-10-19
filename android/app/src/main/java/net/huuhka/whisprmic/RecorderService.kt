package net.huuhka.whisprmic

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.IBinder
import java.io.File

/**
 * Holds the microphone while recording. Android only lets a background app
 * record from a microphone-type foreground service; MicAccessibilityService
 * starts this one when the button is tapped and gets the result through
 * [Listener]. Everything here runs on the main thread.
 */
class RecorderService : Service() {
    private var recorder: WavRecorder? = null

    interface Listener {
        fun onRecordingStarted()
        fun onRecordingFailed(message: String)
        fun onRecorded(recording: WavRecorder.Recording)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> start()
            ACTION_STOP -> finish(deliver = true)
            ACTION_CANCEL -> finish(deliver = false)
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        recorder?.stop()
        recorder = null
        super.onDestroy()
    }

    private fun start() {
        if (recorder != null) {
            listener?.onRecordingStarted()
            return
        }
        try {
            // Must come first: startForegroundService() requires it within a few seconds.
            startForeground(NOTIFICATION_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } catch (e: Exception) {
            fail("Android didn't allow recording right now: ${e.message}")
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            fail("Allow microphone access in Whispr Mic first")
            return
        }
        try {
            recorder = WavRecorder(recordingFile(this)).also { it.start() }
        } catch (e: Exception) {
            fail(e.message ?: "The microphone couldn't be opened")
            return
        }
        listener?.onRecordingStarted()
    }

    private fun finish(deliver: Boolean) {
        val recording = recorder?.stop()
        recorder = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        if (!deliver) {
            recordingFile(this).delete()
            return
        }
        // Without a recording (the service was restarted meanwhile), say so rather than leave the button waiting.
        if (recording != null) listener?.onRecorded(recording) else listener?.onRecordingFailed("Nothing was recorded")
    }

    private fun fail(message: String) {
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        listener?.onRecordingFailed(message)
    }

    private fun notification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Recording", NotificationManager.IMPORTANCE_LOW),
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle("Listening")
            .setOngoing(true)
            .build()
    }

    companion object {
        const val ACTION_START = "start"
        const val ACTION_STOP = "stop"
        const val ACTION_CANCEL = "cancel"
        private const val CHANNEL_ID = "recording"
        private const val NOTIFICATION_ID = 1

        var listener: Listener? = null

        /** The latest recording, kept only until it's sent or given up on, so it can be sent again. */
        fun recordingFile(context: Context) = File(context.cacheDir, "last-dictation.wav")
    }
}
