package ai.chintu

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.Settings
import ai.chintu.core.WakePhrase
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService
import org.vosk.android.StorageService

/**
 * Listens for "Chintu" on the device with Vosk and hands the conversation to MainActivity.
 *
 * Audio never leaves the phone here: the recogniser only knows the wake phrase and a few decoys
 * (WakePhrase.GRAMMAR). On a match it chimes, releases the microphone and either calls the
 * activity (when it is on screen) or brings the activity up, over the lock screen if needed;
 * that needs "Display over other apps", otherwise a tap-to-talk notification is posted.
 * The activity holds the service while it listens or speaks, and releases it when idle.
 *
 * Same rule as TaskWatchService: every command reaches startForeground() first, and stop() sends
 * a queued ACTION_STOP, because stopping before startForeground() makes Android kill the app.
 */
class WakeWordService : Service() {
    companion object {
        const val ACTION_WAKE = "ai.chintu.action.WAKE"
        const val PREF_ENABLED = "wake_word"
        private const val ACTION_STOP = "ai.chintu.action.STOP_WAKE"
        private const val CHANNEL = "chintu-wake"
        private const val CHANNEL_TAP = "chintu-wake-tap"
        private const val NOTIFICATION_ID = 42
        private const val TAP_ID = 43
        private const val SAMPLE_RATE = 16000f
        private const val COOLDOWN_MS = 2500L
        private const val RELEASE_UNCLAIMED_MS = 20_000L

        @Volatile var running = false; private set
        /** Set by MainActivity while it is on screen; called on the main thread instead of launching the activity. */
        @Volatile var onWake: (() -> Unit)? = null
        private var instance: WakeWordService? = null

        fun start(context: Context) {
            running = true
            context.startForegroundService(Intent(context, WakeWordService::class.java))
        }
        fun stop(context: Context) {
            if (running) context.startService(Intent(context, WakeWordService::class.java).setAction(ACTION_STOP))
        }
        /** MainActivity owns the microphone while it listens or speaks; call on the main thread. */
        fun hold(held: Boolean) { instance?.setHeld(held) }
    }

    private val handler = Handler(Looper.getMainLooper())
    private var model: Model? = null
    private var recognizer: Recognizer? = null
    private var speechService: SpeechService? = null
    private var loading = false
    private var held = false
    private var lastWake = 0L
    private val resume = Runnable { listen() }
    private val releaseUnclaimed = Runnable { setHeld(false) }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() { super.onCreate(); instance = this }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        channels()
        try {
            startForeground(NOTIFICATION_ID, listening(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } catch (_: Exception) {
            // Android refuses a microphone service started from the background; the activity starts it on its next visit.
            running = false; stopSelf(); return START_NOT_STICKY
        }
        if (intent?.action == ACTION_STOP) {
            getSharedPreferences("chintu-ui", MODE_PRIVATE).edit().putBoolean(PREF_ENABLED, false).apply()
            stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); return START_NOT_STICKY
        }
        load()
        return START_STICKY
    }

    private fun load() {
        if (model != null) { listen(); return }
        if (loading) return
        loading = true
        // Copies the model from the APK assets to app storage once per model version (its uuid file).
        StorageService.unpack(this, "wake-model", "wake-model", { unpacked ->
            loading = false; model = unpacked; listen()
        }, { error ->
            loading = false
            android.util.Log.w("Chintu", "wake model unavailable", error)
            notify(NOTIFICATION_ID, listening("The wake word could not start: ${error.message ?: "model missing"}."))
        })
    }

    private fun listen() {
        val loaded = model ?: return
        if (held || speechService != null) return
        try {
            val next = Recognizer(loaded, SAMPLE_RATE, WakePhrase.GRAMMAR)
            recognizer = next
            speechService = SpeechService(next, SAMPLE_RATE).also { it.startListening(listener) }
        } catch (error: Exception) {
            android.util.Log.w("Chintu", "wake listener failed", error)
            releaseMicrophone(); handler.postDelayed(resume, 3000)
        }
    }

