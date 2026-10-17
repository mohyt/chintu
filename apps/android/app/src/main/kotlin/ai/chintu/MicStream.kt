package ai.chintu

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.ParcelFileDescriptor
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Records the microphone and streams 16 kHz mono PCM into a pipe that the speech recogniser reads
 * (RecognizerIntent.EXTRA_AUDIO_SOURCE). Chintu, not the recogniser, decides when an instruction ends:
 * the pipe closes after [silenceMs] of quiet once speech has started, after [noSpeechMs] if nobody
 * speaks, or after [maxMs]. Closing the pipe ends the recogniser's segmented session.
 *
 * Speech is detected from the signal level against a noise floor learned in the first frames;
 * [heardWords] lets the recogniser's live transcript also count as speech.
 */
class MicStream(
    private val source: Int = MediaRecorder.AudioSource.VOICE_RECOGNITION,
    private val silenceMs: Long = 2000L,
    private val noSpeechMs: Long = 6000L,
    private val maxMs: Long = 60_000L,
    private val level: (Float) -> Unit = {},
    private val ended: (Reason) -> Unit = {},
) {
    enum class Reason { SILENCE, NO_SPEECH, MAX_LENGTH, STOPPED, FAILED }

    companion object {
        const val SAMPLE_RATE = 16000
        private const val FRAME = 320                 // 20 ms
        private const val CALIBRATION_FRAMES = 10     // first 200 ms set the noise floor
        private const val MIN_SPEECH_RMS = 350.0      // absolute floor so a silent room is not "speech"
    }

    /** The recogniser's end of the pipe; pass it as EXTRA_AUDIO_SOURCE, then call [start]. */
    val readSide: ParcelFileDescriptor
    private val writeSide: ParcelFileDescriptor
    @Volatile private var running = false
    @Volatile private var lastWordsAt = 0L
    private var thread: Thread? = null

    init {
        val pipe = ParcelFileDescriptor.createPipe()
        readSide = pipe[0]; writeSide = pipe[1]
    }

    /** Called when the recogniser's live transcript changes: words count as speech even when quiet. */
    fun heardWords() { lastWordsAt = System.currentTimeMillis() }

    @SuppressLint("MissingPermission")  // callers hold RECORD_AUDIO before listening
    fun start(): Boolean {
        val minimum = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val record = try {
            AudioRecord(source, SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(minimum, FRAME * 2 * 8))
        } catch (_: Exception) { null }
        if (record == null || record.state != AudioRecord.STATE_INITIALIZED) { record?.release(); close(); return false }
        running = true
        thread = Thread({ pump(record) }, "chintu-mic").apply { start() }
        return true
    }

    private fun pump(record: AudioRecord) {
        var reason = Reason.STOPPED
        // Owns writeSide: closing the stream closes the pipe exactly once.
        val out = ParcelFileDescriptor.AutoCloseOutputStream(writeSide)
        val samples = ShortArray(FRAME)
        val bytes = ByteArray(FRAME * 2)
        val started = System.currentTimeMillis()
        var noise = 0.0; var calibrated = 0
        var speaking = false; var lastSpeech = started
        try {
            record.startRecording()
            while (running) {
                val read = record.read(samples, 0, FRAME)
                if (read <= 0) { reason = Reason.FAILED; break }
                var sum = 0.0
                for (i in 0 until read) {
                    val s = samples[i].toInt(); sum += (s * s).toDouble()
                    bytes[2 * i] = (s and 0xff).toByte(); bytes[2 * i + 1] = (s shr 8 and 0xff).toByte()
                }
                out.write(bytes, 0, read * 2)
                val rms = sqrt(sum / read)
                level(((rms / 3000.0).coerceIn(0.0, 1.0)).toFloat())
                val now = System.currentTimeMillis()
                if (calibrated < CALIBRATION_FRAMES) { noise += rms / CALIBRATION_FRAMES; calibrated++; continue }
                val loud = rms > max(MIN_SPEECH_RMS, noise * 3.0)
                if (loud || now - lastWordsAt < 300) { speaking = true; lastSpeech = now }
                when {
                    speaking && now - max(lastSpeech, lastWordsAt) >= silenceMs -> { reason = Reason.SILENCE; break }
                    !speaking && now - started >= noSpeechMs -> { reason = Reason.NO_SPEECH; break }
                    now - started >= maxMs -> { reason = Reason.MAX_LENGTH; break }
                }
            }
        } catch (_: Exception) {
            reason = if (running) Reason.FAILED else Reason.STOPPED
        } finally {
            try { record.stop() } catch (_: Exception) { }
            record.release()
            try { out.close() } catch (_: Exception) { }
            val wasRunning = running
            running = false
            ended(if (wasRunning || reason != Reason.STOPPED) reason else Reason.STOPPED)
        }
    }

    /** Stops recording; the recogniser sees the end of the stream and finishes the session. */
    fun stop() { running = false }

    /** Only for the failure path in [start], before the stream has taken ownership of the pipe. */
    private fun close() {
        try { writeSide.close() } catch (_: Exception) { }
        try { readSide.close() } catch (_: Exception) { }
    }

    /** The recogniser holds its own copy once listening started; drop ours when the session is over. */
    fun releaseReadSide() { try { readSide.close() } catch (_: Exception) { } }
}
