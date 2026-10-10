package ai.chintu

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import android.test.InstrumentationTestCase
import ai.chintu.core.SubmissionGuard
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.UUID

/** Device tests exercise real Keystore, preferences and atomic files without using live credentials. */
@Suppress("DEPRECATION")
class ClientStoreTest : InstrumentationTestCase() {
    private lateinit var isolated: TestContext
    private lateinit var cobra: String
    private lateinit var mac: String

    private class TestContext(base: Context, private val prefix: String) : ContextWrapper(base) {
        private val preferences = mutableSetOf<String>()
        private val directory = File(base.filesDir, prefix).apply { check(mkdir()) }
        override fun getFilesDir(): File = directory
        override fun getSharedPreferences(name: String, mode: Int): SharedPreferences {
            val isolatedName = "$prefix-$name"
            preferences.add(isolatedName)
            return baseContext.getSharedPreferences(isolatedName, mode)
        }
        fun clean() {
            preferences.forEach { baseContext.deleteSharedPreferences(it) }
            directory.deleteRecursively()
            // The shared provider key alias is never deleted: existing app tokens stay valid.
        }
    }

    override fun setUp() {
        super.setUp()
        val suffix = UUID.randomUUID().toString().replace("-", "")
        isolated = TestContext(instrumentation.targetContext, "client-store-test-$suffix")
        cobra = "https://cobra-$suffix.example.ts.net"
        mac = "https://mac-$suffix.example.ts.net"
    }

    override fun tearDown() {
        try { isolated.clean() } finally { super.tearDown() }
    }

