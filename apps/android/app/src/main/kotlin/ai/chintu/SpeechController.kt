package ai.chintu

import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale

enum class VoicePhase { IDLE, LISTENING, THINKING, SPEAKING }

/** Local Android speech with lifecycle/generation guards; the UI explicitly opts into foreground sessions. */
class SpeechController(private val context: Context, private val status: (String) -> Unit,
    private val finalText: (String) -> Unit, private val phaseChanged: (VoicePhase) -> Unit,
    private val meter: (Float) -> Unit, private val idle: () -> Unit,
    private val recognitionError: (Boolean) -> Unit) {
    private val handler = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    var isListening = false; private set
    var isSpeaking = false; private set
    /** Last SpeechRecognizer error code, so callers can back off harder on BUSY/CLIENT than on silence. */
    var lastError = 0; private set
    private var recognizerOnDevice: Boolean? = null
    /** Note mode taps the live hypothesis so text can be flushed on a fixed cadence, not only at end of speech. */
    var onPartial: ((String) -> Unit)? = null
    var onSessionStart: (() -> Unit)? = null
    /**
     * Android 13+: instructions are streamed from Chintu's own microphone reader (MicStream) into a segmented
     * session, so Chintu decides when the speaker has finished (2 s of quiet) instead of the recogniser, which
     * ends at a ~1 s pause and ignores the silence, minimum-length and plain segmented-session extras.
     */
    var streamInstructions = android.os.Build.VERSION.SDK_INT >= 33
    private var mic: MicStream? = null
    private val segments = StringBuilder()
    private var generation = 0
    private var utterance = 0
    private var closed = false
    init {
        tts = TextToSpeech(context) { result -> handler.post {
            if (!closed) {
                ttsReady = result == TextToSpeech.SUCCESS
                if (!ttsReady) status("Speech playback is unavailable. You can still read replies.")
                else tts?.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            }
        } }
        tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String?) { handler.post {
                if (!closed && id == "chintu-$utterance") { isSpeaking = true; phaseChanged(VoicePhase.SPEAKING) }
            } }
            override fun onDone(id: String?) = finished(id)
            @Deprecated("Deprecated in Java") override fun onError(id: String?) = finished(id)
            override fun onError(id: String?, errorCode: Int) = finished(id)
            private fun finished(id: String?) { handler.post {
                if (!closed && id == "chintu-$utterance") { isSpeaking = false; phaseChanged(VoicePhase.IDLE); idle() }
            } }
        })
    }
    /** [dictation] keeps a session open across long pauses so note mode restarts (and the service's tones) are rare. */
    fun listen(onlineOptIn: Boolean, language: String, dictation: Boolean = false) {
        stopSpeaking()
        val session = ++generation
        isListening = false
        stopMic(); segments.setLength(0)
        try {
            val onDevice = SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
            // Reuse the bound recogniser between sessions: destroying and recreating it on every phrase makes
            // the service report BUSY/CLIENT errors while the previous instance is still shutting down.
            if (recognizer != null && recognizerOnDevice != onDevice) { try { recognizer?.destroy() } catch (_: Exception) { }; recognizer = null }
            recognizer?.cancel()
            if (!onDevice && !onlineOptIn) {
                status("Install on-device speech, or allow your Android speech service in Settings.")
                recognitionError(false); return
            }
            if (!onDevice && !SpeechRecognizer.isRecognitionAvailable(context)) {
                status("No speech recognizer is installed. You can type instead."); recognitionError(false); return
            }
            val service = recognizer ?: (if (onDevice) SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
                else SpeechRecognizer.createSpeechRecognizer(context)).also { recognizer = it; recognizerOnDevice = onDevice }
            service.setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) {
                    if (session != generation) return
                    isListening = true; phaseChanged(VoicePhase.LISTENING); status("I'm listening.")
                    onSessionStart?.invoke()
                }
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) { if (session == generation && mic == null) meter(((rmsdB + 2f) / 12f).coerceIn(0f, 1f)) }
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() { if (session == generation && mic == null) { phaseChanged(VoicePhase.THINKING); status("One moment…") } }
                override fun onError(error: Int) {
                    if (session != generation) return
                    generation++; isListening = false; phaseChanged(VoicePhase.IDLE); lastError = error
                    val streamed = mic != null
                    stopMic()
                    val partial = segments.toString().trim()
                    if (streamed && partial.isNotEmpty()) { finalText(partial); return }  // keep what was already recognised
                    if (error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY || error == SpeechRecognizer.ERROR_CLIENT ||
                        error == SpeechRecognizer.ERROR_SERVER_DISCONNECTED) {
                        // The bound service is wedged; drop it so the next attempt binds a fresh one after a pause.
                        try { recognizer?.destroy() } catch (_: Exception) { }
                        recognizer = null
                    }
                    val retryable = error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT ||
                        error == SpeechRecognizer.ERROR_SERVER_DISCONNECTED || error == SpeechRecognizer.ERROR_CLIENT ||
                        error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY
                    val reason = when (error) {
                        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone permission is needed."
                        SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "Ready when you are."
                        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Speech recognition is busy. Trying again…"
                        SpeechRecognizer.ERROR_SERVER_DISCONNECTED, SpeechRecognizer.ERROR_CLIENT -> "The speech service blinked. Trying again…"
                        SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED -> "That speech language isn't supported."
                        SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> "Download this language's speech model in Android settings."
                        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "The speech service couldn't connect."
                        else -> "Speech recognition stopped ($error). Tap to try again."
                    }
                    status(reason); recognitionError(retryable)
                }
                override fun onResults(results: Bundle?) {
                    if (session != generation) return
                    generation++; isListening = false; phaseChanged(VoicePhase.IDLE); lastError = 0
                    val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                    if (text.isNullOrBlank()) { status("I didn't catch that."); recognitionError(true) }
                    else finalText(text)
                }
                override fun onPartialResults(partialResults: Bundle?) {
                    if (session != generation) return
                    val partial = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                    if (!partial.isNullOrBlank()) {
                        mic?.heardWords()
                        val sofar = (segments.toString() + " " + partial).trim()
                        status("Hearing: $sofar"); onPartial?.invoke(partial)
                    }
                }
                override fun onEvent(eventType: Int, params: Bundle?) {}
                override fun onSegmentResults(segmentResults: Bundle) {
                    if (session != generation) return
                    val text = segmentResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                    if (!text.isNullOrBlank()) { segments.append(' ').append(text.trim()); mic?.heardWords() }
                }
                override fun onEndOfSegmentedSession() {
                    if (session != generation) return
                    generation++; isListening = false; phaseChanged(VoicePhase.IDLE); lastError = 0
                    stopMic()
                    val text = segments.toString().trim()
                    if (text.isEmpty()) { status("I didn't catch that."); recognitionError(true) } else finalText(text)
                }
            })
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, onDevice)
            if (dictation) intent
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 12000L)
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 10000L)
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 120000L)
            val stream = if (!dictation && streamInstructions) MicStream(source = micSource(),
                level = { value -> handler.post { if (session == generation) meter(value) } },
                ended = { reason -> handler.post {
                    if (session == generation && reason != MicStream.Reason.STOPPED) { phaseChanged(VoicePhase.THINKING); status("One moment…") }
                } }) else null
            if (stream != null) intent
                .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, stream.readSide)
                .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
                .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, android.media.AudioFormat.ENCODING_PCM_16BIT)
                .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, MicStream.SAMPLE_RATE)
                .putExtra(RecognizerIntent.EXTRA_SEGMENTED_SESSION, RecognizerIntent.EXTRA_AUDIO_SOURCE)
            isListening = true; phaseChanged(VoicePhase.LISTENING); status("Opening the microphone…")
            service.startListening(intent)
            if (stream != null) {
                mic = stream
                if (!stream.start()) { mic = null; streamInstructions = false; service.cancel(); listen(onlineOptIn, language, dictation); return }
            }
        } catch (_: Exception) {
            generation++; isListening = false; phaseChanged(VoicePhase.IDLE)
            status("Couldn't start speech recognition. You can type instead."); recognitionError(false)
        }
    }
    /** Headset or phone microphone: a Bluetooth communication device is reached through VOICE_COMMUNICATION. */
    private fun micSource(): Int {
        val manager = context.getSystemService(android.media.AudioManager::class.java)
        val type = manager?.communicationDevice?.type
        val headset = type == android.media.AudioDeviceInfo.TYPE_BLUETOOTH_SCO || type == android.media.AudioDeviceInfo.TYPE_BLE_HEADSET
        return if (headset) android.media.MediaRecorder.AudioSource.VOICE_COMMUNICATION else android.media.MediaRecorder.AudioSource.VOICE_RECOGNITION
    }
    private fun stopMic() {
        val current = mic ?: return
        mic = null
        current.stop()
        handler.postDelayed({ current.releaseReadSide() }, 1000)
    }
    fun finish() {
        // Streaming: ending the audio ends the session and keeps every segment heard so far.
        mic?.let { it.stop(); return }
        try { recognizer?.stopListening() }
        catch (_: Exception) { pause(); status("Speech recognition stopped. Try again."); recognitionError(false) }
    }
    fun speak(text: String, languageTag: String): Boolean {
        if (!ttsReady || text.isBlank()) { status("Speech playback isn't ready yet."); return false }
        cancelRecognition()
        val engine = tts ?: return false
        val locale = Locale.forLanguageTag(languageTag.trim())
        val voices = engine.voices.orEmpty().filter { !it.isNetworkConnectionRequired && it.locale.language == locale.language }
        val offline = voices.firstOrNull { it.locale == locale } ?: voices.firstOrNull()
        if (offline == null || engine.setVoice(offline) == TextToSpeech.ERROR) {
            status("Install an offline voice for $languageTag in Android speech settings."); return false
        }
        utterance++; isSpeaking = true; phaseChanged(VoicePhase.SPEAKING)
        val result = engine.speak(text.take(2000), TextToSpeech.QUEUE_FLUSH, null, "chintu-$utterance")
        if (result == TextToSpeech.ERROR) { isSpeaking = false; phaseChanged(VoicePhase.IDLE); status("Couldn't play that reply."); return false }
        return true
    }
    fun stopSpeaking() {
        utterance++; tts?.stop(); isSpeaking = false
        if (!isListening) phaseChanged(VoicePhase.IDLE)
    }
    fun cancelRecognition() {
        generation++; isListening = false; stopMic()
        try { recognizer?.cancel() } catch (_: Exception) { }
        if (!isSpeaking) phaseChanged(VoicePhase.IDLE)
    }
    fun pause() { cancelRecognition(); stopSpeaking() }
    fun close() { closed = true; pause(); recognizer?.destroy(); tts?.shutdown(); handler.removeCallbacksAndMessages(null) }
}
