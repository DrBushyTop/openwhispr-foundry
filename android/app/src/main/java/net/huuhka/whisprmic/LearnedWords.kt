package net.huuhka.whisprmic

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Handler
import android.os.Looper
import android.widget.Toast

/**
 * The "Added … to the dictionary" notification. Undo removes the words again;
 * a tap opens the dictionary. Each one replaces the last, so older words are
 * removed in the dictionary instead.
 */
object LearnedWords {
    private const val CHANNEL_ID = "learned"
    private const val NOTIFICATION_ID = 2
    private const val EXTRA_WORDS = "words"

    fun quote(words: List<String>) = words.joinToString(", ") { "“$it”" }

    fun notify(context: Context, words: List<String>) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Learned words", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Words added to OpenWhispr's dictionary from your corrections"
            },
        )
        val undo = PendingIntent.getBroadcast(
            context,
            0,
            Intent(context, UndoReceiver::class.java).putStringArrayListExtra(EXTRA_WORDS, ArrayList(words)),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val open = PendingIntent.getActivity(
            context,
            0,
            Intent(context, DictionaryActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        manager.notify(
            NOTIFICATION_ID,
            Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_mic)
                .setContentTitle("Added ${quote(words)} to the dictionary")
                .setContentText("From your corrections. Tap to see the dictionary.")
                .setContentIntent(open)
                .setAutoCancel(true)
                .addAction(Notification.Action.Builder(null as Icon?, "Undo", undo).build())
                .build(),
        )
    }

    class UndoReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val words = intent.getStringArrayListExtra(EXTRA_WORDS) ?: return
            val prefs = Prefs(context)
            context.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
            val pending = goAsync()
            Thread {
                val result = runCatching { DictationClient(prefs.serverUrl, prefs.token).changeDictionary(remove = words) }
                val message = result.fold(
                    { "Removed ${quote(words)} from the dictionary" },
                    { "Couldn't remove ${quote(words)}: ${it.message}" },
                )
                Handler(Looper.getMainLooper()).post {
                    Toast.makeText(context, message, Toast.LENGTH_LONG).show()
                    pending.finish()
                }
            }.start()
        }
    }
}
