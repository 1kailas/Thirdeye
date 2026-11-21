package app.thirdeye.phone

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.media.MediaPlayer
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelChildren
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import com.google.android.material.card.MaterialCardView
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class HistoryActivity : AppCompatActivity() {

    private val screenScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var history: History
    private var tts: TextToSpeech? = null
    private var player: MediaPlayer? = null
    private lateinit var container: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        history = History(File(filesDir, "history"))
        tts = TextToSpeech(this) { result ->
            if (result == TextToSpeech.SUCCESS) {
                val engine = tts ?: return@TextToSpeech
                val malayalam = engine.setLanguage(Locale.forLanguageTag("ml-IN"))
                if (malayalam == TextToSpeech.LANG_MISSING_DATA || malayalam == TextToSpeech.LANG_NOT_SUPPORTED) {
                    engine.language = Locale.getDefault()
                }
            }
        }
        val scroll = ScrollView(this)
        container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }
        scroll.addView(container)
        setContentView(scroll)
        rebuild()
    }

    override fun onDestroy() {
        screenScope.cancel()
        player?.release()
        player = null
        tts?.shutdown()
        tts = null
        super.onDestroy()
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    private fun rebuild() {
        screenScope.coroutineContext.cancelChildren()
        container.removeAllViews()
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(TextView(this).apply {
            text = "Debug history"
            textSize = 22f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
        header.addView(Button(this).apply {
            text = "Clear"
            minHeight = dp(48)
            contentDescription = "Clear all debug history entries"
            setOnClickListener {
                AlertDialog.Builder(this@HistoryActivity)
                    .setTitle("Clear history?")
                    .setMessage("This deletes all saved photos, recordings and replies.")
                    .setPositiveButton("Clear") { _, _ -> history.clear(); rebuild() }
                    .setNegativeButton("Cancel", null)
                    .show()
            }
        })
        container.addView(header)
        screenScope.launch {
            val turns = withContext(Dispatchers.IO) { history.list() }
            renderTurns(turns)
        }
    }

    private fun renderTurns(turns: List<History.Turn>) {
        if (turns.isEmpty()) {
            container.addView(LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER_HORIZONTAL
                setPadding(0, dp(48), 0, dp(48))
                addView(TextView(this@HistoryActivity).apply {
                    text = "🕘"
                    textSize = 40f
                    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
                })
                addView(TextView(this@HistoryActivity).apply {
                    text = "No requests recorded yet."
                    textSize = 16f
                    setPadding(0, dp(12), 0, 0)
                })
            })
            return
        }
        for (turn in turns) container.addView(card(turn))
    }

    private fun card(turn: History.Turn): View {
        val card = MaterialCardView(this).apply {
            radius = dp(20).toFloat()
            cardElevation = dp(1).toFloat()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(16) }
        }
        val inner = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }
        card.addView(inner)
        val time = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date(turn.timestamp))
        inner.addView(android.widget.HorizontalScrollView(this).apply { addView(chipRow(turn, time)) })
        turn.transcript?.let {
            inner.addView(TextView(this).apply {
                text = "You: $it"
                textSize = 14f
                setTypeface(typeface, android.graphics.Typeface.ITALIC)
                setPadding(0, dp(8), 0, 0)
            })
        }
        inner.addView(TextView(this).apply {
            text = turn.reply
            textSize = 16f
            setPadding(0, dp(4), 0, 0)
        })
        val imageFile = history.imageFile(turn)
        val thumbnail = ImageView(this).apply {
            adjustViewBounds = true
            scaleType = ImageView.ScaleType.FIT_CENTER
            setPadding(0, dp(12), 0, 0)
            visibility = View.GONE
            contentDescription = "Uploaded photo for request at $time. Tap to enlarge."
            setOnClickListener { showFullImage(imageFile) }
        }
        inner.addView(thumbnail)
        screenScope.launch {
            val bitmap = withContext(Dispatchers.IO) {
                if (imageFile.isFile) decodeSampled(imageFile, 480) else null
            }
            if (bitmap != null) {
                thumbnail.setImageBitmap(bitmap)
                thumbnail.visibility = View.VISIBLE
            }
        }
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        history.audioFile(turn)?.takeIf { it.isFile }?.let { audio ->
            buttons.addView(textButton("Play my recording", "Play back the voice recording uploaded with this request") {
                playAudio(audio)
            })
        }
        buttons.addView(textButton("Replay answer", "Speak the assistant reply again") {
            replayAnswer(turn.reply)
        })
        inner.addView(buttons)
        return card
    }

    private fun chipRow(turn: History.Turn, time: String): View {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        fun chip(text: String, color: Int) {
            row.addView(TextView(this).apply {
                this.text = text
                textSize = 12f
                setTextColor(Color.WHITE)
                setPadding(dp(10), dp(4), dp(10), dp(4))
                background = GradientDrawable().apply {
                    cornerRadius = dp(12).toFloat()
                    setColor(color)
                }
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { marginEnd = dp(8) }
            })
        }
        chip(time, 0xFF455A64.toInt())
        chip("route=${turn.route ?: "-"}", 0xFF0B6E6E.toInt())
        chip("http ${turn.httpCode}", if (turn.ok) 0xFF2E7D32.toInt() else 0xFFC62828.toInt())
        chip("${turn.elapsedMs} ms", 0xFF455A64.toInt())
        turn.firstSoundMs?.let { chip("first sound ${"%.1f".format(it / 1000.0)} s", 0xFF0B6E6E.toInt()) }
        turn.netMs?.let { chip("net $it ms", 0xFF455A64.toInt()) }
        if (!turn.ok) chip("FAIL", 0xFFC62828.toInt())
        return row
    }

    private fun textButton(label: String, desc: String, onClick: () -> Unit): Button =
        Button(this).apply {
            text = label
            contentDescription = desc
            minHeight = dp(48)
            setOnClickListener { onClick() }
        }

    private fun decodeSampled(file: File, maxDim: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) > maxDim) sample *= 2
        return BitmapFactory.decodeFile(file.absolutePath,
            BitmapFactory.Options().apply { inSampleSize = sample })
    }

    private fun showFullImage(file: File) {
        val bitmap = BitmapFactory.decodeFile(file.absolutePath) ?: return
        val view = ImageView(this).apply {
            setImageBitmap(bitmap)
            adjustViewBounds = true
            scaleType = ImageView.ScaleType.FIT_CENTER
            setBackgroundColor(Color.BLACK)
            contentDescription = "Full size uploaded photo"
        }
        AlertDialog.Builder(this)
            .setView(view)
            .setPositiveButton("Close", null)
            .show()
    }

    private fun playAudio(file: File) {
        releasePlayer()
        try {
            val mp = MediaPlayer()
            mp.setDataSource(file.absolutePath)
            mp.setOnCompletionListener { releasePlayer() }
            mp.prepare()
            mp.start()
            player = mp
        } catch (_: Exception) {
            android.widget.Toast.makeText(this, "Could not play recording",
                android.widget.Toast.LENGTH_SHORT).show()
        }
    }

    private fun replayAnswer(reply: String) {
        tts?.speak(reply, TextToSpeech.QUEUE_FLUSH, null, "history-replay")
    }

    private fun releasePlayer() {
        player?.let {
            runCatching { it.stop() }
            runCatching { it.release() }
        }
        player = null
    }
}
