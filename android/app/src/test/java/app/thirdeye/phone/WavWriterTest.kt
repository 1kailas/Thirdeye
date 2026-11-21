package app.thirdeye.phone

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class WavWriterTest {

    private fun intLe(buf: ByteArray, offset: Int) =
        ByteBuffer.wrap(buf, offset, 4).order(ByteOrder.LITTLE_ENDIAN).int

    private fun shortLe(buf: ByteArray, offset: Int) =
        ByteBuffer.wrap(buf, offset, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF

    private fun ascii(buf: ByteArray, offset: Int, length: Int) =
        String(buf, offset, length, Charsets.US_ASCII)

    @Test
    fun headerIsCanonicalPcmWav() {
        val pcm = ByteArray(3200) { (it % 251).toByte() }
        val wav = WavWriter.wav(pcm)
        assertEquals(44 + pcm.size, wav.size)
        assertEquals("RIFF", ascii(wav, 0, 4))
        assertEquals("WAVE", ascii(wav, 8, 4))
        assertEquals("fmt ", ascii(wav, 12, 4))
        assertEquals(16, intLe(wav, 16))
        assertEquals(1, shortLe(wav, 20))
        assertEquals(1, shortLe(wav, 22))
        assertEquals(16000, intLe(wav, 24))
        assertEquals(32000, intLe(wav, 28))
        assertEquals(2, shortLe(wav, 32))
        assertEquals(16, shortLe(wav, 34))
        assertEquals("data", ascii(wav, 36, 4))
        assertEquals(pcm.size, intLe(wav, 40))
        assertEquals(36 + pcm.size, intLe(wav, 4))
        assertArrayEquals(pcm, wav.copyOfRange(44, wav.size))
    }

    @Test
    fun emptyPcmStillValid() {
        val wav = WavWriter.wav(ByteArray(0))
        assertEquals(44, wav.size)
        assertEquals(36, intLe(wav, 4))
        assertEquals(0, intLe(wav, 40))
    }
}
