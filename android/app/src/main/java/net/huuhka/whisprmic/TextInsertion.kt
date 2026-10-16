package net.huuhka.whisprmic

/** A text field's content and selection, as accessibility reports them. */
data class FieldText(val text: String, val selectionStart: Int, val selectionEnd: Int)

data class Insertion(val text: String, val cursor: Int)

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
    return Insertion(before + insert + after, start + insert.length)
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
