package app.thirdeye.phone

import kotlinx.coroutines.withTimeoutOrNull

/** A camera eviction can leave CameraX waiting indefinitely for a capture callback. */
internal suspend fun captureWithRetry(
    timeoutMs: Long,
    capture: suspend (attempt: Int) -> ByteArray?,
): ByteArray? {
    for (attempt in 1..2) {
        val photo = withTimeoutOrNull(timeoutMs) { capture(attempt) }
        if (photo != null) return photo
    }
    return null
}
