package net.huuhka.whisprmic

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.abs
import kotlin.math.max

/** Records 16 kHz mono 16-bit PCM into a WAV file on a background thread. */
class WavRecorder(private val file: File) {
    private var record: AudioRecord? = null
    private var thread: Thread? = null
    @Volatile private var running = false
    @Volatile private var dataBytes = 0L
    @Volatile private var peak = 0

    @SuppressLint("MissingPermission") // RecorderService checks it before starting.
    fun start() {
        val minimum = AudioRecord.getMinBufferSize(RATE, CHANNEL, ENCODING)
        val audio = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, CHANNEL, ENCODING, max(minimum, CHUNK_BYTES) * 2)
        if (audio.state != AudioRecord.STATE_INITIALIZED) {
            audio.release()
            error("The microphone couldn't be opened")
        }
        audio.startRecording()
        if (audio.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            audio.release()
            error("The microphone is in use by another app")
        }
        record = audio
        running = true
        thread = Thread({ writeLoop(audio) }, "wav-recorder").apply { start() }
    }

    /** Stops recording and finishes the file. */
    fun stop(): Recording {
        running = false
        runCatching { record?.stop() }
        thread?.join()
        record?.release()
        record = null
        return Recording(file, dataBytes * 1000 / (RATE * 2), peak)
    }

    private fun writeLoop(audio: AudioRecord) {
        RandomAccessFile(file, "rw").use { out ->
            out.setLength(0)
            out.write(ByteArray(HEADER_BYTES))
            val buffer = ByteArray(CHUNK_BYTES)
            while (running && dataBytes < MAX_DATA_BYTES) {
                val count = audio.read(buffer, 0, buffer.size)
                if (count <= 0) {
                    if (count < 0) break
                    continue
                }
                out.write(buffer, 0, count)
                dataBytes += count
                peak = max(peak, peakOf(buffer, count))
            }
            out.seek(0)
            out.write(wavHeader(dataBytes.toInt()))
        }
    }

    data class Recording(val file: File, val durationMs: Long, val peak: Int)

    companion object {
        const val RATE = 16_000
        private const val CHANNEL = AudioFormat.CHANNEL_IN_MONO
        private const val ENCODING = AudioFormat.ENCODING_PCM_16BIT
        private const val CHUNK_BYTES = RATE / 10 * 2 // 100 ms
        private const val HEADER_BYTES = 44
        /** Ten minutes; OpenWhispr's server takes up to 25 MB. */
        private const val MAX_DATA_BYTES = 10L * 60 * RATE * 2

        private fun peakOf(buffer: ByteArray, count: Int): Int {
            var peak = 0
            var i = 0
            while (i + 1 < count) {
                val sample = (buffer[i].toInt() and 0xFF) or (buffer[i + 1].toInt() shl 8)
                peak = max(peak, abs(sample.toShort().toInt()))
                i += 2
            }
            return peak
        }

        fun wavHeader(dataBytes: Int): ByteArray = ByteBuffer.allocate(HEADER_BYTES)
            .order(ByteOrder.LITTLE_ENDIAN)
            .put("RIFF".toByteArray()).putInt(36 + dataBytes)
            .put("WAVE".toByteArray())
            .put("fmt ".toByteArray()).putInt(16)
            .putShort(1) // PCM
            .putShort(1) // mono
            .putInt(RATE)
            .putInt(RATE * 2) // byte rate
            .putShort(2) // block align
            .putShort(16) // bits per sample
            .put("data".toByteArray()).putInt(dataBytes)
            .array()
    }
}
