package app.thirdeye.phone

import org.json.JSONObject
import java.util.Base64

sealed interface StreamEvent {
    data class Meta(
        val ok: Boolean,
        val reply: String,
        val transcript: String?,
        val route: String?,
        val parts: Int,
    ) : StreamEvent

    data class Audio(val index: Int, val clip: ByteArray?) : StreamEvent

    data class Done(val ttfaMs: Long?) : StreamEvent
}

object StreamProtocol {
    fun parseLine(line: String): StreamEvent? {
        if (line.isBlank()) return null
        val json = runCatching { JSONObject(line) }.getOrNull() ?: return null
        return when (json.optString("type")) {
            "meta" -> StreamEvent.Meta(
                ok = json.optBoolean("ok", true),
                reply = json.optString("reply"),
                transcript = json.nullableString("transcript"),
                route = json.optJSONObject("debug")?.nullableString("route"),
                parts = json.optInt("parts", 0),
            )
            "audio" -> StreamEvent.Audio(
                index = json.optInt("index"),
                clip = json.nullableString("audio_b64")
                    ?.let { runCatching { Base64.getDecoder().decode(it) }.getOrNull() },
            )
            "done" -> StreamEvent.Done(
                json.optJSONObject("debug")?.takeIf { !it.isNull("ttfa_ms") }?.optDouble("ttfa_ms")?.toLong()
            )
            else -> null
        }
    }

    private fun JSONObject.nullableString(key: String): String? = if (isNull(key)) null else optString(key)
}
