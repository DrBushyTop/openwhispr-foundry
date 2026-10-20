package net.huuhka.whisprmic

import org.junit.Assert.assertEquals
import org.junit.Test

class DictationClientTest {
    @Test
    fun keepsTheMessageFromOpenWhisprsWrapper() {
        assertEquals(
            "transcription failed: Azure didn't answer",
            readableError("""API Error: 502 {"error": "transcription failed: Azure didn't answer"}"""),
        )
    }

    @Test
    fun readsANestedMessage() {
        assertEquals("Rate limit hit", readableError("""API Error: 429 {"error": {"message": "Rate limit hit", "code": "429"}}"""))
    }

    @Test
    fun leavesPlainTextAlone() {
        assertEquals("OpenWhispr's dictation window isn't running", readableError(" OpenWhispr's dictation window isn't running "))
        assertEquals("API Error: 500 {not json", readableError("API Error: 500 {not json"))
    }
}
