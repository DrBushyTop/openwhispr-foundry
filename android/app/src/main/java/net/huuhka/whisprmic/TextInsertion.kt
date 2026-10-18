package net.huuhka.whisprmic

/** A text field's content and selection, as accessibility reports them. */
data class FieldText(val text: String, val selectionStart: Int, val selectionEnd: Int)

/** The field's new text. The dictation, with any spaces added around it, runs from [start] to [cursor]. */
data class Insertion(val text: String, val start: Int, val cursor: Int)

/**
 * Puts dictated text at the cursor, replacing any selection, with a space on
 * either side where it would otherwise run into a word. Without a cursor
 * (selection -1) it goes at the end.
 */
fun insertDictation(field: FieldText, dictated: String): Insertion {
    val text = field.text
    val hasCursor = field.selectionStart >= 0 && field.selectionEnd >= 0
    val start = if (hasCursor) minOf(field.selectionStart, field.selectionEnd).coerceIn(0, text.length) else text.length
    val end = if (hasCursor) maxOf(field.selectionStart, field.selectionEnd).coerceIn(start, text.length) else text.length
    val before = text.substring(0, start)
    val after = text.substring(end)
    var insert = dictated.trim()
    if (before.isNotEmpty() && !before.last().isWhitespace()) insert = " $insert"
    if (after.isNotEmpty() && !after.first().isWhitespace()) insert = "$insert "
    return Insertion(before + insert + after, start, start + insert.length)
}

/**
 * The field's real text. Empty fields often report their placeholder as text:
 * some say so with isShowingHintText, others only show it by text == hint.
 * Telegram and WhatsApp draw their own placeholder and report it as plain text,
 * but Android only gives a field a cursor and the set-selection action when it
 * really has text, so text without either (hasCursor false) is a placeholder.
 */
fun realFieldText(text: CharSequence?, hint: CharSequence?, showingHint: Boolean, hasCursor: Boolean): String {
    val shown = text?.toString().orEmpty()
    if (showingHint || !hasCursor) return ""
    if (hint != null && shown == hint.toString()) return ""
    return shown
}

/**
 * Follows a field after a dictation went in, to see how the user corrected it
 * before sending. [original] is the dictated text.
 */
class EditWatch(val original: String, private val inserted: Insertion) {
    private val before = inserted.text.substring(0, inserted.start)
    private val after = inserted.text.substring(inserted.cursor)
    private var latest = inserted.text

    /**
     * Takes the field's text now. An empty field was sent or cleared, which ends
     * the watch: that returns false, and the text before it is kept.
     */
    fun update(text: String): Boolean {
        if (text.isEmpty()) return false
        latest = text
        return true
    }

    /**
     * The dictation as the user left it, or null if they didn't change the field.
     * While the text around it is unchanged that's cut off, so only the dictated
     * part is compared; otherwise it's the whole field.
     */
    fun edited(): String? {
        if (latest == inserted.text) return null
        val aroundIntact = latest.length >= before.length + after.length &&
            latest.startsWith(before) && latest.endsWith(after)
        return if (aroundIntact) latest.substring(before.length, latest.length - after.length).trim() else latest
    }
}
