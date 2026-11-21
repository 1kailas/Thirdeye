package app.thirdeye.phone

import java.util.TreeMap

class AudioPartQueue(
    private val player: Player,
    private val onFirstStart: () -> Unit,
    private val onFinished: (played: Boolean) -> Unit,
) {
    interface Player {
        fun play(clip: ByteArray, onDone: () -> Unit): Boolean
        fun stop()
    }

    private val pending = TreeMap<Int, ByteArray?>()
    private var next = 0
    private var total = -1
    private var playing = false
    private var started = false
    private var finished = false
    private var cancelled = false

    val played: Boolean get() = started

    fun expect(parts: Int) {
        total = parts
        advance()
    }

    fun offer(index: Int, clip: ByteArray?) {
        if (cancelled || finished || index < next) return
        pending[index] = clip
        advance()
    }

    fun done() {
        if (cancelled || finished) return
        total = next + pending.size
        advance()
    }

    fun cancel() {
        if (cancelled || finished) return
        cancelled = true
        pending.clear()
        player.stop()
    }

    private fun advance() {
        if (cancelled || finished || playing) return
        while (pending.containsKey(next)) {
            val clip = pending.remove(next)
            next++
            if (clip == null) continue
            playing = true
            val accepted = player.play(clip) {
                playing = false
                advance()
            }
            if (accepted) {
                if (!started) {
                    started = true
                    onFirstStart()
                }
                return
            }
            playing = false
        }
        if (total >= 0 && next >= total) {
            finished = true
            onFinished(started)
        }
    }
}
