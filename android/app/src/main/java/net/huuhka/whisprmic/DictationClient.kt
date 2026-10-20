package net.huuhka.whisprmic

import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import org.json.JSONArray
import org.json.JSONObject

data class DictationResult(val text: String, val cleanupFailed: Boolean)

data class DictionaryWord(val word: String, val learned: Boolean)

/** OpenWhispr's dictionary, oldest first, and whether its auto-learn setting is on. */
data class Dictionary(val words: List<DictionaryWord>, val autoLearn: Boolean)

/**
 * OpenWhispr passes a transcription service's error on as it came, like
 * `API Error: 502 {"error": "transcription failed: Azure didn't answer"}`.
 * This keeps the message inside the JSON, so it fits in a toast.
 */
internal fun readableError(error: String): String {
    val start = error.indexOf('{')
    val json = if (start < 0) null else runCatching { JSONObject(error.substring(start)) }.getOrNull()
    val inner = json?.optJSONObject("error")?.optString("message")
        ?: json?.optString("error")?.ifEmpty { null }
        ?: json?.optString("message")
    return inner?.trim()?.ifEmpty { null } ?: error.trim()
}

/**
 * Talks to OpenWhispr Patched's mobile dictation endpoint (see
 * openwhispr-patch/mobile-dictation.patch). Blocking; call off the main thread.
 * Answers are untrusted: their size is capped, and a dictation's text too.
 */
class DictationClient(private val baseUrl: String, private val token: String) {

    fun dictate(wav: File): DictationResult {
        val connection = open("/v1/dictate", readTimeoutMs = 130_000)
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "audio/wav")
        connection.setFixedLengthStreamingMode(wav.length())
        val body = request(connection) {
            connection.outputStream.use { out -> wav.inputStream().use { it.copyTo(out) } }
        }
        val json = JSONObject(body)
        val text = json.optString("text")
        if (text.length > MAX_DICTATION_CHARS) throw IOException("The server sent back far more text than a dictation has")
        return DictationResult(text, json.optBoolean("cleanupFailed"))
    }

    /** OpenWhispr's settings summary, also a check that the URL and token work. */
    fun health(): JSONObject = JSONObject(request(open("/v1/health", readTimeoutMs = 15_000)) {})

    fun dictionary(): Dictionary = parseDictionary(request(open("/v1/dictionary", readTimeoutMs = 15_000)) {})

    /** Adds and removes words in one go, so an edit is a remove plus an add. */
    fun changeDictionary(add: List<String> = emptyList(), remove: List<String> = emptyList()): Dictionary =
        parseDictionary(postJson("/v1/dictionary", JSONObject().put("add", JSONArray(add)).put("remove", JSONArray(remove))))

    /**
     * Sends a dictation and the field's text after the user's edits. OpenWhispr
     * adds the words they corrected to the dictionary and returns them.
     */
    fun learn(original: String, edited: String): List<String> {
        val json = JSONObject(postJson("/v1/learn", JSONObject().put("original", original).put("edited", edited)))
        val words = json.optJSONArray("words") ?: return emptyList()
        return (0 until words.length()).map { words.getString(it) }
    }

    private fun postJson(path: String, json: JSONObject): String {
        val connection = open(path, readTimeoutMs = 15_000)
        val bytes = json.toString().toByteArray()
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setFixedLengthStreamingMode(bytes.size)
        return request(connection) { connection.outputStream.use { it.write(bytes) } }
    }

    private fun parseDictionary(body: String): Dictionary {
        val json = JSONObject(body)
        val words = json.optJSONArray("words") ?: JSONArray()
        return Dictionary(
            (0 until words.length()).map { i ->
                val word = words.getJSONObject(i)
                DictionaryWord(word.getString("word"), word.optBoolean("learned"))
            },
            json.optBoolean("autoLearn", true),
        )
    }

    private fun open(path: String, readTimeoutMs: Int): HttpURLConnection {
        val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
        connection.connectTimeout = 10_000
        connection.readTimeout = readTimeoutMs
        connection.setRequestProperty("Authorization", "Bearer $token")
        return connection
    }

    private fun request(connection: HttpURLConnection, send: () -> Unit): String {
        try {
            send()
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.use(::readCapped).orEmpty()
            if (status == 401) throw IOException("The token was refused. Check it in Whispr Mic")
            // Builds before the dictionary endpoints answer those with 404.
            if (status == 404) throw IOException("OpenWhispr Patched on the Mac needs updating for this")
            if (status !in 200..299) {
                val message = runCatching { JSONObject(body).getString("error") }.getOrNull()
                throw IOException(message?.let(::readableError) ?: "Server answered $status")
            }
            return body
        } catch (e: UnknownHostException) {
            throw IOException("Can't find ${connection.url.host}. Is Tailscale on?", e)
        } catch (e: ConnectException) {
            throw IOException("Can't connect to ${connection.url.host}. Is Tailscale on?", e)
        } catch (e: SocketTimeoutException) {
            throw IOException("The server took too long to answer", e)
        } finally {
            connection.disconnect()
        }
    }

    private fun readCapped(stream: InputStream): String {
        val out = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (true) {
            val count = stream.read(buffer)
            if (count < 0) break
            out.write(buffer, 0, count)
            if (out.size() > MAX_RESPONSE_BYTES) throw IOException("The server's answer was too large")
        }
        return out.toString(Charsets.UTF_8.name())
    }

    companion object {
        /** Far above any real answer; even a long dictionary comes to a few KB. */
        private const val MAX_RESPONSE_BYTES = 256 * 1024
        /** A ten-minute recording, the longest there is, comes to some 10,000 characters. */
        private const val MAX_DICTATION_CHARS = 30_000
    }
}
