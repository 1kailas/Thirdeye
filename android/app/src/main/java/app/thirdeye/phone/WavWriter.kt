package app.thirdeye.phone

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Wraps raw PCM16 mono samples in a canonical 44-byte RIFF/WAVE header. */
object WavWriter {
    const val SAMPLE_RATE = 16000
    const val CHANNELS = 1
    const val BITS_PER_SAMPLE = 16

    fun wav(pcm: ByteArray, sampleRate: Int = SAMPLE_RATE): ByteArray {
        val dataSize = pcm.size
        val out = ByteArray(44 + dataSize)
        val buffer = ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN)
        buffer.put("RIFF".toByteArray(Charsets.US_ASCII))
        buffer.putInt(36 + dataSize)
        buffer.put("WAVE".toByteArray(Charsets.US_ASCII))
        buffer.put("fmt ".toByteArray(Charsets.US_ASCII))
        buffer.putInt(16)
        buffer.putShort(1)
        buffer.putShort(CHANNELS.toShort())
        buffer.putInt(sampleRate)
        buffer.putInt(sampleRate * CHANNELS * BITS_PER_SAMPLE / 8)
        buffer.putShort((CHANNELS * BITS_PER_SAMPLE / 8).toShort())
        buffer.putShort(BITS_PER_SAMPLE.toShort())
        buffer.put("data".toByteArray(Charsets.US_ASCII))
        buffer.putInt(dataSize)
        pcm.copyInto(out, 44)
        return out
    }
}
