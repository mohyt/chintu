package ai.chintu

import ai.chintu.core.SseParser
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.URI
import javax.net.ssl.HttpsURLConnection

/**
 * Server-sent events from one gateway, on its own thread: each stored event arrives the moment it commits.
 * Reconnects with capped, jittered backoff and resumes from the last event ID it delivered. The stream only
 * speeds things up; polling with the persisted cursor stays the source of truth, so a lost stream loses nothing.
 */
class EventStream(
    base: String,
    private val token: String,
    startCursor: Long,
    private val onEvent: (JSONObject) -> Unit,
    private val onState: (State) -> Unit,
) {
    /** UNSUPPORTED and UNAUTHORIZED are final for this stream: the caller keeps polling. */
    enum class State { CONNECTING, LIVE, RETRYING, UNSUPPORTED, UNAUTHORIZED, STOPPED }
    private enum class Outcome { RECYCLED, FAILED, UNSUPPORTED, UNAUTHORIZED }

    private val base = GatewayApi.validateBase(base)
    @Volatile private var running = true
    @Volatile private var connection: HttpsURLConnection? = null
    @Volatile var cursor: Long = startCursor
        private set
    private val thread = Thread(::loop, "chintu-event-stream").apply { isDaemon = true }

    fun start() = thread.start()

    fun stop() {
        running = false
        // A blocked socket read ignores interrupts; closing the connection ends it.
        connection?.disconnect()
        thread.interrupt()
    }

    private fun loop() {
        var failures = 0
        try {
            while (running) {
                onState(if (failures == 0) State.CONNECTING else State.RETRYING)
                val outcome = try { readOnce() } catch (_: Exception) { Outcome.FAILED }
                when (outcome) {
                    Outcome.UNSUPPORTED -> { onState(State.UNSUPPORTED); return }
                    Outcome.UNAUTHORIZED -> { onState(State.UNAUTHORIZED); return }
                    // The server recycles streams on purpose; come straight back.
                    Outcome.RECYCLED -> failures = 0
                    Outcome.FAILED -> failures++
                }
                if (!running) break
                Thread.sleep(delay(failures))
            }
        } catch (_: InterruptedException) {
        } finally {
            connection = null
            if (!running) onState(State.STOPPED)
        }
    }

    /** 0.3 s after a recycle; otherwise 1, 2, 4 … 30 s with jitter so many phones never reconnect in step. */
    private fun delay(failures: Int): Long {
        if (failures == 0) return 300
        val ceiling = minOf(30_000L, 1000L shl minOf(failures - 1, 5))
        return ceiling / 2 + (Math.random() * ceiling / 2).toLong()
    }

    private fun readOnce(): Outcome {
        val opened = URI("$base/api/v1/events/stream?after=$cursor").toURL().openConnection() as HttpsURLConnection
        connection = opened
        try {
            opened.instanceFollowRedirects = false
            // The server sends a keepalive every 15 s; three missed ones mean the link is gone.
            opened.connectTimeout = 10_000; opened.readTimeout = 45_000
            opened.setRequestProperty("X-Chintu-Protocol", "1")
            opened.setRequestProperty("Accept", "text/event-stream")
            opened.setRequestProperty("Cache-Control", "no-cache")
            opened.setRequestProperty("Authorization", "Bearer $token")
            if (cursor > 0) opened.setRequestProperty("Last-Event-ID", cursor.toString())
            when (opened.responseCode) {
                200 -> Unit
                401 -> return Outcome.UNAUTHORIZED
                // An older gateway has no stream; an impossible cursor needs the polling resync first.
                400, 404, 405, 501 -> return Outcome.UNSUPPORTED
                else -> return Outcome.FAILED
            }
            if (opened.contentType?.startsWith("text/event-stream") != true) return Outcome.FAILED
            val reader = BufferedReader(InputStreamReader(opened.inputStream, Charsets.UTF_8))
            val parser = SseParser(262_144)
            var live = false
            while (running) {
                val line = reader.readLine() ?: return if (live) Outcome.RECYCLED else Outcome.FAILED
                parser.line(line) { kind, data ->
                    if (kind == "ready") { live = true; onState(State.LIVE) }
                    else if (data.isNotEmpty()) deliver(JSONObject(data))
                }
            }
            return Outcome.RECYCLED
        } finally {
            opened.disconnect()
            connection = null
        }
    }

    private fun deliver(event: JSONObject) {
        val id = event.getLong("event_id")
        if (id <= cursor) return  // A replay after reconnecting; already delivered.
        cursor = id
        onEvent(event)
    }
}
