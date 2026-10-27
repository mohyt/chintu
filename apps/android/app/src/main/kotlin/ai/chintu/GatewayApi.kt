package ai.chintu

import org.json.JSONObject
import java.net.URI
import java.io.ByteArrayOutputStream
import javax.net.ssl.HttpsURLConnection

class ApiFailure(val status: Int, val code: String, val retryable: Boolean, val details: JSONObject, val serverMessage: String = "") :
    Exception("Gateway returned HTTP $status ($code).")

/** Called off the main thread. System TLS validation remains enabled; redirects are rejected. */
class GatewayApi(private val base: String, private val token: String? = null) {
    companion object {
        fun validateBase(value: String): String {
            val uri = URI(value.trim())
            val host = uri.host?.lowercase(java.util.Locale.ROOT)
            require(uri.scheme.equals("https", ignoreCase = true) && host?.endsWith(".ts.net") == true &&
                uri.userInfo == null && uri.query == null && uri.fragment == null &&
                (uri.path.isNullOrEmpty() || uri.path == "/") && (uri.port == -1 || uri.port == 443)) {
                "Use your private https://machine.tailnet.ts.net Tailscale Serve URL."
            }
            return "https://$host"
        }
    }
    init { validateBase(base) }
    fun call(method: String, path: String, body: String? = null): JSONObject {
        require(path.startsWith("/api/v1/"))
        val connection = URI(base + path).toURL().openConnection() as HttpsURLConnection
        try {
            connection.requestMethod = method
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 10_000; connection.readTimeout = 15_000
            connection.setRequestProperty("X-Chintu-Protocol", "1")
            connection.setRequestProperty("Accept", "application/json")
            token?.let { connection.setRequestProperty("Authorization", "Bearer $it") }
            body?.let {
                val bytes = it.toByteArray(Charsets.UTF_8)
                require(bytes.size <= 65_536) { "Request exceeds 64 KiB." }
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.setFixedLengthStreamingMode(bytes.size)
                connection.outputStream.use { stream -> stream.write(bytes) }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val bytes = stream?.use { input ->
                val buffer = ByteArray(8192)
                val result = ByteArrayOutputStream()
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    check(result.size() + count <= 1_048_576) { "Gateway response exceeds client limit." }
                    result.write(buffer, 0, count)
                }
                result.toByteArray()
            } ?: byteArrayOf()
            check(bytes.size <= 1_048_576) { "Gateway response exceeds client limit." }
            val json = if (bytes.isEmpty()) JSONObject() else JSONObject(String(bytes, Charsets.UTF_8))
            if (status !in 200..299) {
                val error = json.optJSONObject("error") ?: JSONObject()
                throw ApiFailure(status, error.optString("code", "http_error"), error.optBoolean("retryable"),
                    error.optJSONObject("details") ?: JSONObject(), error.optString("message").take(300))
            }
            return json
        } finally { connection.disconnect() }
    }
}
