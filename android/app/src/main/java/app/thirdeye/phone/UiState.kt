package app.thirdeye.phone

/** Operational state shown on the hero card. SETUP_NEEDED is computed by the UI. */
enum class UiState { SETUP_NEEDED, READY, LISTENING, LOOKING, SPEAKING, PROBLEM }

object UiStateLogic {
    /** Maps the service's raw state plus setup completeness to the hero state. */
    fun hero(serviceRunning: Boolean, setupComplete: Boolean, raw: UiState): UiState =
        if (!serviceRunning || !setupComplete) UiState.SETUP_NEEDED else raw
}