    private fun client(url: String): ClientStore = ClientStore(isolated, url)
    private fun hostPrefs(url: String): SharedPreferences {
        val canonical = GatewayApi.validateBase(url)
        val identity = MessageDigest.getInstance("SHA-256").digest(canonical.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
        return isolated.getSharedPreferences("chintu-host-$identity", Context.MODE_PRIVATE)
    }
    private fun pair(store: ClientStore, marker: String) {
        store.paired(UUID.randomUUID().toString(), "non-secret-fixture-token-$marker")
    }
    private fun submission(guard: SubmissionGuard, marker: String): String = guard.prepare { key ->
        JSONObject().put("submission_key", key).put("instruction", marker)
            .put("project_id", "fixture-$marker").put("provider", "scripted")
            .put("execution_profile", "inspect").toString()
    }

    fun testHostTokensAreIsolatedAndSurviveRecreation() {
        val first = client(cobra)
        val second = client(mac)
        assertFalse(first.isPaired())
        assertFalse(second.isPaired())
        pair(first, "cobra")
        assertEquals("non-secret-fixture-token-cobra", first.token())
        assertNull(second.token())
        pair(second, "mac")
        assertEquals("non-secret-fixture-token-cobra", client(cobra).token())
        assertEquals("non-secret-fixture-token-mac", client(mac).token())
        assertTrue(hostPrefs(cobra).getBoolean("token_aad", false))
        assertTrue(hostPrefs(mac).getBoolean("token_aad", false))
    }

    fun testCopiedCiphertextCannotAuthenticateAnotherDestination() {
        val first = client(cobra)
        pair(first, "source")
        assertEquals("non-secret-fixture-token-source", first.token())
        val source = hostPrefs(cobra)
        check(hostPrefs(mac).edit()
            .putString("token", source.getString("token", null))
            .putString("iv", source.getString("iv", null))
            .putBoolean("token_aad", true).commit())
        var rejected = false
        try { client(mac).token() }
        catch (failure: java.security.GeneralSecurityException) { rejected = true }
        catch (failure: java.security.ProviderException) { rejected = true }
        assertTrue("A different gateway must fail the token's authenticated-data check", rejected)
        assertEquals("non-secret-fixture-token-source", client(cobra).token())
    }

    fun testCursorDraftAndChoicesBelongToEachDestination() {
        val first = client(cobra)
        val second = client(mac)
        first.cursor = 31
        first.draft = "Inspect Ubuntu fixture"
        first.choose("project", "ubuntu-project")
        first.choose("provider", "scripted")
        assertEquals(0L, second.cursor)
        assertEquals("", second.draft)
        assertEquals("", second.choice("project"))
        second.cursor = 8
        second.draft = "Inspect Mac fixture"
        second.choose("project", "mac-project")
        assertEquals(31L, client(cobra).cursor)
        assertEquals("Inspect Ubuntu fixture", client(cobra).draft)
        assertEquals("ubuntu-project", client(cobra).choice("project"))
        assertEquals("scripted", client(cobra).choice("provider"))
        assertEquals(8L, client(mac).cursor)
        assertEquals("Inspect Mac fixture", client(mac).draft)
        assertEquals("mac-project", client(mac).choice("project"))
    }

    fun testPendingRetryAndAcceptedIdentityNeverMoveBetweenHosts() {
        val first = SubmissionGuard(client(cobra))
        val second = SubmissionGuard(client(mac))
        val firstRequest = submission(first, "cobra")
        val secondRequest = submission(second, "mac")
        assertFalse(firstRequest == secondRequest)
        val restarted = SubmissionGuard(client(cobra))
        assertEquals(firstRequest, restarted.prepare { throw AssertionError("Retry must preserve exact saved bytes/key") })
        assertEquals(secondRequest, SubmissionGuard(client(mac)).pending())
        val accepted = UUID.randomUUID().toString()
        restarted.accepted(accepted)
        assertNull(SubmissionGuard(client(cobra)).pending())
        assertEquals(accepted, SubmissionGuard(client(cobra)).acceptedTaskId())
        assertEquals(secondRequest, SubmissionGuard(client(mac)).pending())
        assertNull(SubmissionGuard(client(mac)).acceptedTaskId())
    }

    fun testAcceptedRequestClearsOriginalDraftWithoutAnyUiCallback() {
        val origin = client(cobra)
        origin.draft = "  read the fixture token\n"
        val guard = SubmissionGuard(origin)
        submission(guard, "read the fixture token")
        client(mac).draft = "keep this other computer's draft"
        val accepted = UUID.randomUUID().toString()

        // Equivalent to a receipt arriving after onStop: only the durable guard is called.
        SubmissionGuard(client(cobra)).accepted(accepted)

        val recreated = client(cobra)
        assertEquals("", recreated.draft)
        assertNull(SubmissionGuard(recreated).pending())
        assertEquals(accepted, SubmissionGuard(recreated).acceptedTaskId())
        assertEquals("keep this other computer's draft", client(mac).draft)
    }

    fun testAcceptedRequestPreservesDraftEditedWhileReceiptWasPending() {
        val origin = client(cobra)
        origin.draft = "first instruction"
        val guard = SubmissionGuard(origin)
        val originalRequest = submission(guard, "first instruction")
        // A recreated screen writes a newer draft before the original worker returns.
        client(cobra).draft = "second instruction, not submitted yet"
        assertEquals(originalRequest, guard.pending())
        val accepted = UUID.randomUUID().toString()
        guard.accepted(accepted)

        val recreated = client(cobra)
        assertEquals("second instruction, not submitted yet", recreated.draft)
        assertNull(SubmissionGuard(recreated).pending())
        assertEquals(accepted, SubmissionGuard(recreated).acceptedTaskId())
    }

    fun testFailedAcceptanceCommitRetainsPendingIdentityAndDraft() {
        val origin = client(cobra)
        origin.draft = "must survive a failed durable receipt"
        val guard = SubmissionGuard(origin)
        val originalRequest = submission(guard, origin.draft)
        // Deny creation of the replacement file in this test's disposable directory only.
        android.system.Os.chmod(isolated.filesDir.absolutePath, 0b101000000)
        var refused = false
        try {
            guard.accepted(UUID.randomUUID().toString())
        } catch (expected: java.io.IOException) {
            refused = true
        } finally {
            android.system.Os.chmod(isolated.filesDir.absolutePath, 0b111000000)
        }
        assertTrue("A failed atomic receipt write must be reported", refused)
        val recreated = client(cobra)
        assertEquals(originalRequest, SubmissionGuard(recreated).pending())
        assertNull(SubmissionGuard(recreated).acceptedTaskId())
        assertEquals("must survive a failed durable receipt", recreated.draft)
        assertEquals(originalRequest, SubmissionGuard(recreated).prepare {
            throw AssertionError("Retry must preserve the original request/key after a failed commit")
        })
    }

    fun testCanonicalUrlAliasesShareTheSameHostIdentity() {
        val computers = ComputerStore(isolated)
        val alias = cobra.uppercase(java.util.Locale.ROOT) + "/"
        computers.save(Computer(alias, " Cobra Ubuntu ", "cobra, linux desktop"))
        computers.save(Computer(cobra, "Cobra Ubuntu", "cobra, my linux"))
        assertEquals(1, computers.all().size)
        assertEquals(cobra, computers.all().single().url)
        assertEquals("cobra, my linux", computers.all().single().aliases)
        pair(client(cobra), "canonical")
        client(cobra).cursor = 9
        val original = submission(SubmissionGuard(client(cobra)), "canonical")
        // Direct store callers must also canonicalize before deriving storage/AAD identity.
        assertEquals(cobra, client(alias).gateway)
        assertEquals("non-secret-fixture-token-canonical", client(alias).token())
        assertEquals(9L, client(alias).cursor)
        assertEquals(original, SubmissionGuard(client(alias)).pending())
    }

    fun testFailedSwitchKeepsActiveDestinationAndOtherHostPendingState() {
        val computers = ComputerStore(isolated)
        computers.save(Computer(cobra, "Cobra Ubuntu"))
        computers.save(Computer(mac, "My Mac"))
        computers.activate(cobra)
        val pending = submission(SubmissionGuard(client(cobra)), "still-on-cobra")
        try {
            computers.activate("https://not-saved.example.ts.net")
            fail("Unknown destination must not become active")
        } catch (expected: IllegalArgumentException) { }
        assertEquals(cobra, ComputerStore(isolated).active)
        try {
            computers.save(Computer("https://outside.invalid", "Unsafe endpoint"))
            fail("An invalid endpoint must not alter the active destination")
        } catch (expected: IllegalArgumentException) { }
        assertEquals(cobra, ComputerStore(isolated).active)
        computers.activate(mac)
        assertEquals(mac, ComputerStore(isolated).active)
        assertEquals(pending, SubmissionGuard(client(cobra)).pending())
        assertNull(SubmissionGuard(client(mac)).pending())
    }

    fun testPendingSubmissionBlocksRePairingOnlyItsOwnHost() {
        val first = client(cobra)
        pair(first, "original")
        val original = submission(SubmissionGuard(first), "pending")
        try {
            pair(first, "replacement")
            fail("Re-pairing an uncertain pending submission would change its client identity")
        } catch (expected: IllegalStateException) { }
        assertEquals("non-secret-fixture-token-original", client(cobra).token())
        assertEquals(original, SubmissionGuard(client(cobra)).pending())
        pair(client(mac), "independent")
        assertEquals("non-secret-fixture-token-independent", client(mac).token())
        assertEquals(original, SubmissionGuard(client(cobra)).pending())
    }
}
