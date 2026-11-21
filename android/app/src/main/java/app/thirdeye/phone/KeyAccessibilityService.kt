package app.thirdeye.phone

import android.accessibilityservice.AccessibilityService
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent

/** Forwards Volume Up presses to ThirdEyeService while it is running. */
class KeyAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        serviceInfo = serviceInfo.apply {
            flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_REQUEST_FILTER_KEY_EVENTS
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    override fun onKeyEvent(event: KeyEvent): Boolean {
        if (event.keyCode != KeyEvent.KEYCODE_VOLUME_UP) return false
        val service = ThirdEyeService.instance ?: return false
        when (event.action) {
            KeyEvent.ACTION_DOWN -> service.onKeyDown(event.repeatCount)
            KeyEvent.ACTION_UP -> service.onKeyUp()
            else -> return false
        }
        return true
    }
}
