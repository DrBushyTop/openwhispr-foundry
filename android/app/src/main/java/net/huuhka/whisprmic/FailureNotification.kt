package net.huuhka.whisprmic

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context

/** A failed dictation's whole error. The toast shows only two lines of it. */
object FailureNotification {
    private const val CHANNEL_ID = "failures"
    private const val NOTIFICATION_ID = 3

    fun show(context: Context, message: String) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Dictation errors", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Why a dictation couldn't be sent, in full"
            },
        )
        manager.notify(
            NOTIFICATION_ID,
            Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_mic)
                .setContentTitle("Dictation not sent")
                .setContentText(message)
                .setStyle(Notification.BigTextStyle().bigText(message))
                .build(),
        )
    }

    /** Called when a dictation goes through, so an old error doesn't linger. */
    fun clear(context: Context) {
        context.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
    }
}
