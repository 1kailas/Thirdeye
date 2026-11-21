package app.thirdeye.phone

import java.net.URI
import java.net.URLDecoder

/** Parses thirdeye://pair?url=<base>&token=<token> deep links. Pure Kotlin for JVM tests. */
object PairingLink {
    data class Result(val baseUrl: String, val token: String)

    fun parse(link: String?): Result? {
        if (link.isNullOrBlank()) return null
        val uri = runCatching { URI(link) }.getOrNull() ?: return null
        if (uri.scheme != "thirdeye" || uri.host != "pair") return null
        val params = (uri.rawQuery ?: return null).split("&").mapNotNull { pair ->
            val kv = pair.split("=", limit = 2)
            if (kv.size == 2) kv[0] to URLDecoder.decode(kv[1], "UTF-8") else null
        }.toMap()
        val url = params["url"]?.trim()?.trimEnd('/') ?: return null
        val token = params["token"]?.trim() ?: return null
        if (token.isEmpty()) return null
        if (!url.startsWith("http://") && !url.startsWith("https://")) return null
        return Result(url, token)
    }
}