    private fun releaseMicrophone() {
        handler.removeCallbacks(resume)
        speechService?.let { try { it.stop(); it.shutdown() } catch (_: Exception) { } }
        speechService = null
        recognizer?.let { try { it.close() } catch (_: Exception) { } }
        recognizer = null
    }

    private fun setHeld(value: Boolean) {
        handler.removeCallbacks(releaseUnclaimed)
        if (value == held) return
        held = value
        if (value) releaseMicrophone()
        else { handler.removeCallbacks(resume); handler.postDelayed(resume, 400) }
    }

    private val listener = object : RecognitionListener {
        override fun onResult(hypothesis: String?) { check(hypothesis, "text") }
        override fun onFinalResult(hypothesis: String?) { check(hypothesis, "text") }
        override fun onPartialResult(hypothesis: String?) {}
        override fun onError(error: Exception?) {
            android.util.Log.w("Chintu", "wake listener error", error)
            releaseMicrophone(); handler.postDelayed(resume, 2000)
        }
        override fun onTimeout() {}
        private fun check(hypothesis: String?, key: String) {
            val text = try { JSONObject(hypothesis ?: "{}").optString(key) } catch (_: Exception) { "" }
            if (!WakePhrase.matches(text)) return
            val now = System.currentTimeMillis()
            if (now - lastWake < COOLDOWN_MS) return
            lastWake = now
            handler.post { woke() }
        }
    }

    private fun woke() {
        held = true; releaseMicrophone()
        try { ToneGenerator(AudioManager.STREAM_MUSIC, 70).startTone(ToneGenerator.TONE_PROP_ACK, 160) } catch (_: Exception) { }
        // If no conversation claims the microphone, go back to listening rather than staying deaf.
        handler.postDelayed(releaseUnclaimed, RELEASE_UNCLAIMED_MS)
        val callback = onWake
        if (callback != null) { callback(); return }
        val open = Intent(this, MainActivity::class.java).setAction(ACTION_WAKE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
        if (Settings.canDrawOverlays(this)) {
            try { startActivity(open); return } catch (_: Exception) { }
        }
        // Without "Display over other apps" Android blocks opening the app from here; offer a one-tap notification.
        val tap = PendingIntent.getActivity(this, 1, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        notify(TAP_ID, Notification.Builder(this, CHANNEL_TAP).setSmallIcon(R.drawable.chintu_icon)
            .setContentTitle("Chintu heard you").setContentText("Tap to talk. Allow \"Display over other apps\" to skip this tap.")
            .setContentIntent(tap).setAutoCancel(true).setCategory(Notification.CATEGORY_CALL).build())
    }

    private fun channels() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Wake word", NotificationManager.IMPORTANCE_LOW)
            .apply { description = "Shown while Chintu listens for its name" })
        manager.createNotificationChannel(NotificationChannel(CHANNEL_TAP, "Wake word heard", NotificationManager.IMPORTANCE_HIGH)
            .apply { description = "Tap to talk after saying Chintu" })
    }

    private fun listening(text: String = "Say \"Chintu\" to talk. Listening happens on this phone only."): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val off = PendingIntent.getService(this, 2, Intent(this, WakeWordService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL).setSmallIcon(R.drawable.chintu_icon)
            .setContentTitle("Chintu is listening for its name").setContentText(text)
            .setOngoing(true).setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Turn off", off).build()).build()
    }

    private fun notify(id: Int, notification: Notification) {
        try { getSystemService(NotificationManager::class.java).notify(id, notification) } catch (_: SecurityException) { }
    }

    override fun onDestroy() {
        running = false; instance = null
        handler.removeCallbacksAndMessages(null); releaseMicrophone()
        model?.let { try { it.close() } catch (_: Exception) { } }; model = null
        super.onDestroy()
    }
}
