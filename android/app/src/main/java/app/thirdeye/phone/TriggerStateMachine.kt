package app.thirdeye.phone

/**
 * Pure state machine for the Volume Up trigger. The service feeds key and
 * lifecycle events and executes the returned actions. All timing goes through
 * the injected clock and scheduler so the machine stays unit-testable.
 */
class TriggerStateMachine(
    private val now: () -> Long = System::currentTimeMillis,
    private val scheduler: Scheduler? = null,
) {
    interface Scheduler {
        fun schedule(delayMs: Long, event: KeyEvent)
        fun cancelAll()
    }

    sealed interface KeyEvent {
        data class KeyDown(val repeatCount: Int = 0) : KeyEvent
        data object KeyUp : KeyEvent
        data object HoldTimeout : KeyEvent
        data object RecordingCap : KeyEvent
        data object RequestFinished : KeyEvent
        data object PlaybackFinished : KeyEvent
    }

    sealed interface SmAction {
        /** Quick press (or too-short hold): take a photo and send it without audio. */
        data object TakePhotoThenSend : SmAction
        /** Hold reached 400 ms: take a photo now, vibrate and start recording. */
        data object StartHold : SmAction
        /** Hold released (or hit the 20 s cap): stop recording and send photo plus audio. */
        data object FinishHold : SmAction
        /** Service asked to abort everything and return to idle. */
        data object CancelAll : SmAction
        /** A fresh press arrived while a request is still uploading. */
        data object RejectBusy : SmAction
        /** A fresh press arrived during reply playback; stop playback, press continues. */
        data object InterruptPlayback : SmAction
    }

    private enum class State { IDLE, PRESSED, HOLDING, SENDING, PLAYING }

    private var state = State.IDLE
    private var holdStartedAt = 0L

    fun onEvent(event: KeyEvent): List<SmAction> {
        return when (state) {
            State.IDLE -> when (event) {
                is KeyEvent.KeyDown -> beginPress(event.repeatCount)
                else -> emptyList()
            }
            State.PRESSED -> when (event) {
                KeyEvent.KeyUp -> {
                    state = State.SENDING
                    scheduler?.cancelAll()
                    listOf(SmAction.TakePhotoThenSend)
                }
                KeyEvent.HoldTimeout -> {
                    state = State.HOLDING
                    holdStartedAt = now()
                    scheduler?.cancelAll()
                    scheduler?.schedule(RECORDING_CAP_MS, KeyEvent.RecordingCap)
                    listOf(SmAction.StartHold)
                }
                else -> emptyList()
            }
            State.HOLDING -> when (event) {
                KeyEvent.KeyUp -> releaseHold()
                KeyEvent.RecordingCap -> {
                    state = State.SENDING
                    scheduler?.cancelAll()
                    listOf(SmAction.FinishHold)
                }
                else -> emptyList()
            }
            State.SENDING -> when (event) {
                is KeyEvent.KeyDown -> if (event.repeatCount == 0) listOf(SmAction.RejectBusy) else emptyList()
                KeyEvent.RequestFinished -> {
                    state = State.PLAYING
                    emptyList()
                }
                else -> emptyList()
            }
            State.PLAYING -> when (event) {
                is KeyEvent.KeyDown -> if (event.repeatCount == 0) {
                    state = State.PRESSED
                    armHoldTimeout()
                    listOf(SmAction.InterruptPlayback)
                } else {
                    emptyList()
                }
                KeyEvent.PlaybackFinished -> {
                    state = State.IDLE
                    emptyList()
                }
                else -> emptyList()
            }
        }
    }

    /** Abort timers and return to idle; used when the service stops or resets. */
    fun cancel(): List<SmAction> {
        state = State.IDLE
        scheduler?.cancelAll()
        return listOf(SmAction.CancelAll)
    }

    private fun beginPress(repeatCount: Int): List<SmAction> {
        if (repeatCount != 0) return emptyList()
        state = State.PRESSED
        armHoldTimeout()
        return emptyList()
    }

    private fun armHoldTimeout() {
        scheduler?.cancelAll()
        scheduler?.schedule(HOLD_THRESHOLD_MS, KeyEvent.HoldTimeout)
    }

    private fun releaseHold(): List<SmAction> {
        state = State.SENDING
        scheduler?.cancelAll()
        return if (now() - holdStartedAt < MIN_AUDIO_MS) {
            listOf(SmAction.TakePhotoThenSend)
        } else {
            listOf(SmAction.FinishHold)
        }
    }

    companion object {
        const val HOLD_THRESHOLD_MS = 400L
        const val RECORDING_CAP_MS = 20_000L
        const val MIN_AUDIO_MS = 300L
    }
}
