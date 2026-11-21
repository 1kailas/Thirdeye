package app.thirdeye.phone

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.speech.tts.TextToSpeech
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var serverUrl: EditText
    private lateinit var token: EditText
    private lateinit var status: TextView
    private lateinit var accessibilityStatus: TextView
    private lateinit var startStop: Button
    private lateinit var prefs: SharedPreferences
    private var tts: TextToSpeech? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        val missing = grants.filterValues { !it }.keys
        say(if (missing.isEmpty()) "Permissions granted." else "Still missing: ${missing.joinToString()}")
        refresh()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = getSharedPreferences(ThirdEyeService.PREFS, Context.MODE_PRIVATE)
        serverUrl = findViewById(R.id.serverUrl)
        token = findViewById(R.id.token)
        status = findViewById(R.id.status)
        accessibilityStatus = findViewById(R.id.accessibilityStatus)
        startStop = findViewById(R.id.startStopService)
        serverUrl.setText(prefs.getString("server_url", ""))
        token.setText(prefs.getString("token", ""))
        tts = TextToSpeech(this) { result ->
            if (result == TextToSpeech.SUCCESS) {
                val engine = tts ?: return@TextToSpeech
                val malayalam = engine.setLanguage(Locale.forLanguageTag("ml-IN"))
                if (malayalam == TextToSpeech.LANG_MISSING_DATA || malayalam == TextToSpeech.LANG_NOT_SUPPORTED) {
                    engine.language = Locale.getDefault()
                }
            }
        }
        findViewById<Button>(R.id.grantPermissions).setOnClickListener { requestPermissions() }
        findViewById<Button>(R.id.allowRestricted).setOnClickListener { openAppInfo() }
        findViewById<Button>(R.id.openAccessibility).setOnClickListener { openAccessibility() }
        startStop.setOnClickListener { toggleService() }
        findViewById<Button>(R.id.testConnection).setOnClickListener { testConnection() }
        findViewById<Button>(R.id.debugHistory).setOnClickListener {
            startActivity(Intent(this, HistoryActivity::class.java))
        }
        findViewById<Button>(R.id.testButton).setOnTouchListener(testTouch)
    }

    override fun onResume() {
        super.onResume()
        persist()
        refresh()
        ThirdEyeService.instance?.lastStatus()?.takeIf { it.isNotEmpty() }
            ?.let { status.text = it }
    }

    override fun onDestroy() {
        tts?.shutdown()
        tts = null
        super.onDestroy()
    }

    private fun persist() {
        prefs.edit()
            .putString("server_url", serverUrl.text.toString().trim())
            .putString("token", token.text.toString().trim())
            .apply()
    }

    private fun refresh() {
        val enabled = accessibilityEnabled()
        accessibilityStatus.text = if (enabled) "Key service: on" else RESTRICTED_GUIDANCE
        val running = ThirdEyeService.instance != null
        startStop.text = if (running) "Stop service" else "Start service"
        startStop.contentDescription = startStop.text
    }

    private fun accessibilityEnabled(): Boolean {
        val component = ComponentName(this, KeyAccessibilityService::class.java)
        val names = setOf(component.flattenToString(), component.flattenToShortString())
            .map { it.lowercase() }.toSet()
        val enabled = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
        return enabled?.split(':')?.any { it.trim().lowercase() in names } == true
    }

    private fun requestPermissions() {
        val wanted = mutableListOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) wanted += Manifest.permission.POST_NOTIFICATIONS
        val missing = wanted.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) say("All permissions already granted.") else permissionLauncher.launch(missing.toTypedArray())
    }

    private fun permissionsGranted(): Boolean {
        val needed = mutableListOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) needed += Manifest.permission.POST_NOTIFICATIONS
        return needed.all {
            ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
        }
    }

    private fun toggleService() {
        persist()
        if (ThirdEyeService.instance != null) {
            startService(Intent(this, ThirdEyeService::class.java).setAction(ThirdEyeService.ACTION_STOP))
            say("Service stopped.")
            return
        }
        if (!permissionsGranted()) {
            say("Grant camera, microphone and notification permissions first.")
            requestPermissions()
            return
        }
        val url = prefs.getString("server_url", "").orEmpty()
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            say("Enter a server address starting with http:// or https://")
            return
        }
        ContextCompat.startForegroundService(this, Intent(this, ThirdEyeService::class.java))
        if (accessibilityEnabled()) {
            say("Service started. Use Volume Up, or the on-screen Test button.")
        } else {
            say("Service started. Volume Up needs the accessibility key service; the on-screen Test button works without it.")
        }
    }

    private fun openAppInfo() {
        startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.fromParts("package", packageName, null)))
    }

    private fun openAccessibility() {
        if (!accessibilityEnabled()) say(RESTRICTED_GUIDANCE)
        startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
    }

    private fun testConnection() {
        persist()
        val url = prefs.getString("server_url", "").orEmpty()
        val key = prefs.getString("token", "").orEmpty()
        if (!url.startsWith("http://") && !url.startsWith("https://") || key.isEmpty()) {
            say("Set a valid server address and token first.")
            return
        }
        say("Testing connection…")
        Thread {
            val result = PhoneApi(url, key).ping()
            val message = when {
                result == null -> "Server unreachable. Is the PC app running?"
                result.ok -> "Connected. AI ready: ${if (result.aiReady) "yes" else "no key"}."
                else -> "Server answered HTTP ${result.httpCode} but rejected the token."
            }
            runOnUiThread {
                status.text = message
                say(message)
            }
        }.start()
    }

    private fun say(text: String) {
        status.text = text
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "ui")
    }

    private val testTouch = View.OnTouchListener { _, event ->
        val service = ThirdEyeService.instance
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                if (service == null) say("Start the service first.") else service.onKeyDown(0)
                true
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                service?.onKeyUp()
                true
            }
            else -> false
        }
    }

    private companion object {
        const val RESTRICTED_GUIDANCE = "Key service: off. If the service is greyed out in " +
            "Accessibility settings: tap Allow restricted settings, then in the App info page " +
            "open the ⋮ menu (top right) and choose Allow restricted settings, then come back " +
            "and enable DrishtiGuide."
    }
}
