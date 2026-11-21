package app.thirdeye.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioPartQueueTest {

    private class FakePlayer(private val rejectFirstByte: Int? = null) : AudioPartQueue.Player {
        val played = mutableListOf<Int>()
        var stopped = 0
        private var finish: (() -> Unit)? = null

        override fun play(clip: ByteArray, onDone: () -> Unit): Boolean {
            if (clip[0].toInt() == rejectFirstByte) return false
            played += clip[0].toInt()
            finish = onDone
            return true
        }

        override fun stop() {
            stopped++
            finish = null
        }

        fun complete() {
            val callback = finish ?: return
            finish = null
            callback()
        }
    }

    private fun clip(n: Int) = byteArrayOf(n.toByte())

    private fun queue(player: FakePlayer, events: MutableList<String>) =
        AudioPartQueue(player, { events += "first" }, { played -> events += "finished:$played" })

    @Test
    fun playsPartsInOrderEvenWhenOfferedOutOfOrder() {
        val player = FakePlayer()
        val events = mutableListOf<String>()
        val q = queue(player, events)
        q.expect(3)
        q.offer(1, clip(1))
        assertTrue(player.played.isEmpty())
        q.offer(0, clip(0))
        assertEquals(listOf(0), player.played)
        player.complete()
        assertEquals(listOf(0, 1), player.played)
        q.offer(2, clip(2))
        player.complete()
        assertEquals(listOf(0, 1, 2), player.played)
        player.complete()
        assertEquals(listOf("first", "finished:true"), events)
    }

    @Test
    fun firstStartFiresOnceAtFirstPlayedPart() {
        val player = FakePlayer()
        val events = mutableListOf<String>()
        val q = queue(player, events)
        q.expect(2)
        q.offer(0, null)
        assertTrue(events.isEmpty())
        q.offer(1, clip(1))
        assertEquals(listOf("first"), events)
        player.complete()
        assertEquals(listOf("first", "finished:true"), events)
    }

    @Test
    fun failedAndRejectedPartsAreSkipped() {
        val player = FakePlayer(rejectFirstByte = 1)
        val events = mutableListOf<String>()
        val q = queue(player, events)
        q.expect(3)
        q.offer(0, clip(0))
        q.offer(1, clip(1))
        q.offer(2, clip(2))
        player.complete()
        assertEquals(listOf(0, 2), player.played)
        player.complete()
        assertEquals(listOf("first", "finished:true"), events)
    }

    @Test
    fun noPlayableAudioFinishesWithPlayedFalse() {
        val player = FakePlayer()
        val events = mutableListOf<String>()
        val q = queue(player, events)
        q.expect(2)
        q.offer(0, null)
        q.offer(1, null)
        assertEquals(listOf("finished:false"), events)
        assertFalse(q.played)
    }

    @Test
    fun zeroPartsFinishesImmediatelyForLocalFallback() {
        val events = mutableListOf<String>()
        val q = queue(FakePlayer(), events)
        q.expect(0)
        assertEquals(listOf("finished:false"), events)
    }

    @Test
    fun doneEndsQueueWhenMetaPartCountIsUnknown() {
        val player = FakePlayer()
        val events = mutableListOf<String>()
        val q = queue(player, events)
        q.offer(0, clip(0))
        player.complete()
        assertTrue(events == listOf("first"))
        q.done()
        assertEquals(listOf("first", "finished:true"), events)
    }

    @Test
    fun cancelStopsPlaybackAndSuppressesCallbacks() {
        val player = FakePlayer()
        val events = mutableListOf<String>()
        val q = queue(player, events)
        q.expect(2)
        q.offer(0, clip(0))
        q.cancel()
        assertEquals(1, player.stopped)
        q.offer(1, clip(1))
        q.done()
        assertEquals(listOf("first"), events)
        assertEquals(listOf(0), player.played)
    }
}
