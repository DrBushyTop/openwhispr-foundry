package net.huuhka.whisprmic

import java.io.File
import java.io.IOException
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
 * Talks to OpenWhispr Patched's mobile dictation endpoint (see
 * openwhispr-patch/mobile-dictation.patch). Blocking; call off the main thread.
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
        return DictationResult(json.optString("text"), json.optBoolean("cleanupFailed"))
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
            val body = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (status == 401) throw IOException("The token was refused. Check it in Whispr Mic")
            // Builds before the dictionary endpoints answer those with 404.
            if (status == 404) throw IOException("OpenWhispr Patched on the Mac needs updating for this")
            if (status !in 200..299) {
                val message = runCatching { JSONObject(body).getString("error") }.getOrNull()
                throw IOException(message ?: "Server answered $status")
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
}
