package ai.chintu

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import org.json.JSONArray
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import ai.chintu.core.SubmissionGuard
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** A destination owns its credential, cursor, draft, selection and pending submission. */
data class Computer(val url: String, val name: String, val aliases: String = "")

class ComputerStore(private val context: Context) {
    private val prefs = context.getSharedPreferences("chintu-computers", Context.MODE_PRIVATE)
    init {
        // Preserve the first prototype's pairing and pending request on upgrade.
        val legacy = context.getSharedPreferences("chintu", Context.MODE_PRIVATE)
        val oldUrl = legacy.getString("gateway", "").orEmpty()
        if (!prefs.getBoolean("migrated", false)) {
            if (oldUrl.isNotBlank()) {
                val canonical = GatewayApi.validateBase(oldUrl)
                val destination = ClientStore(context, canonical)
                destination.migrate(legacy, File(context.filesDir, "submission-state.json"))
                save(Computer(canonical, java.net.URI(canonical).host.substringBefore('.')))
                activate(canonical)
            }
            check(prefs.edit().putBoolean("migrated", true).commit())
        }
    }
    var active: String
        get() = prefs.getString("active", "").orEmpty()
        private set(value) { check(prefs.edit().putString("active", value).commit()) }
    fun all(): List<Computer> {
        val values = JSONArray(prefs.getString("computers", "[]"))
        return (0 until values.length()).map { values.getJSONObject(it).let { row ->
            Computer(row.getString("url"), row.getString("name"), row.optString("aliases"))
        } }
    }
    fun save(value: Computer) {
        require(value.name.trim().length in 1..60) { "Give this computer a name of 1–60 characters." }
        require(value.aliases.length <= 240) { "Keep voice aliases under 240 characters." }
        val canonical = GatewayApi.validateBase(value.url)
        val rows = all().filter { it.url != canonical } + value.copy(url = canonical, name = value.name.trim())
        require(rows.size <= 100) { "Up to 100 computers can be saved." }
        val array = JSONArray()
        rows.forEach { array.put(JSONObject().put("url", it.url).put("name", it.name).put("aliases", it.aliases)) }
        check(prefs.edit().putString("computers", array.toString()).commit()) { "Could not save computer." }
    }
    fun activate(url: String) {
        require(all().any { it.url == url }) { "Save the computer before selecting it." }
        active = url
    }
    fun deactivate() { active = "" }
    fun remove(url: String) {
        val canonical = GatewayApi.validateBase(url)
        val array = JSONArray()
        all().filter { it.url != canonical }.forEach { array.put(JSONObject().put("url", it.url).put("name", it.name).put("aliases", it.aliases)) }
        check(prefs.edit().putString("computers", array.toString()).commit()) { "Could not remove computer." }
        if (active == canonical) deactivate()
    }
}

