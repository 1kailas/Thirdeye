package app.thirdeye.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TriggerStateMachineTest {

    private class ManualScheduler : TriggerStateMachine.Scheduler {
        val scheduled = mutableListOf<Pair<Long, TriggerStateMachine.KeyEvent>>()
        val pending = mutableListOf<Pair<Long, TriggerStateMachine.KeyEvent>>()
        override fun schedule(delayMs: Long, event: TriggerStateMachine.KeyEvent) {
            scheduled += delayMs to event
            pending += delayMs to event
        }
        override fun cancelAll() {
            pending.clear()
        }
    }

    private var now = 1_000L
    private lateinit var scheduler: ManualScheduler
    private lateinit var sm: TriggerStateMachine

    private fun setup() {
        now = 1_000L
        scheduler = ManualScheduler()
        sm = TriggerStateMachine(now = { now }, scheduler = scheduler)
    }

    private fun down(repeat: Int = 0) = sm.onEvent(TriggerStateMachine.KeyEvent.KeyDown(repeat))
    private fun up() = sm.onEvent(TriggerStateMachine.KeyEvent.KeyUp)
    private fun timeout() = sm.onEvent(TriggerStateMachine.KeyEvent.HoldTimeout)
    private fun cap() = sm.onEvent(TriggerStateMachine.KeyEvent.RecordingCap)
    private fun finished() = sm.onEvent(TriggerStateMachine.KeyEvent.RequestFinished)
    private fun played() = sm.onEvent(TriggerStateMachine.KeyEvent.PlaybackFinished)

    @Test
    fun quickTapSendsPhotoOnly() {
        setup()
        assertTrue(down().isEmpty())
        assertEquals(listOf(TriggerStateMachine.SmAction.TakePhotoThenSend), up())
        assertEquals(TriggerStateMachine.HOLD_THRESHOLD_MS, scheduler.scheduled.single().first)
        assertTrue(down().first() is TriggerStateMachine.SmAction.RejectBusy)
    }

    @Test
    fun holdStartsRecordingThenSendsAudio() {
        setup()
        down()
        assertEquals(listOf(TriggerStateMachine.SmAction.StartHold), timeout())
        assertEquals(TriggerStateMachine.RECORDING_CAP_MS, scheduler.scheduled.last().first)
        now += 2_000
        assertEquals(listOf(TriggerStateMachine.SmAction.FinishHold), up())
    }

    @Test
    fun keyRepeatIsIgnored() {
        setup()
        assertTrue(down(repeat = 1).isEmpty())
        down()
        assertTrue(down(repeat = 2).isEmpty())
        assertEquals(listOf(TriggerStateMachine.SmAction.TakePhotoThenSend), up())
    }

    @Test
    fun recordingCapAutoFinishesAndLaterReleaseIsIgnored() {
        setup()
        down()
        timeout()
        now += TriggerStateMachine.RECORDING_CAP_MS
        assertEquals(listOf(TriggerStateMachine.SmAction.FinishHold), cap())
        assertTrue(up().isEmpty())
        assertTrue(down().first() is TriggerStateMachine.SmAction.RejectBusy)
    }

    @Test
    fun shortRecordingFallsBackToPhotoOnly() {
        setup()
        down()
        timeout()
        now += TriggerStateMachine.MIN_AUDIO_MS - 1
        assertEquals(listOf(TriggerStateMachine.SmAction.TakePhotoThenSend), up())
    }

    @Test
    fun pressDuringUploadIsRejected() {
        setup()
        down()
        up()
        assertEquals(listOf(TriggerStateMachine.SmAction.RejectBusy), down())
        assertTrue(up().isEmpty())
        finished()
        played()
        assertTrue(down().isEmpty())
        assertEquals(listOf(TriggerStateMachine.SmAction.TakePhotoThenSend), up())
    }

    @Test
    fun pressDuringPlaybackInterruptsThenContinuesPress() {
        setup()
        down()
        up()
        finished()
        assertEquals(listOf(TriggerStateMachine.SmAction.InterruptPlayback), down())
        assertEquals(listOf(TriggerStateMachine.SmAction.TakePhotoThenSend), up())
    }

    @Test
    fun holdAfterInterruptingPlaybackRecords() {
        setup()
        down()
        up()
        finished()
        down()
        timeout()
        now += 1_500
        assertEquals(listOf(TriggerStateMachine.SmAction.FinishHold), up())
    }

    @Test
    fun quickReleaseCancelsPendingHoldTimeout() {
        setup()
        down()
        assertEquals(1, scheduler.pending.size)
        up()
        assertTrue(scheduler.pending.isEmpty())
    }

    @Test
    fun holdReleaseCancelsPendingRecordingCap() {
        setup()
        down()
        timeout()
        assertEquals(1, scheduler.pending.size)
        now += 1_000
        up()
        assertTrue(scheduler.pending.isEmpty())
    }

    @Test
    fun recordingCapClearsPendingTimers() {
        setup()
        down()
        timeout()
        now += TriggerStateMachine.RECORDING_CAP_MS
        cap()
        assertTrue(scheduler.pending.isEmpty())
    }

    @Test
    fun interruptingPlaybackRearmsFreshTimer() {
        setup()
        down()
        up()
        finished()
        down()
        assertEquals(1, scheduler.pending.size)
        assertEquals(TriggerStateMachine.HOLD_THRESHOLD_MS, scheduler.pending.single().first)
    }

    @Test
    fun cancelClearsPendingTimers() {
        setup()
        down()
        timeout()
        sm.cancel()
        assertTrue(scheduler.pending.isEmpty())
    }

    @Test
    fun cancelReturnsToIdle() {
        setup()
        down()
        timeout()
        assertEquals(listOf(TriggerStateMachine.SmAction.CancelAll), sm.cancel())
        assertTrue(down().isEmpty())
        assertEquals(listOf(TriggerStateMachine.SmAction.TakePhotoThenSend), up())
    }
}
