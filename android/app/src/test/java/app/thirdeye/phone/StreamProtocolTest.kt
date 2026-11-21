package app.thirdeye.phone

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class StreamProtocolTest {

    @Test
    fun parsesMetaWithRouteAndParts() {
        val event = StreamProtocol.parseLine(
            """{"type":"meta","ok":true,"reply":"ശരി","transcript":"hi","parts":3,"debug":{"route":"locate"}}"""
        ) as StreamEvent.Meta
        assertTrue(event.ok)
        assertEquals("ശരി", event.reply)
        assertEquals("hi", event.transcript)
        assertEquals("locate", event.route)
        assertEquals(3, event.parts)
    }

    @Test
    fun metaWithNullTranscriptAndMissingDebug() {
        val event = StreamProtocol.parseLine(
            """{"type":"meta","ok":true,"reply":"x","transcript":null,"parts":1}"""
        ) as StreamEvent.Meta
        assertNull(event.transcript)
        assertNull(event.route)
    }

    @Test
    fun parsesAudioAndNullAudio() {
        val bytes = byteArrayOf(1, 2, 3)
        val b64 = Base64.getEncoder().encodeToString(bytes)
        val audio = StreamProtocol.parseLine("""{"type":"audio","index":2,"audio_b64":"$b64"}""") as StreamEvent.Audio
        assertEquals(2, audio.index)
        assertArrayEquals(bytes, audio.clip)
        val missing = StreamProtocol.parseLine("""{"type":"audio","index":0,"audio_b64":null}""") as StreamEvent.Audio
        assertNull(missing.clip)
    }

    @Test
    fun parsesDoneWithTtfa() {
        val done = StreamProtocol.parseLine("""{"type":"done","debug":{"ttfa_ms":1234.5}}""") as StreamEvent.Done
        assertEquals(1234L, done.ttfaMs)
        val none = StreamProtocol.parseLine("""{"type":"done","debug":{"ttfa_ms":null}}""") as StreamEvent.Done
        assertNull(none.ttfaMs)
    }

    @Test
    fun ignoresBlankUnknownAndBrokenLines() {
        assertNull(StreamProtocol.parseLine(""))
        assertNull(StreamProtocol.parseLine("   "))
        assertNull(StreamProtocol.parseLine("""{"type":"other"}"""))
        assertNull(StreamProtocol.parseLine("not json"))
    }
}
