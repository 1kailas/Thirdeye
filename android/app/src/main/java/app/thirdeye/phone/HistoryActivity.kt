package app.thirdeye.phone

import android.graphics.Bitmap
import android.graphics.BitmapFactory
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
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class HistoryActivity : AppCompatActivity() {

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
            setPadding(24, 24, 24, 24)
        }
        scroll.addView(container)
        setContentView(scroll)
        rebuild()
    }

    override fun onDestroy() {
        player?.release()
        player = null
        tts?.shutdown()
        tts = null
        super.onDestroy()
    }

    private fun rebuild() {
        container.removeAllViews()
        val header = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        header.addView(TextView(this).apply {
            text = "Debug history (last ${history.list().size})"
            textSize = 20f
            setTextColor(ContextCompat.getColor(this@HistoryActivity, android.R.color.white))
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
        header.addView(Button(this).apply {
            text = "Clear"
            contentDescription = "Clear all debug history entries"
            setOnClickListener {
                history.clear()
                rebuild()
            }
        })
        container.addView(header)
        val turns = history.list()
        if (turns.isEmpty()) {
            container.addView(TextView(this).apply {
                text = "No requests recorded yet."
                setPadding(0, 32, 0, 32)
            })
            return
        }
        for (turn in turns) container.addView(card(turn))
    }

    private fun card(turn: History.Turn): View {
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16, 16, 16, 16)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = 16 }
            background = ContextCompat.getDrawable(this@HistoryActivity, android.R.drawable.dialog_holo_light_frame)
        }
        val time = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date(turn.timestamp))
        val meta = buildString {
            append("$time · route=${turn.route ?: "-"} · http ${turn.httpCode} · ")
            append(if (turn.ok) "ok" else "FAIL")
            append(" · ${turn.elapsedMs} ms")
            turn.error?.let { append("\nerror: $it") }
        }
        card.addView(TextView(this).apply { text = meta; textSize = 13f })
        turn.transcript?.let {
            card.addView(TextView(this).apply {
                text = "heard: $it"; textSize = 14f; setPadding(0, 8, 0, 0)
            })
        }
        card.addView(TextView(this).apply {
            text = "reply: ${turn.reply}"; textSize = 15f; setPadding(0, 4, 0, 0)
        })
        val imageFile = history.imageFile(turn)
        if (imageFile.isFile) {
            val bitmap = decodeSampled(imageFile, 480)
            if (bitmap != null) {
                card.addView(ImageView(this).apply {
                    setImageBitmap(bitmap)
                    adjustViewBounds = true
                    contentDescription = "Uploaded photo for request at $time. Tap to enlarge."
                    setOnClickListener { showFullImage(imageFile) }
                })
            }
        }
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        history.audioFile(turn)?.takeIf { it.isFile }?.let { audio ->
            buttons.addView(Button(this).apply {
                text = "Play my recording"
                contentDescription = "Play back the voice recording uploaded with this request"
                setOnClickListener { playAudio(audio) }
            })
        }
        buttons.addView(Button(this).apply {
            text = "Replay answer"
            contentDescription = "Speak the assistant reply again"
            setOnClickListener { replayAnswer(turn.reply) }
        })
        card.addView(buttons)
        return card
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
            Toast.makeText(this, "Could not play recording", Toast.LENGTH_SHORT).show()
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
