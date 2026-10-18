package net.huuhka.whisprmic

import android.app.Activity
import android.app.AlertDialog
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.Switch
import android.widget.TextView
import java.util.concurrent.ExecutorService

/**
 * The Dictionary tab: OpenWhispr's dictionary, read and edited on the Mac
 * through the server, including the words learned from corrections. The field
 * at the top finds words as you type and adds the word in it.
 */
class DictionaryPage(private val activity: Activity, private val prefs: Prefs, private val worker: ExecutorService) :
    LinearLayout(activity) {
    private val input: EditText
    private val addButton: Button
    private val autoLearnOff: TextView
    private val status: TextView
    private val adapter = WordAdapter()
    private var words = emptyList<DictionaryWord>()
    private var visible = emptyList<DictionaryWord>()
    /** The last load failed, so the status line shows that instead of the list's state. */
    private var error: String? = null
    private var loaded = false

    init {
        orientation = VERTICAL
        val pad = dp(16)
        val column = this
        column.addView(
            TextView(context).apply {
                text = "Names and terms OpenWhispr listens for. It's the same dictionary as on the Mac."
                textSize = 14f
                alpha = 0.8f
                setPadding(pad, dp(12), pad, dp(4))
            },
        )
        column.addView(
            Switch(context).apply {
                text = "Learn from my corrections"
                isChecked = prefs.learnCorrections
                setPadding(pad, dp(8), pad, 0)
                setOnCheckedChangeListener { _, checked -> prefs.learnCorrections = checked }
            },
        )
        column.addView(
            TextView(context).apply {
                text = "If you fix a misheard word after a dictation, before sending it, the word is added here, marked learned. " +
                    "A notification lets you undo it."
                textSize = 13f
                alpha = 0.7f
                setPadding(pad, dp(4), pad, 0)
            },
        )
        autoLearnOff = TextView(context).apply {
            text = "Auto-learn is off in OpenWhispr's settings on the Mac, so nothing is learned."
            textSize = 13f
            setPadding(pad, dp(4), pad, 0)
            visibility = View.GONE
        }
        column.addView(autoLearnOff)

        val addRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(pad, dp(12), pad, 0)
        }
        input = EditText(context).apply {
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
        addButton = Button(context).apply {
            text = "Add"
            setOnClickListener { add() }
        }
        addRow.addView(addButton)
        column.addView(addRow)

        status = TextView(context).apply {
            gravity = Gravity.CENTER
            setPadding(pad, dp(24), pad, pad)
        }
        column.addView(status)
        val list = ListView(context).apply {
            adapter = this@DictionaryPage.adapter
            setOnItemClickListener { _, _, position, _ -> edit(visible[position]) }
        }
        column.addView(list, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        render()
    }

    /** Fetches the dictionary again, for words learned since it was last shown. */
    fun reload() = run({ it.dictionary() })

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
        val field = EditText(context).apply {
            setText(word.word)
            isSingleLine = true
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setSelection(word.word.length)
        }
        val box = FrameLayout(context).apply {
            setPadding(dp(20), dp(8), dp(20), 0)
            addView(field)
        }
        AlertDialog.Builder(activity)
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
            error = "Set the server URL and token in the Setup tab first."
            return render()
        }
        val client = DictationClient(prefs.serverUrl, prefs.token)
        addButton.isEnabled = false
        worker.execute {
            val result = runCatching { request(client) }
            activity.runOnUiThread {
                if (activity.isDestroyed) return@runOnUiThread
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

    private inner class Row : LinearLayout(activity) {
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
