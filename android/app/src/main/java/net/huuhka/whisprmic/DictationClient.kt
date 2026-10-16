package net.huuhka.whisprmic

import java.io.File
import java.io.IOException
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import org.json.JSONObject

data class DictationResult(val text: String, val cleanupFailed: Boolean)

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