class ClientStore(context: Context, gateway: String) : SubmissionGuard.Store {
    companion object {
        private val stateLocks = java.util.concurrent.ConcurrentHashMap<String, Any>()
    }
    val gateway: String = if (gateway.isBlank()) "" else GatewayApi.validateBase(gateway)
    private val identity = MessageDigest.getInstance("SHA-256").digest(this.gateway.toByteArray())
        .joinToString("") { "%02x".format(it) }
    private val submissionFile = File(context.filesDir, "submission-$identity.json")
    private val stateLock = stateLocks.computeIfAbsent(submissionFile.absolutePath) { Any() }
    private val prefs = context.getSharedPreferences("chintu-host-$identity", Context.MODE_PRIVATE)
    var cursor: Long
        get() = prefs.getLong("cursor", 0)
        set(value) { commit { putLong("cursor", value) } }
    var draft: String
        get() = synchronized(stateLock) { submissionState().optString("draft", prefs.getString("draft", "").orEmpty()) }
        set(value) { synchronized(stateLock) { saveSubmissionState(submissionState().put("draft", value)) } }
    /**
     * The conversation that new voice and chat turns join on this computer: its ID, workspace, agent, profile,
     * working directory and title, plus whether the computer has acknowledged it. Null starts a fresh one.
     */
    var conversation: JSONObject?
        get() = prefs.getString("conversation", null)?.let { try { JSONObject(it) } catch (_: Exception) { null } }
        set(value) { commit { if (value == null) remove("conversation") else putString("conversation", value.toString()) } }
    fun choice(key: String): String = prefs.getString("choice_$key", "").orEmpty()
    fun choose(key: String, value: String) { commit { putString("choice_$key", value) } }
    private fun commit(edit: android.content.SharedPreferences.Editor.() -> Unit) {
        check(prefs.edit().apply(edit).commit()) { "Cannot persist client state; request not sent." }
    }
    fun migrate(legacy: android.content.SharedPreferences, oldSubmission: File) {
        if (prefs.getBoolean("migrated", false)) return
        if (oldSubmission.exists() && !submissionFile.exists()) saveSubmissionState(JSONObject(oldSubmission.readText()))
        val legacyToken = legacy.getString("token", null)?.let { encrypted ->
            Cipher.getInstance("AES/GCM/NoPadding").run {
                init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(legacy.getString("iv", null), Base64.NO_WRAP)))
                doFinal(Base64.decode(encrypted, Base64.NO_WRAP))
            }
        }
        val boundCipher = legacyToken?.let {
            Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()); updateAAD(gateway.toByteArray(Charsets.UTF_8)) }
        }
        val boundToken = legacyToken?.let { boundCipher!!.doFinal(it) }
        commit {
            legacy.getString("client_id", null)?.let { putString("client_id", it) }
            if (boundToken != null) {
                putString("token", Base64.encodeToString(boundToken, Base64.NO_WRAP))
                putString("iv", Base64.encodeToString(boundCipher!!.iv, Base64.NO_WRAP)); putBoolean("token_aad", true)
            }
            putLong("cursor", legacy.getLong("cursor", 0)); putBoolean("migrated", true)
        }
    }
    private fun key(): SecretKey {
        val keys = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keys.getKey("chintu-token", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("chintu-token", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun isPaired() = prefs.contains("token")
    /** Drops this host's credential, cursor, choices, draft and pending submission. The shared key stays for other hosts. */
    fun forget() {
        synchronized(stateLock) {
            check(prefs.edit().clear().commit()) { "Could not clear this computer's data." }
            submissionFile.delete()
        }
    }
    fun token(): String? {
        val encrypted = prefs.getString("token", null) ?: return null
        val iv = Base64.decode(prefs.getString("iv", null), Base64.NO_WRAP)
        return Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
            if (prefs.getBoolean("token_aad", false)) updateAAD(gateway.toByteArray(Charsets.UTF_8))
            String(doFinal(Base64.decode(encrypted, Base64.NO_WRAP)), Charsets.UTF_8)
        }
    }
    fun paired(id: String, token: String) {
        check(read() == null) { "Resolve this computer's pending submission before pairing again." }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.ENCRYPT_MODE, key()); updateAAD(gateway.toByteArray(Charsets.UTF_8))
        }
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        commit {
            putString("client_id", id); putString("token", Base64.encodeToString(encrypted, Base64.NO_WRAP))
            putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            putBoolean("token_aad", true); putLong("cursor", 0)
        }
    }
    private fun submissionState(): JSONObject = if (submissionFile.exists())
        JSONObject(submissionFile.readText(Charsets.UTF_8)) else JSONObject()
    private fun saveSubmissionState(state: JSONObject) {
        val temporary = File.createTempFile("submission-", ".tmp", submissionFile.parentFile)
        try {
            FileOutputStream(temporary).use { it.write(state.toString().toByteArray(Charsets.UTF_8)); it.fd.sync() }
            Files.move(temporary.toPath(), submissionFile.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally { temporary.delete() }
    }
    override fun read(): String? = synchronized(stateLock) { submissionState().let { if (it.has("pending")) it.getString("pending") else null } }
    override fun write(request: String) { synchronized(stateLock) { saveSubmissionState(submissionState().put("pending", request)) } }
    override fun clear() { synchronized(stateLock) { val state = submissionState(); state.remove("pending"); saveSubmissionState(state) } }
    override fun accept(taskId: String) { synchronized(stateLock) {
        val state = submissionState()
        val instruction = state.optString("pending").takeIf { it.isNotBlank() }?.let { JSONObject(it).optString("instruction") }
        if (instruction != null && draft.trim() == instruction.trim()) state.put("draft", "")
        state.put("accepted_task_id", taskId); state.remove("pending"); saveSubmissionState(state)
    } }
    override fun acceptedTaskId(): String? = synchronized(stateLock) { submissionState().let { if (it.has("accepted_task_id")) it.getString("accepted_task_id") else null } }
}

/**
 * Per-computer activity log kept on the phone: final outcomes only, pruned to the last seven days.
 * Anything older is fetched from the daemon page by page when the owner asks for it.
 */
class ActivityStore(context: Context, gateway: String) {
    companion object { const val RETENTION_MS = 7L * 24 * 60 * 60 * 1000 }
    data class Entry(val taskId: String, val instruction: String, val state: String, val summary: String, val updatedAt: Long, val seen: Boolean) {
        val terminal: Boolean get() = state in setOf("succeeded", "failed", "cancelled", "interrupted")
    }
    private val identity = MessageDigest.getInstance("SHA-256").digest(gateway.toByteArray()).joinToString("") { "%02x".format(it) }.take(32)
    private val prefs = context.getSharedPreferences("chintu-activity-$identity", Context.MODE_PRIVATE)

    private fun load(): MutableList<Entry> {
        val cutoff = System.currentTimeMillis() - RETENTION_MS
        val array = try { JSONArray(prefs.getString("entries", "[]")) } catch (_: Exception) { JSONArray() }
        val rows = (0 until array.length()).map { array.getJSONObject(it) }.map {
            Entry(it.getString("task_id"), it.optString("instruction"), it.optString("state"), it.optString("summary"),
                it.optLong("updated_at"), it.optBoolean("seen"))
        }
        return rows.filter { it.updatedAt >= cutoff }.sortedByDescending { it.updatedAt }.toMutableList()
    }
    private fun save(rows: List<Entry>) {
        val array = JSONArray()
        rows.sortedByDescending { it.updatedAt }.take(200).forEach { array.put(JSONObject()
            .put("task_id", it.taskId).put("instruction", it.instruction).put("state", it.state)
            .put("summary", it.summary).put("updated_at", it.updatedAt).put("seen", it.seen)) }
        prefs.edit().putString("entries", array.toString()).apply()
    }
    fun all(): List<Entry> = load()
    fun unseen(): Int = load().count { it.terminal && !it.seen }
    fun markAllSeen() { save(load().map { it.copy(seen = true) }) }
    /** Upsert from a task snapshot; a newly terminal task becomes unseen so the bell can light up. */
    fun record(task: JSONObject) {
        val rows = load()
        val id = task.getString("task_id")
        val state = task.optString("state")
        val summary = task.optJSONObject("result")?.optString("summary").orEmpty()
        val stamp = parseInstant(task.optString("updated_at")) ?: System.currentTimeMillis()
        val previous = rows.firstOrNull { it.taskId == id }
        val becameTerminal = state in setOf("succeeded", "failed", "cancelled", "interrupted") && previous?.state != state
        rows.removeAll { it.taskId == id }
        rows.add(Entry(id, task.optString("instruction"), state, summary, maxOf(stamp, previous?.updatedAt ?: 0L),
            if (becameTerminal) false else previous?.seen ?: true))
        save(rows)
    }
    fun entryFrom(task: JSONObject): Entry = Entry(task.getString("task_id"), task.optString("instruction"), task.optString("state"),
        task.optJSONObject("result")?.optString("summary").orEmpty(), parseInstant(task.optString("updated_at")) ?: 0L, true)
    private fun parseInstant(value: String): Long? = try { java.time.Instant.parse(value).toEpochMilli() } catch (_: Exception) { null }
}
