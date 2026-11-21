package app.thirdeye.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class HistoryTest {

    @get:Rule
    val temp = TemporaryFolder()

    private fun history(limit: Int = 20) = History(File(temp.root, "history"), limit)

    @Test
    fun trimsToLimit() {
        val h = history(limit = 5)
        repeat(8) { i ->
            h.add(byteArrayOf(i.toByte()), null, null, "t$i", "r$i", "general", 200, true, 10, null)
            Thread.sleep(2)
        }
        val turns = h.list()
        assertEquals(5, turns.size)
        assertEquals("r7", turns[0].reply)
        assertEquals("r3", turns[4].reply)
        assertTrue(h.imageFile(turns[0]).isFile)
    }

    @Test
    fun jsonRoundTrip() {
        val h = history()
        val wav = byteArrayOf(1, 2, 3, 4)
        h.add(byteArrayOf(9, 9), wav, "wav", "question?", "reply 텍스트", "guide",
            200, true, 1234, null)
        val turns = h.list()
        assertEquals(1, turns.size)
        val turn = turns[0]
        assertEquals("question?", turn.transcript)
        assertEquals("reply 텍스트", turn.reply)
        assertEquals("guide", turn.route)
        assertEquals(200, turn.httpCode)
        assertTrue(turn.ok)
        assertEquals(1234, turn.elapsedMs)
        assertNull(turn.error)
        assertNotNull(turn.audioName)
        assertTrue(h.imageFile(turn).isFile)
        assertTrue(h.audioFile(turn)!!.isFile)
        assertTrue(h.audioFile(turn)!!.readBytes().contentEquals(wav))
    }

    @Test
    fun failureTurnHasErrorAndNoAudio() {
        val h = history()
        h.add(byteArrayOf(1), null, null, null, "", null, 0, false, 5000, "timeout")
        val turn = h.list().single()
        assertEquals("timeout", turn.error)
        assertFalse(turn.ok)
        assertNull(turn.audioName)
        assertNull(turn.transcript)
        assertNull(turn.route)
    }

    @Test
    fun clearRemovesEverything() {
        val h = history()
        h.add(byteArrayOf(1), byteArrayOf(2), "wav", null, "r", null, 200, true, 1, null)
        assertEquals(1, h.list().size)
        h.clear()
        assertTrue(h.list().isEmpty())
    }
}
