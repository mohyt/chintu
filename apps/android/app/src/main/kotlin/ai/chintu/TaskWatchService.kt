package ai.chintu

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Keeps watching one accepted task after the app leaves the foreground, then notifies when it finishes.
 * Polls the task snapshot only (never the event cursor, which belongs to the activity), so the two
 * readers cannot race. Stops itself on a terminal state, after a bounded wait, or when the app returns.
 *
 * Android kills the app when a service started with startForegroundService() is stopped, or stops
 * itself, before it calls startForeground(). Every command therefore reaches startForeground() first,
 * and stop() sends an ACTION_STOP command instead of stopService() while a start may still be queued,
 * so the start command is handled (and promoted) before the stop.
 */
class TaskWatchService : Service() {
    companion object {
        const val CHANNEL_WORKING = "chintu-working"
        const val CHANNEL_DONE = "chintu-done"
        private const val NOTIFICATION_ID = 41
        private const val MAX_WAIT_MS = 45 * 60 * 1000L
        const val PREF_ANNOUNCE = "announce"
        private const val ACTION_STOP = "ai.chintu.action.STOP_WATCH"
        @Volatile private var running = false  // a start was requested and the service has not finished yet

        fun start(context: Context, gateway: String, taskId: String, computer: String, instruction: String) {
            val intent = Intent(context, TaskWatchService::class.java)
                .putExtra("gateway", gateway).putExtra("task", taskId).putExtra("computer", computer).putExtra("instruction", instruction)
            running = true
            context.startForegroundService(intent)
        }
        /** Called from the activity's onStart, so the app is in the foreground and may start the service to stop it. */
        fun stop(context: Context) {
            if (running) context.startService(Intent(context, TaskWatchService::class.java).setAction(ACTION_STOP))
        }
        fun channels(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(CHANNEL_WORKING, "Working on a task", NotificationManager.IMPORTANCE_LOW)
                .apply { description = "Shown while a computer is still working on your instruction" })
            manager.createNotificationChannel(NotificationChannel(CHANNEL_DONE, "Task results", NotificationManager.IMPORTANCE_HIGH)
                .apply { description = "A computer finished an instruction" })
        }
    }

    private val executor = Executors.newSingleThreadExecutor()
    private val stopped = AtomicBoolean(false)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val gateway = intent?.getStringExtra("gateway"); val taskId = intent?.getStringExtra("task")
        val computer = intent?.getStringExtra("computer") ?: "your computer"
        val instruction = intent?.getStringExtra("instruction").orEmpty()
        channels(this)
        startForeground(NOTIFICATION_ID, working(computer, instruction, "Working on it…"), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        if (intent?.action == ACTION_STOP || gateway.isNullOrBlank() || taskId.isNullOrBlank()) {
            stopped.set(true); stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); return START_NOT_STICKY
        }
        executor.execute { watch(gateway, taskId, computer, instruction) }
        return START_NOT_STICKY
    }

    private fun watch(gateway: String, taskId: String, computer: String, instruction: String) {
        val store = ClientStore(this, gateway)
        val token = try { store.token() } catch (_: Exception) { null } ?: run { stopSelf(); return }
        val api = GatewayApi(gateway, token)
        val deadline = System.currentTimeMillis() + MAX_WAIT_MS
        var lastProgress = ""
        while (!stopped.get() && System.currentTimeMillis() < deadline) {
            try {
                val snapshot = api.call("GET", "/api/v1/tasks/$taskId")
                val state = snapshot.getString("state")
                if (state in setOf("succeeded", "failed", "cancelled", "interrupted")) {
                    val result = snapshot.optJSONObject("result")
                    val summary = result?.optString("summary").orEmpty().ifBlank { "Finished with state $state." }
                    val speech = result?.optString("speech_text").orEmpty().ifBlank { summary }
                    // The activity reads this once on return and speaks it, so the reply is heard even if the notification was missed.
                    getSharedPreferences("chintu-ui", MODE_PRIVATE).edit().putString(PREF_ANNOUNCE, JSONObject()
                        .put("task", taskId).put("computer", computer).put("speech", speech).toString()).apply()
                    notify(done(computer, state, summary))
                    stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); return
                }
                val latest = snapshot.optJSONObject("pending_input")?.optString("question").orEmpty()
                if (latest.isNotBlank() && latest != lastProgress) { lastProgress = latest; notify(working(computer, instruction, latest)) }
            } catch (_: Exception) { /* transient; keep polling until the deadline */ }
            try { Thread.sleep(5000) } catch (_: InterruptedException) { return }
        }
        if (!stopped.get()) notify(done(computer, "still running", "Still working. Open Chintu to check on it."))
        stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
    }

    private fun open(): PendingIntent = PendingIntent.getActivity(this, 0,
        Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    /** Lock screen only ever sees a generic line; instruction and result text stay behind the unlock. */
    private fun publicVersion(channel: String, title: String): Notification =
        Notification.Builder(this, channel).setSmallIcon(R.drawable.chintu_icon)
            .setContentTitle(title).setContentText("Unlock to see details").setContentIntent(open()).build()

    private fun working(computer: String, instruction: String, progress: String): Notification =
        Notification.Builder(this, CHANNEL_WORKING).setSmallIcon(R.drawable.chintu_icon)
            .setContentTitle("$computer is working").setContentText(progress)
            .setStyle(Notification.BigTextStyle().bigText("“${instruction.take(140)}”\n$progress"))
            .setVisibility(Notification.VISIBILITY_PRIVATE).setPublicVersion(publicVersion(CHANNEL_WORKING, "Chintu is working"))
            .setOngoing(true).setContentIntent(open()).build()

    private fun done(computer: String, state: String, summary: String): Notification =
        Notification.Builder(this, CHANNEL_DONE).setSmallIcon(R.drawable.chintu_icon)
            .setContentTitle("$computer · $state").setContentText(summary.take(120))
            .setStyle(Notification.BigTextStyle().bigText(summary.take(1200)))
            .setVisibility(Notification.VISIBILITY_PRIVATE).setPublicVersion(publicVersion(CHANNEL_DONE, "Chintu finished a task"))
            .setAutoCancel(true).setContentIntent(open()).build()

    private fun notify(notification: Notification) {
        try { getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification) } catch (_: SecurityException) { }
    }

    override fun onDestroy() { stopped.set(true); running = false; executor.shutdownNow(); super.onDestroy() }
}
