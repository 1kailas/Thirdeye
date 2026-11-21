package app.thirdeye.phone

import org.junit.Assert.assertEquals
import org.junit.Test

class UiStateLogicTest {

    @Test
    fun setupNeededWhenServiceOrSetupIncomplete() {
        assertEquals(UiState.SETUP_NEEDED,
            UiStateLogic.hero(serviceRunning = false, setupComplete = true, raw = UiState.READY))
        assertEquals(UiState.SETUP_NEEDED,
            UiStateLogic.hero(serviceRunning = true, setupComplete = false, raw = UiState.LISTENING))
    }

    @Test
    fun rawStatePassesThroughWhenReady() {
        for (state in listOf(UiState.READY, UiState.LISTENING, UiState.LOOKING,
            UiState.SPEAKING, UiState.PROBLEM)) {
            assertEquals(state, UiStateLogic.hero(true, true, state))
        }
    }
}
