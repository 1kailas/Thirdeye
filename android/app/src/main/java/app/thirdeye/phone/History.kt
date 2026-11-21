package app.thirdeye.phone

import org.json.JSONObject
import java.io.File
import java.util.concurrent.atomic.AtomicLong

/** Last-N upload turns persisted under filesDir/history as JSON + media files. */
class History(private val dir: File, private val limit: Int = 20) {

    data class Turn(
        val id: String,
        val timestamp: Long,
        val transcript: String?,
        val reply: String,
        val route: String?,
        val httpCode: Int,
        val ok: Boolean,
        val elapsedMs: Long,
        val error: String?,
        val imageName: String,
        val audioName: String?,
        val firstSoundMs: Long? = null,
        val netMs: Long? = null,
    )

    private val counter = AtomicLong()

    fun add(
        image: ByteArray,
        audio: ByteArray?,
        audioExt: String?,
        transcript: String?,
        reply: String,
        route: String?,
        httpCode: Int,
        ok: Boolean,
        elapsedMs: Long,
        error: String?,
        firstSoundMs: Long? = null,
        netMs: Long? = null,
    ) {
        dir.mkdirs()
        val id = "${System.currentTimeMillis()}-${counter.getAndIncrement()}"
        File(dir, "$id.jpg").writeBytes(image)
        var audioName: String? = null
        if (audio != null && audioExt != null) {
            audioName = "$id.$audioExt"
            File(dir, audioName).writeBytes(audio)
        }
        val meta = JSONObject()
            .put("id", id)
            .put("timestamp", System.currentTimeMillis())
            .put("transcript", transcript ?: JSONObject.NULL)
            .put("reply", reply)
            .put("route", route ?: JSONObject.NULL)
            .put("http_code", httpCode)
            .put("ok", ok)
            .put("elapsed_ms", elapsedMs)
            .put("error", error ?: JSONObject.NULL)
            .put("image", "$id.jpg")
            .put("audio", audioName ?: JSONObject.NULL)
            .put("first_sound_ms", firstSoundMs ?: JSONObject.NULL)
            .put("net_ms", netMs ?: JSONObject.NULL)
        File(dir, "$id.json").writeText(meta.toString(), Charsets.UTF_8)
        trim()
    }

    fun list(): List<Turn> {
        val files = dir.listFiles { f -> f.extension == "json" } ?: return emptyList()
        return files.mapNotNull { file ->
            runCatching { turnFromJson(JSONObject(file.readText(Charsets.UTF_8))) }.getOrNull()
        }.sortedByDescending { it.timestamp }
    }

    fun turn(id: String): Turn? = list().firstOrNull { it.id == id }

    fun imageFile(turn: Turn): File = File(dir, turn.imageName)

    fun audioFile(turn: Turn): File? = turn.audioName?.let { File(dir, it) }

    fun clear() {
        dir.listFiles()?.forEach { it.delete() }
    }

    private fun trim() {
        val files = dir.listFiles { f -> f.extension == "json" }
            ?.sortedByDescending { it.lastModified() } ?: return
        for (old in files.drop(limit)) {
            val id = old.nameWithoutExtension
            dir.listFiles { f -> f.name.startsWith("$id.") }?.forEach { it.delete() }
        }
    }

    private fun turnFromJson(json: JSONObject): Turn {
        fun str(key: String) = if (json.isNull(key)) null else json.optString(key)
        return Turn(
            id = json.getString("id"),
            timestamp = json.getLong("timestamp"),
            transcript = str("transcript"),
            reply = json.optString("reply"),
            route = str("route"),
            httpCode = json.optInt("http_code"),
            ok = json.optBoolean("ok"),
            elapsedMs = json.optLong("elapsed_ms"),
            error = str("error"),
            imageName = json.optString("image"),
            audioName = str("audio"),
            firstSoundMs = if (json.isNull("first_sound_ms")) null else json.optLong("first_sound_ms"),
            netMs = if (json.isNull("net_ms")) null else json.optLong("net_ms"),
        )
    }
}
