package net.huuhka.whisprmic

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class TextInsertionTest {
    @Test
    fun emptyField() {
        assertEquals(Insertion("Hello there.", 12), insertDictation(FieldText("", 0, 0), " Hello there. "))
    }

    @Test
    fun appendsWithSpaceAfterAWord() {
        assertEquals(Insertion("Fix the bug please", 18), insertDictation(FieldText("Fix the bug", 11, 11), "please"))
    }

    @Test
    fun noExtraSpaceAfterWhitespace() {
        assertEquals(Insertion("Fix it", 6), insertDictation(FieldText("Fix ", 4, 4), "it"))
    }

    @Test
    fun insertsInTheMiddleWithSpacesOnBothSides() {
        assertEquals(Insertion("one two three", 8), insertDictation(FieldText("onethree", 3, 3), "two"))
    }

    @Test
    fun replacesSelection() {
        assertEquals(Insertion("keep new keep", 8), insertDictation(FieldText("keep old keep", 8, 5), "new"))
    }

    @Test
    fun noCursorAppendsAtEnd() {
        assertEquals(Insertion("start end", 9), insertDictation(FieldText("start", -1, -1), "end"))
    }

    @Test
    fun cursorPastTheEndIsClamped() {
        assertEquals(Insertion("ab cd", 5), insertDictation(FieldText("ab", 10, 10), "cd"))
    }

    @Test
    fun placeholderIsNotText() {
        assertEquals("", realFieldText("Message", "Message", showingHint = false, hasCursor = true))
        assertEquals("", realFieldText("Message", null, showingHint = true, hasCursor = true))
        // Telegram and WhatsApp: placeholder as plain text, no hint, no cursor.
        assertEquals("", realFieldText("Message", null, showingHint = false, hasCursor = false))
        assertEquals("Real", realFieldText("Real", "Message", showingHint = false, hasCursor = true))
        assertEquals("", realFieldText(null, null, showingHint = false, hasCursor = true))
    }

    @Test
    fun wavHeader() {
        val header = WavRecorder.wavHeader(32_000)
        assertEquals(44, header.size)
        assertArrayEquals("RIFF".toByteArray(), header.copyOfRange(0, 4))
        assertArrayEquals("data".toByteArray(), header.copyOfRange(36, 40))
        // Data size, little-endian, at offset 40.
        assertArrayEquals(byteArrayOf(0x00, 0x7D, 0x00, 0x00), header.copyOfRange(40, 44))
    }
}
