package app.thirdeye.phone

import android.util.Base64
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

/** Minimal blocking client for POST /api/phone/ask and GET /api/phone/ping. */
class PhoneApi(
    private val baseUrl: String,
    private val token: String,
) {
    data class AskResult(
        val ok: Boolean,
        val reply: String,
        val transcript: String?,
        val audioB64: String?,
        val httpCode: Int,
        val route: String?,
    )

    interface StreamListener {
        fun onMeta(meta: StreamEvent.Meta)
        fun onAudio(index: Int, clip: ByteArray?)
    }

    data class StreamResult(val result: AskResult, val streamed: Boolean, val serverTtfaMs: Long?)

    @Volatile private var active: HttpURLConnection? = null

    fun cancel() {
        val connection = active ?: return
        Thread { runCatching { connection.disconnect() } }.start()
    }

    private fun parseResult(json: JSONObject, code: Int): AskResult {
        val debug = json.optJSONObject("debug")
        return AskResult(
            ok = json.optBoolean("ok"),
            reply = json.optString("reply"),
            transcript = if (json.isNull("transcript")) null else json.optString("transcript"),
            audioB64 = if (json.isNull("audio_b64")) null else json.optString("audio_b64"),
            httpCode = code,
            route = debug?.takeIf { !it.isNull("route") }?.optString("route"),
        )
    }

    fun askStream(imageJpeg: ByteArray, audioWav: ByteArray?, listener: StreamListener): StreamResult? {
        val body = JSONObject()
            .put("image_b64", Base64.encodeToString(imageJpeg, Base64.NO_WRAP))
            .put("stream", true)
        if (audioWav != null) {
            body.put("audio_b64", Base64.encodeToString(audioWav, Base64.NO_WRAP))
            body.put("audio_type", "audio/wav")
        }
        val payload = body.toString().toByteArray(Charsets.UTF_8)
        var outcome = postStream(payload, listener)
        if (outcome == null && connectFailed) {
            Thread.sleep(700)
            outcome = postStream(payload, listener)
        }
        return outcome
    }

    private fun postStream(payload: ByteArray, listener: StreamListener): StreamResult? {
        connectFailed = false
        val connection = open("/api/phone/ask") ?: return null
        active = connection
        var responded = false
        var meta: StreamEvent.Meta? = null
        var ttfa: Long? = null
        var code = 0
        try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.setRequestProperty("Accept", "application/x-ndjson, application/json")
            connection.setFixedLengthStreamingMode(payload.size)
            connection.outputStream.use { it.write(payload) }
            code = connection.responseCode
            responded = true
            val type = connection.contentType.orEmpty()
            if (code !in 200..299 || !type.contains("ndjson")) {
                val text = readBody(if (code in 200..299) connection.inputStream else connection.errorStream)
                val json = runCatching { JSONObject(text) }.getOrNull() ?: return null
                return StreamResult(parseResult(json, code), false, null)
            }
            BufferedReader(InputStreamReader(connection.inputStream, Charsets.UTF_8)).use { reader ->
                while (true) {
                    val line = reader.readLine() ?: break
                    when (val event = StreamProtocol.parseLine(line)) {
                        is StreamEvent.Meta -> {
                            meta = event
                            listener.onMeta(event)
                        }
                        is StreamEvent.Audio -> listener.onAudio(event.index, event.clip)
                        is StreamEvent.Done -> {
                            ttfa = event.ttfaMs
                            break
                        }
                        null -> {}
                    }
                }
            }
        } catch (failure: Exception) {
            connectFailed = !responded && (failure is java.net.ConnectException
                || failure is java.net.UnknownHostException
                || failure is java.net.SocketTimeoutException)
        } finally {
            active = null
            connection.disconnect()
        }
        val known = meta ?: return null
        return StreamResult(AskResult(known.ok, known.reply, known.transcript, null, code, known.route), true, ttfa)
    }

    data class PingResult(val ok: Boolean, val aiReady: Boolean, val httpCode: Int)

    /** Returns null only when the request itself failed (DNS, timeout, refused). */
    fun ask(imageJpeg: ByteArray, audioWav: ByteArray?): AskResult? {
        val body = JSONObject()
            .put("image_b64", Base64.encodeToString(imageJpeg, Base64.NO_WRAP))
        if (audioWav != null) {
            body.put("audio_b64", Base64.encodeToString(audioWav, Base64.NO_WRAP))
            body.put("audio_type", "audio/wav")
        }
        val payload = body.toString().toByteArray(Charsets.UTF_8)
        var outcome = post("/api/phone/ask", payload)
        if (outcome == null && connectFailed) {
            Thread.sleep(700)
            outcome = post("/api/phone/ask", payload)
        }
        val (code, text) = outcome ?: return null
        val json = runCatching { JSONObject(text) }.getOrNull() ?: return null
        val debug = json.optJSONObject("debug")
        return AskResult(
            ok = json.optBoolean("ok"),
            reply = json.optString("reply"),
            transcript = if (json.isNull("transcript")) null else json.optString("transcript"),
            audioB64 = if (json.isNull("audio_b64")) null else json.optString("audio_b64"),
            httpCode = code,
            route = debug?.takeIf { !it.isNull("route") }?.optString("route"),
        )
    }

    fun ping(): PingResult? {
        val connection = open("/api/phone/ping") ?: return null
        try {
            connection.requestMethod = "GET"
            connection.setRequestProperty("Authorization", "Bearer $token")
            val code = connection.responseCode
            val text = readBody(if (code in 200..299) connection.inputStream else connection.errorStream)
            val json = runCatching { JSONObject(text) }.getOrNull()
                ?: return PingResult(ok = false, aiReady = false, httpCode = code)
            return PingResult(ok = json.optBoolean("ok"), aiReady = json.optBoolean("ai_ready"), httpCode = code)
        } catch (_: Exception) {
            return null
        } finally {
            connection.disconnect()
        }
    }

    private var connectFailed = false

    private fun post(path: String, payload: ByteArray): Pair<Int, String>? {
        connectFailed = false
        val connection = open(path) ?: return null
        var responded = false
        try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.setFixedLengthStreamingMode(payload.size)
            connection.outputStream.use { it.write(payload) }
            val code = connection.responseCode
            responded = true
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            return code to readBody(stream)
        } catch (failure: Exception) {
            connectFailed = !responded && (failure is java.net.ConnectException
                || failure is java.net.UnknownHostException
                || failure is java.net.SocketTimeoutException)
            return null
        } finally {
            connection.disconnect()
        }
    }

    private fun open(path: String): HttpURLConnection? {
        return try {
            (URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection).apply {
                connectTimeout = 8_000
                readTimeout = 70_000
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Accept", "application/json")
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun readBody(stream: java.io.InputStream?): String {
        if (stream == null) return ""
        return BufferedReader(InputStreamReader(stream, Charsets.UTF_8)).use { it.readText() }
    }
}
