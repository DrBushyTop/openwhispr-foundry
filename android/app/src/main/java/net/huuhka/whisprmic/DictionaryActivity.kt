package net.huuhka.whisprmic

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.inputmethod.EditorInfo
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.Switch
import android.widget.TextView
import java.util.concurrent.Executors

/**
 * OpenWhispr's dictionary, read and edited on the Mac through the server: the
 * words it listens for, including the ones learned from corrections. The field
 * at the top finds words as you type and adds the word in it.
 */
class DictionaryActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var input: EditText
    private lateinit var addButton: Button
    private lateinit var autoLearnOff: TextView
    private lateinit var status: TextView
    private lateinit var adapter: WordAdapter
    private var words = emptyList<DictionaryWord>()
    private var visible = emptyList<DictionaryWord>()
    /** The last load failed, so the status line shows that instead of the list's state. */
    private var error: String? = null
    private var loaded = false
    private val worker = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestWindowFeature(Window.FEATURE_NO_TITLE)
        prefs = Prefs(this)

        val pad = dp(16)
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            // Takes the initial focus, so the keyboard doesn't open over the list.
            isFocusableInTouchMode = true
            descendantFocusability = ViewGroup.FOCUS_BEFORE_DESCENDANTS
            padForSystemBars()
        }
        column.addView(
            TextView(this).apply {
                text = "Dictionary"
                textSize = 24f
                setPadding(pad, dp(20), pad, 0)
            },
        )
        column.addView(
            TextView(this).apply {
                text = "Names and terms OpenWhispr listens for. It's the same dictionary as on the Mac."
                textSize = 14f
                alpha = 0.8f
                setPadding(pad, dp(4), pad, dp(8))
            },
        )
        column.addView(
            Switch(this).apply {
                text = "Learn from my corrections"
                isChecked = prefs.learnCorrections
                setPadding(pad, dp(8), pad, 0)
                setOnCheckedChangeListener { _, checked -> prefs.learnCorrections = checked }
            },
        )
        column.addView(
            TextView(this).apply {
                text = "If you fix a misheard word after a dictation, before sending it, the word is added here, marked learned. " +
                    "A notification lets you undo it."
                textSize = 13f
                alpha = 0.7f
                setPadding(pad, dp(4), pad, 0)
            },
        )
        autoLearnOff = TextView(this).apply {
            text = "Auto-learn is off in OpenWhispr's settings on the Mac, so nothing is learned."
            textSize = 13f
            setPadding(pad, dp(4), pad, 0)
            visibility = View.GONE
        }
        column.addView(autoLearnOff)

        val addRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(pad, dp(12), pad, 0)
        }
        input = EditText(this).apply {
            hint = "Find or add a word"
            isSingleLine = true
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            imeOptions = EditorInfo.IME_ACTION_DONE
            setOnEditorActionListener { _, action, _ ->
                if (action == EditorInfo.IME_ACTION_DONE) add()
                action == EditorInfo.IME_ACTION_DONE
            }
            addTextChangedListener(
                object : TextWatcher {
                    override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
                    override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
                    override fun afterTextChanged(s: Editable?) = render()
                },
            )
        }
        addRow.addView(input, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        addButton = Button(this).apply {
            text = "Add"
            setOnClickListener { add() }
        }
        addRow.addView(addButton)
        column.addView(addRow)

        status = TextView(this).apply {
            gravity = Gravity.CENTER
            setPadding(pad, dp(24), pad, pad)
        }
        column.addView(status)
        adapter = WordAdapter()
        val list = ListView(this).apply {
            adapter = this@DictionaryActivity.adapter
            setOnItemClickListener { _, _, position, _ -> edit(visible[position]) }
        }
        column.addView(list, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(column)

        render()
        run({ it.dictionary() })
    }

    override fun onDestroy() {
        worker.shutdownNow()
        super.onDestroy()
    }

    private fun add() {
        val word = input.text.toString().trim()
        if (word.isEmpty()) return
        if (words.any { it.word.equals(word, ignoreCase = true) }) {
            input.setText("")
            return
        }
        run({ it.changeDictionary(add = listOf(word)) }) { input.setText("") }
    }

    private fun edit(word: DictionaryWord) {
        val field = EditText(this).apply {
            setText(word.word)
            isSingleLine = true
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setSelection(word.word.length)
        }
        val box = FrameLayout(this).apply {
            setPadding(dp(20), dp(8), dp(20), 0)
            addView(field)
        }
        AlertDialog.Builder(this)
            .setTitle(if (word.learned) "Edit learned word" else "Edit word")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                val renamed = field.text.toString().trim()
                if (renamed.isNotEmpty() && renamed != word.word) {
                    run({ it.changeDictionary(add = listOf(renamed), remove = listOf(word.word)) })
                }
            }
            .setNegativeButton("Delete") { _, _ -> run({ it.changeDictionary(remove = listOf(word.word)) }) }
            .setNeutralButton("Cancel", null)
            .show()
    }

    /** Runs a request off the main thread and shows the dictionary it returns. */
    private fun run(request: (DictationClient) -> Dictionary, onDone: () -> Unit = {}) {
        if (!prefs.isConfigured) {
            error = "Set the server URL and token in Whispr Mic first."
            return render()
        }
        val client = DictationClient(prefs.serverUrl, prefs.token)
        addButton.isEnabled = false
        worker.execute {
            val result = runCatching { request(client) }
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                addButton.isEnabled = true
                result.onSuccess {
                    words = it.words
                    loaded = true
                    error = null
                    autoLearnOff.visibility = if (it.autoLearn) View.GONE else View.VISIBLE
                    onDone()
                }.onFailure { error = it.message ?: "Couldn't reach OpenWhispr" }
                render()
            }
        }
    }

    /** Newest first, since that's where learned words turn up, filtered by the field. */
    private fun render() {
        val query = input.text.toString().trim()
        visible = words.asReversed().filter { query.isEmpty() || it.word.contains(query, ignoreCase = true) }
        status.text = when {
            error != null -> error
            !loaded -> "Loading…"
            visible.isNotEmpty() -> ""
            query.isNotEmpty() -> "No words match. Tap Add to add “$query”."
            else -> "No words yet."
        }
        status.visibility = if (status.text.isEmpty()) View.GONE else View.VISIBLE
        adapter.notifyDataSetChanged()
    }

    private inner class WordAdapter : BaseAdapter() {
        override fun getCount() = visible.size
        override fun getItem(position: Int) = visible[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val row = convertView as? Row ?: Row()
            val word = visible[position]
            row.word.text = word.word
            row.learned.visibility = if (word.learned) View.VISIBLE else View.GONE
            return row
        }
    }

    private inner class Row : LinearLayout(this@DictionaryActivity) {
        val word = TextView(context).apply { textSize = 16f }
        val learned = TextView(context).apply {
            text = "learned"
            textSize = 12f
            alpha = 0.6f
        }

        init {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
            addView(word, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
            addView(learned)
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
