package app.thirdeye.phone

import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CameraCaptureTest {
    @Test
    fun retryStartsAfterStalledAttemptReleasesCamera() = runBlocking {
        val released = mutableListOf<Int>()
        val photo = byteArrayOf(1, 2)
        val result = captureWithRetry(100) { attempt ->
            try {
                if (attempt == 1) awaitCancellation()
                assertEquals(listOf(1), released)
                photo
            } finally {
                released += attempt
            }
        }
        assertArrayEquals(photo, result)
        assertEquals(listOf(1, 2), released)
    }

    @Test
    fun twoMissingCallbacksFinishWithCameraError() = runBlocking {
        var attempts = 0
        assertNull(captureWithRetry(100) {
            attempts++
            awaitCancellation()
        })
        assertEquals(2, attempts)
    }
}
