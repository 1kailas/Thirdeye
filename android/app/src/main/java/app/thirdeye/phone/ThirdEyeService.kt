package app.thirdeye.phone

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.Matrix
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Base64
import androidx.annotation.StringRes
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Locale
import java.util.concurrent.Executors
import kotlin.coroutines.resume

class ThirdEyeService : LifecycleService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val smHandler = Handler(Looper.getMainLooper())
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private var sm: TriggerStateMachine? = null
    private lateinit var prefs: SharedPreferences
    private lateinit var api: PhoneApi
    private lateinit var history: History

    private var tts: TextToSpeech? = null
    @Volatile private var malayalamTtsReady = false
    private var mediaPlayer: MediaPlayer? = null
    private var audioManager: AudioManager? = null
    private var audioFocusRequest: android.media.AudioFocusRequest? = null
    private var tickGenerator: android.media.ToneGenerator? = null
    private var waitTicker: Runnable? = null
    private var watchdogTicker: Runnable? = null

    @Volatile var uiState: UiState = UiState.READY
        private set
    private val uiListeners = java.util.concurrent.CopyOnWriteArrayList<(UiState) -> Unit>()

    fun addStateListener(listener: (UiState) -> Unit) { uiListeners.add(listener) }
    fun removeStateListener(listener: (UiState) -> Unit) { uiListeners.remove(listener) }

    private fun setUiState(state: UiState) {
        if (uiState == state) return
        uiState = state
        uiListeners.forEach { it(state) }
    }

    private var photoJob: Deferred<ByteArray?>? = null
    private val cameraMutex = Mutex()
    private var prewarmExpiry: Runnable? = null
    private var requestSeq = 0
    private var partQueue: AudioPartQueue? = null
    private var capture: ImageCapture? = null
    private var cameraProvider: ProcessCameraProvider? = null

    private var recorder: AudioRecord? = null
    private var recordThread: Thread? = null
    @Volatile private var recording = false
    private val pcm = ByteArrayOutputStream()
    private var recordStartedAt = 0L

    private var lastStatus = ""

    private val smScheduler = object : TriggerStateMachine.Scheduler {
        override fun schedule(delayMs: Long, event: TriggerStateMachine.KeyEvent) {
            smHandler.postDelayed({ onSmEvent(event) }, delayMs)
        }
        override fun cancelAll() {
            smHandler.removeCallbacksAndMessages(null)
        }
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        api = PhoneApi(serverUrl(), token())
        history = History(java.io.File(filesDir, "history"))
        sm = TriggerStateMachine(scheduler = smScheduler)
        audioManager = getSystemService(AUDIO_SERVICE) as AudioManager
        tts = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                val engine = tts ?: return@TextToSpeech
                malayalamTtsReady = engine.setLanguage(Locale.forLanguageTag("ml-IN")) >= TextToSpeech.LANG_AVAILABLE
            }
        }
        tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}
            override fun onDone(utteranceId: String?) {
                if (utteranceId?.startsWith("reply") == true || utteranceId?.startsWith("error") == true) {
                    onSmEvent(TriggerStateMachine.KeyEvent.PlaybackFinished)
                }
            }
            @Deprecated("deprecated")
            override fun onError(utteranceId: String?) {
                onSmEvent(TriggerStateMachine.KeyEvent.PlaybackFinished)
            }
        })
        createChannel()
        startForegroundWithType()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            shutdown()
            return START_NOT_STICKY
        }
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        api = PhoneApi(serverUrl(), token())
        return START_STICKY
    }

    override fun onDestroy() {
        shutdown()
        instance = null
        super.onDestroy()
    }

    private fun shutdown() {
        sm?.cancel()?.forEach(::executeAction)
        stopRecordingInternal()
        releasePlayer()
        unbindCamera()
        tts?.shutdown()
        tts = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    // ---- key entry points (called by KeyAccessibilityService and MainActivity) ----

    fun onKeyDown(repeatCount: Int) {
        tick()
        if (repeatCount == 0) prewarmCamera()
        onSmEvent(TriggerStateMachine.KeyEvent.KeyDown(repeatCount))
    }

    fun onKeyUp() {
        onSmEvent(TriggerStateMachine.KeyEvent.KeyUp)
    }

    private fun onSmEvent(event: TriggerStateMachine.KeyEvent) {
        mainHandler.post {
            val actions = sm?.onEvent(event).orEmpty()
            actions.forEach(::executeAction)
            if (event == TriggerStateMachine.KeyEvent.PlaybackFinished) setUiState(UiState.READY)
        }
    }

    private fun executeAction(action: TriggerStateMachine.SmAction) {
        when (action) {
            TriggerStateMachine.SmAction.TakePhotoThenSend -> requestJob {
                if (recording) stopRecordingInternal()
                setUiState(UiState.LOOKING)
                val jpeg = photoJob?.await() ?: capturePhoto()
                photoJob = null
                if (jpeg == null) {
                    settleWithError(R.string.err_no_photo_ml)
                } else {
                    clickTone()
                    send(jpeg, null)
                }
            }
            TriggerStateMachine.SmAction.StartHold -> {
                setUiState(UiState.LISTENING)
                vibrateHold()
                startRecording()
                photoJob = scope.async { capturePhoto() }
            }
            TriggerStateMachine.SmAction.FinishHold -> requestJob {
                setUiState(UiState.LOOKING)
                val wav = stopRecording()
                val jpeg = photoJob?.await() ?: capturePhoto()
                photoJob = null
                if (jpeg == null) {
                    settleWithError(R.string.err_no_photo_ml)
                } else {
                    send(jpeg, wav)
                }
            }
            TriggerStateMachine.SmAction.RejectBusy -> errorTone()
            TriggerStateMachine.SmAction.InterruptPlayback -> {
                cancelStream()
                releasePlayer()
            }
            TriggerStateMachine.SmAction.CancelAll -> {
                setUiState(UiState.READY)
                stopRecordingInternal()
                stopWaitTicker()
                stopWatchdog()
                photoJob?.cancel()
                photoJob = null
                cancelStream()
                releasePlayer()
                unbindCamera()
            }
        }
    }

    // ---- request pipeline ----

    private fun requestJob(block: suspend () -> Unit) {
        scope.launch {
            try {
                block()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: Throwable) {
                settleWithError(R.string.err_network_ml)
            }
        }
    }

    private fun cancelStream() {
        requestSeq++
        partQueue?.cancel()
        partQueue = null
        api.cancel()
    }

    private fun send(imageJpeg: ByteArray, audioWav: ByteArray?) {
        val startedAt = System.currentTimeMillis()
        val requestId = ++requestSeq
        scope.launch {
            var firstSoundMs: Long? = null
            var streamReply = ""
            val queue = AudioPartQueue(partPlayer, onFirstStart = {
                firstSoundMs = System.currentTimeMillis() - startedAt
                stopWaitTicker()
                stopWatchdog()
                setUiState(UiState.SPEAKING)
                onSmEvent(TriggerStateMachine.KeyEvent.RequestFinished)
            }, onFinished = { played ->
                abandonAudioFocus()
                if (played) {
                    onSmEvent(TriggerStateMachine.KeyEvent.PlaybackFinished)
                } else {
                    stopWaitTicker()
                    stopWatchdog()
                    setUiState(UiState.SPEAKING)
                    onSmEvent(TriggerStateMachine.KeyEvent.RequestFinished)
                    speakLocal("reply", streamReply)
                }
            })
            partQueue = queue
            try {
                sendTone()
                startWaitTicker()
                startWatchdog()
                val listener = object : PhoneApi.StreamListener {
                    override fun onMeta(meta: StreamEvent.Meta) {
                        mainHandler.post {
                            streamReply = meta.reply
                            queue.expect(meta.parts)
                        }
                    }

                    override fun onAudio(index: Int, clip: ByteArray?) {
                        mainHandler.post { queue.offer(index, clip) }
                    }
                }
                val outcome = withContext(Dispatchers.IO) { api.askStream(imageJpeg, audioWav, listener) }
                if (requestId != requestSeq) return@launch
                if (outcome == null) {
                    queue.cancel()
                    recordHistory(imageJpeg, audioWav, null, null, null, 0, false,
                        System.currentTimeMillis() - startedAt, getString(R.string.err_network_ml), null, null)
                    settleWithError(R.string.err_network_ml)
                    return@launch
                }
                val result = outcome.result
                lastStatus = result.transcript?.let { "$it\n${result.reply}" } ?: result.reply
                val net = if (firstSoundMs != null && outcome.serverTtfaMs != null)
                    (firstSoundMs!! - outcome.serverTtfaMs).coerceAtLeast(0) else null
                recordHistory(imageJpeg, audioWav, result.transcript, result.reply,
                    result.route, result.httpCode, result.ok,
                    System.currentTimeMillis() - startedAt, null, firstSoundMs, net)
                if (outcome.streamed) {
                    streamReply = result.reply
                    queue.done()
                } else {
                    queue.cancel()
                    stopWatchdog()
                    onSmEvent(TriggerStateMachine.KeyEvent.RequestFinished)
                    playReply(result.audioB64, result.reply)
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: Throwable) {
                queue.cancel()
                if (requestId != requestSeq) return@launch
                recordHistory(imageJpeg, audioWav, null, null, null, 0, false,
                    System.currentTimeMillis() - startedAt, failure.message, null, null)
                settleWithError(R.string.err_network_ml)
            }
        }
    }

    private fun recordHistory(image: ByteArray, audio: ByteArray?, transcript: String?,
                              reply: String?, route: String?, httpCode: Int, ok: Boolean,
                              elapsedMs: Long, error: String?, firstSoundMs: Long?, netMs: Long?) {
        runCatching {
            history.add(image, audio, if (audio != null) "wav" else null,
                transcript, reply ?: "", route, httpCode, ok, elapsedMs, error, firstSoundMs, netMs)
        }
    }

    private val partPlayer = object : AudioPartQueue.Player {
        override fun play(clip: ByteArray, onDone: () -> Unit): Boolean {
            return try {
                val file = File(cacheDir, "part.mp3")
                file.writeBytes(clip)
                releasePlayerOnly()
                if (audioFocusRequest == null) requestAudioFocus()
                val player = MediaPlayer()
                mediaPlayer = player
                player.setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                player.setDataSource(file.absolutePath)
                player.setOnCompletionListener { onDone() }
                player.setOnErrorListener { _, _, _ ->
                    onDone()
                    true
                }
                player.prepare()
                player.start()
                true
            } catch (_: Exception) {
                releasePlayerOnly()
                false
            }
        }

        override fun stop() {
            releasePlayerOnly()
        }
    }

    private fun settleWithError(@StringRes messageRes: Int) {
        val message = getString(messageRes)
        stopWaitTicker()
        setUiState(UiState.PROBLEM)
        lastStatus = message
        onSmEvent(TriggerStateMachine.KeyEvent.RequestFinished)
        errorTone()
        speakLocal("error", message)
    }

    private fun startWaitTicker() {
        stopWaitTicker()
        val tick = object : Runnable {
            override fun run() {
                playWaitTick()
                mainHandler.postDelayed(this, 1500)
            }
        }
        waitTicker = tick
        mainHandler.postDelayed(tick, 1500)
    }

    private fun stopWaitTicker() {
        waitTicker?.let { mainHandler.removeCallbacks(it) }
        waitTicker = null
    }

    private fun playWaitTick() {
        runCatching {
            val generator = tickGenerator ?: android.media.ToneGenerator(
                AudioManager.STREAM_MUSIC, 35).also { tickGenerator = it }
            generator.startTone(android.media.ToneGenerator.TONE_PROP_BEEP, 60)
        }
    }

    private fun clickTone() {
        runCatching {
            android.media.ToneGenerator(AudioManager.STREAM_MUSIC, 55)
                .startTone(android.media.ToneGenerator.TONE_PROP_ACK, 60)
        }
    }

    private fun startWatchdog() {
        stopWatchdog()
        val watchdog = Runnable {
            watchdogTicker = null
            sm?.cancel()?.forEach(::executeAction)
            errorTone()
            speakLocal("error", getString(R.string.err_network_ml))
        }
        watchdogTicker = watchdog
        mainHandler.postDelayed(watchdog, 100_000)
    }

    private fun stopWatchdog() {
        watchdogTicker?.let { mainHandler.removeCallbacks(it) }
        watchdogTicker = null
    }

    private fun playReply(audioB64: String?, reply: String) {
        stopWaitTicker()
        setUiState(UiState.SPEAKING)
        if (audioB64 != null) {
            val clip = runCatching {
                Base64.decode(audioB64, Base64.DEFAULT)
            }.getOrNull()
            if (clip != null && playMp3(clip)) return
        }
        speakLocal("reply", reply)
    }

    private fun playMp3(clip: ByteArray): Boolean {
        return try {
            val file = File(cacheDir, "reply.mp3")
            file.writeBytes(clip)
            releasePlayer()
            val player = MediaPlayer()
            mediaPlayer = player
            player.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            requestAudioFocus()
            player.setDataSource(file.absolutePath)
            player.setOnCompletionListener {
                releasePlayer()
                onSmEvent(TriggerStateMachine.KeyEvent.PlaybackFinished)
            }
            player.setOnErrorListener { _, _, _ ->
                releasePlayer()
                onSmEvent(TriggerStateMachine.KeyEvent.PlaybackFinished)
                true
            }
            player.prepare()
            player.start()
            true
        } catch (_: Exception) {
            releasePlayer()
            false
        }
    }

    private fun speakLocal(prefix: String, text: String) {
        val engine = tts
        if (engine == null || !malayalamTtsReady) {
            onSmEvent(TriggerStateMachine.KeyEvent.PlaybackFinished)
            return
        }
        val code = runCatching {
            engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, "$prefix-${System.currentTimeMillis()}")
        }.getOrDefault(TextToSpeech.ERROR)
        if (code != TextToSpeech.SUCCESS) {
            onSmEvent(TriggerStateMachine.KeyEvent.PlaybackFinished)
        }
    }

    private fun releasePlayerOnly() {
        mediaPlayer?.let {
            runCatching { it.stop() }
            runCatching { it.release() }
        }
        mediaPlayer = null
    }

    private fun releasePlayer() {
        releasePlayerOnly()
        abandonAudioFocus()
    }

    private fun requestAudioFocus() {
        val am = audioManager ?: return
        if (Build.VERSION.SDK_INT >= 26) {
            val request = android.media.AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .build()
            audioFocusRequest = request
            am.requestAudioFocus(request)
        } else {
            @Suppress("DEPRECATION")
            am.requestAudioFocus(null, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        }
    }

    private fun abandonAudioFocus() {
        val am = audioManager ?: return
        if (Build.VERSION.SDK_INT >= 26) {
            audioFocusRequest?.let { am.abandonAudioFocusRequest(it) }
        } else {
            @Suppress("DEPRECATION")
            am.abandonAudioFocus(null)
        }
        audioFocusRequest = null
    }

    // ---- camera ----

    private fun cameraAllowed(): Boolean =
        ContextCompat.checkSelfPermission(this, android.Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED

    private suspend fun ensureCapture(): ImageCapture? = cameraMutex.withLock {
        capture?.let { return@withLock it }
        withContext(Dispatchers.Main) {
            suspendCancellableCoroutine<ImageCapture?> { cont ->
                try {
                    val future = ProcessCameraProvider.getInstance(this@ThirdEyeService)
                    future.addListener({
                        try {
                            val provider = future.get()
                            cameraProvider = provider
                            val selector = CameraSelector.Builder()
                                .requireLensFacing(CameraSelector.LENS_FACING_BACK)
                                .build()
                            val bound = ImageCapture.Builder()
                                .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                                .setFlashMode(ImageCapture.FLASH_MODE_AUTO)
                                .build()
                            provider.unbindAll()
                            provider.bindToLifecycle(this@ThirdEyeService, selector, bound)
                            capture = bound
                            if (cont.isActive) cont.resume(bound)
                        } catch (_: Exception) {
                            unbindCamera()
                            if (cont.isActive) cont.resume(null)
                        }
                    }, ContextCompat.getMainExecutor(this@ThirdEyeService))
                } catch (_: Exception) {
                    if (cont.isActive) cont.resume(null)
                }
            }
        }
    }

    private fun prewarmCamera() {
        if (capture != null || !cameraAllowed()) return
        scope.launch {
            if (ensureCapture() != null) {
                prewarmExpiry?.let { mainHandler.removeCallbacks(it) }
                val expiry = Runnable {
                    prewarmExpiry = null
                    if (photoJob == null) unbindCamera()
                }
                prewarmExpiry = expiry
                mainHandler.postDelayed(expiry, 4_000)
            }
        }
    }

    private suspend fun capturePhoto(): ByteArray? {
        if (!cameraAllowed()) return null
        prewarmExpiry?.let { mainHandler.removeCallbacks(it) }
        prewarmExpiry = null
        val ready = ensureCapture() ?: return null
        return try {
            suspendCancellableCoroutine { cont ->
                try {
                    ready.takePicture(cameraExecutor, object : ImageCapture.OnImageCapturedCallback() {
                        override fun onCaptureSuccess(image: ImageProxy) {
                            val jpeg = imageToJpeg(image)
                            image.close()
                            if (cont.isActive) cont.resume(jpeg)
                        }

                        override fun onError(exception: ImageCaptureException) {
                            if (cont.isActive) cont.resume(null)
                        }
                    })
                } catch (_: Exception) {
                    if (cont.isActive) cont.resume(null)
                }
            }
        } finally {
            // CameraX unbinds on the main thread; capture callbacks run on the encoder executor.
            unbindCamera()
        }
    }

    private fun imageToJpeg(image: ImageProxy): ByteArray? {
        return try {
            val rotation = image.imageInfo.rotationDegrees
            val source = image.toBitmap()
            val scale = (1280f / maxOf(source.width, source.height)).coerceAtMost(1f)
            val width = (source.width * scale).toInt().coerceAtLeast(1)
            val height = (source.height * scale).toInt().coerceAtLeast(1)
            val transform = Matrix().apply {
                setScale(width.toFloat() / source.width, height.toFloat() / source.height)
                postRotate(rotation.toFloat())
            }
            val scaled = Bitmap.createBitmap(source, 0, 0, source.width, source.height, transform, true)
            val out = java.io.ByteArrayOutputStream()
            scaled.compress(Bitmap.CompressFormat.JPEG, 82, out)
            out.toByteArray()
        } catch (_: Exception) {
            null
        }
    }

    private fun unbindCamera() {
        runCatching { cameraProvider?.unbindAll() }
        capture = null
        cameraProvider = null
    }

    // ---- audio recording ----

    private fun startRecording() {
        if (ContextCompat.checkSelfPermission(this, android.Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) return
        stopRecordingInternal()
        val minBuffer = AudioRecord.getMinBufferSize(
            WavWriter.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuffer <= 0) return
        try {
            val record = AudioRecord(
                MediaRecorder.AudioSource.MIC, WavWriter.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuffer * 2)
            record.startRecording()
            recorder = record
            pcm.reset()
            recordStartedAt = System.currentTimeMillis()
            recording = true
            recordThread = Thread {
                val buffer = ByteArray(4096)
                while (recording) {
                    val read = record.read(buffer, 0, buffer.size)
                    if (read > 0) synchronized(pcm) { pcm.write(buffer, 0, read) }
                }
            }.apply { start() }
        } catch (_: Exception) {
            recorder = null
        }
    }

    private fun stopRecording(): ByteArray? {
        if (!recording) return null
        recording = false
        recordThread?.join(1_000)
        recordThread = null
        val record = recorder
        recorder = null
        runCatching { record?.stop() }
        runCatching { record?.release() }
        if (record == null) return null
        val raw = synchronized(pcm) { pcm.toByteArray() }
        val duration = System.currentTimeMillis() - recordStartedAt
        return if (duration >= TriggerStateMachine.MIN_AUDIO_MS && raw.isNotEmpty()) {
            WavWriter.wav(raw)
        } else null
    }

    private fun stopRecordingInternal() {
        recording = false
        recordThread?.join(1_000)
        recordThread = null
        runCatching { recorder?.stop() }
        runCatching { recorder?.release() }
        recorder = null
        synchronized(pcm) { pcm.reset() }
    }

    // ---- feedback ----

    private fun tick() {
        val vibrator = getSystemService(VIBRATOR_SERVICE) as? Vibrator ?: return
        if (Build.VERSION.SDK_INT >= 26) {
            vibrator.vibrate(VibrationEffect.createOneShot(25, VibrationEffect.DEFAULT_AMPLITUDE))
        } else {
            @Suppress("DEPRECATION")
            vibrator.vibrate(25)
        }
    }

    private fun vibrateHold() {
        val vibrator = getSystemService(VIBRATOR_SERVICE) as? Vibrator ?: return
        val pattern = longArrayOf(0, 120, 80, 120, 80, 400)
        if (Build.VERSION.SDK_INT >= 26) {
            vibrator.vibrate(VibrationEffect.createWaveform(pattern, -1))
        } else {
            @Suppress("DEPRECATION")
            vibrator.vibrate(pattern, -1)
        }
    }

    private fun sendTone() {
        runCatching {
            android.media.ToneGenerator(AudioManager.STREAM_MUSIC, 60)
                .startTone(android.media.ToneGenerator.TONE_PROP_BEEP, 120)
        }
    }

    private fun errorTone() {
        mainHandler.post {
            runCatching {
                val generator = android.media.ToneGenerator(AudioManager.STREAM_MUSIC, 70)
                generator.startTone(android.media.ToneGenerator.TONE_CDMA_LOW_L, 160)
                mainHandler.postDelayed({
                    runCatching { generator.startTone(android.media.ToneGenerator.TONE_CDMA_LOW_L, 220) }
                }, 220)
            }
        }
    }

    // ---- foreground notification ----

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, getString(R.string.notification_channel_name),
                NotificationManager.IMPORTANCE_LOW))
    }

    private fun startForegroundWithType() {
        val notification = buildNotification()
        var types = 0
        if (ContextCompat.checkSelfPermission(this, android.Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED) {
            types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        }
        if (ContextCompat.checkSelfPermission(this, android.Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED) {
            types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        }
        if (Build.VERSION.SDK_INT >= 29 && types != 0) {
            startForeground(NOTIFICATION_ID, notification, types)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun buildNotification(): Notification {
        val openIntent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stopIntent = PendingIntent.getService(
            this, 1, Intent(this, ThirdEyeService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(getString(R.string.notification_text))
            .setContentIntent(openIntent)
            .addAction(0, getString(R.string.notification_stop), stopIntent)
            .setOngoing(true)
            .build()
    }

    // ---- status + helpers ----

    fun lastStatus(): String = lastStatus

    private fun serverUrl(): String = prefs.getString("server_url", "")?.trim().orEmpty()
    private fun token(): String = prefs.getString("token", "").orEmpty()

    companion object {
        const val ACTION_STOP = "app.thirdeye.phone.STOP"
        const val PREFS = "thirdeye"
        const val CHANNEL_ID = "thirdeye_trigger"
        const val NOTIFICATION_ID = 42

        @Volatile
        var instance: ThirdEyeService? = null
            private set
    }
}
