package app.thirdeye.phone

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.media.MediaPlayer
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.speech.tts.TextToSpeech
import android.text.Editable
import android.text.TextWatcher
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.snackbar.Snackbar
import java.io.File
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var serverUrl: EditText
    private lateinit var token: EditText
    private lateinit var startStop: Button
    private lateinit var prefs: SharedPreferences
    private var tts: TextToSpeech? = null
    private var player: MediaPlayer? = null
    private var subscribedService: ThirdEyeService? = null
    private var lastAnnounced: UiState? = null
    private var persistJob: Runnable? = null

    private val heroIcon: ImageView by lazy { findViewById(R.id.heroIcon) }
    private val heroState: TextView by lazy { findViewById(R.id.heroState) }
    private val heroExplain: TextView by lazy { findViewById(R.id.heroExplain) }
    private val permIcon: ImageView by lazy { findViewById(R.id.permIcon) }
    private val permStatus: TextView by lazy { findViewById(R.id.permStatus) }
    private val keyIcon: ImageView by lazy { findViewById(R.id.keyIcon) }
    private val keyStatus: TextView by lazy { findViewById(R.id.keyStatus) }
    private val linkIcon: ImageView by lazy { findViewById(R.id.linkIcon) }
    private val linkStatus: TextView by lazy { findViewById(R.id.linkStatus) }
    private val svcIcon: ImageView by lazy { findViewById(R.id.svcIcon) }
    private val svcStatus: TextView by lazy { findViewById(R.id.svcStatus) }
    private val connStatus: TextView by lazy { findViewById(R.id.connStatus) }
    private val lastTranscript: TextView by lazy { findViewById(R.id.lastTranscript) }
    private val lastReply: TextView by lazy { findViewById(R.id.lastReply) }
    private val replayAnswer: Button by lazy { findViewById(R.id.replayAnswer) }

    private val stateListener: (UiState) -> Unit = { state ->
        runOnUiThread { renderHero(state) }
    }

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
        val watcher = object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) { schedulePersist() }
            override fun afterTextChanged(s: Editable?) {}
        }
        serverUrl.addTextChangedListener(watcher)
        token.addTextChangedListener(watcher)
        findViewById<Button>(R.id.permAction).setOnClickListener { requestPermissions() }
        findViewById<Button>(R.id.keyAction).setOnClickListener { openAccessibility() }
        findViewById<Button>(R.id.allowRestricted).setOnClickListener { openAppInfo() }
        findViewById<Button>(R.id.linkAction).setOnClickListener { onLinkAction() }
        startStop.setOnClickListener { toggleService() }
        findViewById<Button>(R.id.pastePairing).setOnClickListener { pastePairingLink() }
        findViewById<Button>(R.id.testConnection).setOnClickListener { testConnection() }
        findViewById<Button>(R.id.openHistory).setOnClickListener {
            startActivity(Intent(this, HistoryActivity::class.java))
        }
        replayAnswer.setOnClickListener { replayLastAnswer() }
        findViewById<View>(R.id.testButton).setOnTouchListener(testTouch)
        handlePairing(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handlePairing(intent)
    }

    override fun onStart() {
        super.onStart()
        val service = ThirdEyeService.instance
        if (service != subscribedService) {
            subscribedService?.removeStateListener(stateListener)
            subscribedService = service
            service?.addStateListener(stateListener)
        }
    }

    override fun onStop() {
        subscribedService?.removeStateListener(stateListener)
        subscribedService = null
        super.onStop()
    }

    override fun onResume() {
        super.onResume()
        persist()
        refresh()
        refreshLastAnswer()
    }

    override fun onDestroy() {
        player?.release()
        player = null
        tts?.shutdown()
        tts = null
        super.onDestroy()
    }

    // ---- pairing ----

    private fun pastePairingLink() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? android.content.ClipboardManager
        val text = clipboard?.primaryClip?.getItemAt(0)?.text?.toString()
        val pairing = PairingLink.parse(text)
        if (pairing == null) {
            say("No pairing link found on the clipboard. Copy the link text from the pairing page first.")
            return
        }
        serverUrl.setText(pairing.baseUrl)
        token.setText(pairing.token)
        persist()
        say("Paired with your computer. Tap Start service.")
    }

    private fun handlePairing(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme != "thirdeye") return
        val pairing = PairingLink.parse(data.toString())
        if (pairing == null) {
            say("Pairing link is invalid. Scan the QR code on the computer again.")
            return
        }
        serverUrl.setText(pairing.baseUrl)
        token.setText(pairing.token)
        persist()
        say("Paired with your computer. Tap Start service.")
    }

    // ---- persistence ----

    private fun schedulePersist() {
        persistJob?.let { findViewById<View>(android.R.id.content).removeCallbacks(it) }
        val job = Runnable { persist() }
        persistJob = job
        findViewById<View>(android.R.id.content).postDelayed(job, 500)
    }

    private fun persist() {
        persistJob?.let { findViewById<View>(android.R.id.content).removeCallbacks(it) }
        persistJob = null
        prefs.edit()
            .putString("server_url", serverUrl.text.toString().trim())
            .putString("token", token.text.toString().trim())
            .apply()
    }

    // ---- refresh / checklist ----

    private fun linkConfigured(): Boolean {
        val url = prefs.getString("server_url", "").orEmpty()
        val key = prefs.getString("token", "").orEmpty()
        return (url.startsWith("http://") || url.startsWith("https://")) && key.isNotEmpty()
    }

    private fun setupComplete(): Boolean =
        permissionsGranted() && accessibilityEnabled() && linkConfigured()

    private fun refresh() {
        val perms = permissionsGranted()
        permStatus.text = getString(if (perms) R.string.status_done else R.string.status_missing)
        permIcon.setImageResource(if (perms) R.drawable.ic_check else R.drawable.ic_warn)

        val keyOn = accessibilityEnabled()
        keyStatus.text = if (keyOn) getString(R.string.status_done)
            else getString(R.string.status_missing)
        keyIcon.setImageResource(if (keyOn) R.drawable.ic_check else R.drawable.ic_warn)

        val linked = linkConfigured()
        linkStatus.text = getString(if (linked) R.string.status_done else R.string.status_missing)
        linkIcon.setImageResource(if (linked) R.drawable.ic_check else R.drawable.ic_warn)
        findViewById<Button>(R.id.linkAction).text =
            getString(if (linked) R.string.action_edit else R.string.action_pair)

        val running = ThirdEyeService.instance != null
        svcStatus.text = getString(if (running) R.string.status_running else R.string.status_stopped)
        svcIcon.setImageResource(if (running) R.drawable.ic_check else R.drawable.ic_warn)
        startStop.text = getString(if (running) R.string.action_stop else R.string.action_start)
        startStop.contentDescription = startStop.text

        renderHero(ThirdEyeService.instance?.uiState ?: UiState.READY)
    }

    private fun onLinkAction() {
        if (linkConfigured()) {
            say("Edit the server address and token below, then test the connection.")
            serverUrl.requestFocus()
        } else {
            pastePairingLink()
        }
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
        if (!accessibilityEnabled()) {
            keyStatus.text = RESTRICTED_GUIDANCE
            say(RESTRICTED_GUIDANCE)
        }
        startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
    }

    // ---- connection test ----

    private fun testConnection() {
        persist()
        val url = prefs.getString("server_url", "").orEmpty()
        val key = prefs.getString("token", "").orEmpty()
        if (!url.startsWith("http://") && !url.startsWith("https://") || key.isEmpty()) {
            say("Set a valid server address and token first.")
            return
        }
        connStatus.visibility = View.VISIBLE
        connStatus.text = getString(R.string.conn_testing)
        say("Testing connection…")
        Thread {
            val result = PhoneApi(url, key).ping()
            runOnUiThread {
                when {
                    result == null -> {
                        connStatus.text = "● " + getString(R.string.conn_unreachable)
                        connStatus.setTextColor(ContextCompat.getColor(this, R.color.error_light))
                        say("Server unreachable. Is the PC app running?")
                    }
                    result.httpCode == 401 -> {
                        connStatus.text = "● " + getString(R.string.conn_token_rejected)
                        connStatus.setTextColor(ContextCompat.getColor(this, R.color.brand_accent))
                        say("Server answered but rejected the token.")
                    }
                    result.ok -> {
                        connStatus.text = "● " + getString(
                            if (result.aiReady) R.string.conn_connected else R.string.conn_ai_not_ready)
                        connStatus.setTextColor(ContextCompat.getColor(this, R.color.success_light))
                        say("Connected. AI ready: ${if (result.aiReady) "yes" else "no key"}.")
                    }
                    else -> {
                        connStatus.text = "● " + getString(R.string.conn_unreachable)
                        connStatus.setTextColor(ContextCompat.getColor(this, R.color.error_light))
                        say("Server answered HTTP ${result.httpCode}.")
                    }
                }
            }
        }.start()
    }

    // ---- hero / last answer ----

    private fun renderHero(raw: UiState) {
        val state = UiStateLogic.hero(ThirdEyeService.instance != null, setupComplete(), raw)
        val (iconRes, stateRes, explainRes) = when (state) {
            UiState.SETUP_NEEDED -> Triple(R.drawable.ic_hero_setup, R.string.state_setup, R.string.state_setup_explain)
            UiState.READY -> Triple(R.drawable.ic_hero_ready, R.string.state_ready, R.string.state_ready_explain)
            UiState.LISTENING -> Triple(R.drawable.ic_hero_listening, R.string.state_listening, R.string.state_listening_explain)
            UiState.LOOKING -> Triple(R.drawable.ic_hero_looking, R.string.state_looking, R.string.state_looking_explain)
            UiState.SPEAKING -> Triple(R.drawable.ic_hero_speaking, R.string.state_speaking, R.string.state_speaking_explain)
            UiState.PROBLEM -> Triple(R.drawable.ic_hero_problem, R.string.state_problem, R.string.state_problem_explain)
        }
        heroIcon.setImageResource(iconRes)
        heroState.text = getString(stateRes)
        heroExplain.text = getString(explainRes)
        if (state == UiState.PROBLEM) {
            ThirdEyeService.instance?.lastStatus()?.takeIf { it.isNotEmpty() }?.let {
                heroExplain.text = it.take(120)
            }
        }
        if (state != lastAnnounced) {
            lastAnnounced = state
            heroState.announceForAccessibility(heroState.text)
        }
    }

    private fun refreshLastAnswer() {
        val text = ThirdEyeService.instance?.lastStatus().orEmpty()
        if (text.isEmpty()) {
            lastTranscript.text = getString(R.string.last_answer_empty)
            lastReply.text = ""
            replayAnswer.visibility = View.GONE
            return
        }
        val newline = text.indexOf('\n')
        if (newline >= 0) {
            lastTranscript.text = getString(R.string.you_prefix) + text.substring(0, newline)
            lastReply.text = text.substring(newline + 1)
        } else {
            lastTranscript.text = ""
            lastReply.text = text
        }
        val hasAudio = History(File(filesDir, "history")).list().firstOrNull()
            ?.let { History(File(filesDir, "history")).audioFile(it) }?.isFile == true
        replayAnswer.visibility = if (hasAudio) View.VISIBLE else View.GONE
    }

    private fun replayLastAnswer() {
        val turn = History(File(filesDir, "history")).list().firstOrNull()
        val audio = turn?.let { History(File(filesDir, "history")).audioFile(it) }
        if (audio == null || !audio.isFile) {
            say("No recording to replay.")
            return
        }
        player?.release()
        player = try {
            MediaPlayer().apply {
                setDataSource(audio.absolutePath)
                setOnCompletionListener { release() }
                prepare()
                start()
            }
        } catch (_: Exception) {
            say("Could not play the recording.")
            null
        }
    }

    // ---- misc ----

    private fun say(text: String) {
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "ui")
        Snackbar.make(findViewById(android.R.id.content), text, Snackbar.LENGTH_LONG).show()
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
