package net.huuhka.whisprmic

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Drawable
import android.os.Bundle
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.widget.BaseAdapter
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import java.util.concurrent.Executors

/** A searchable list of installed apps with checkboxes; the ticked ones are the current filter's list. */
class AppPickerActivity : Activity() {
    private class App(val packageName: String, val label: String)

    private lateinit var prefs: Prefs
    private lateinit var selected: MutableSet<String>
    private lateinit var adapter: AppAdapter
    private lateinit var empty: TextView
    private var apps = emptyList<App>()
    private var visible = emptyList<App>()
    private var query = ""
    private val icons = HashMap<String, Drawable?>()
    private val worker = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestWindowFeature(Window.FEATURE_NO_TITLE)
        prefs = Prefs(this)
        selected = prefs.filterApps.toMutableSet()
        val onlyShown = prefs.appFilter == AppFilter.ONLY_SHOWN

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
                text = if (onlyShown) "Show the mic in" else "Hide the mic in"
                textSize = 24f
                setPadding(pad, dp(20), pad, 0)
            },
        )
        column.addView(
            TextView(this).apply {
                text = if (onlyShown) "The mic shows only in the apps you tick." else "The mic shows in every app except the ones you tick."
                textSize = 14f
                alpha = 0.8f
                setPadding(pad, dp(4), pad, dp(8))
            },
        )
        column.addView(
            EditText(this).apply {
                hint = "Search apps"
                inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
                isSingleLine = true
                addTextChangedListener(
                    object : TextWatcher {
                        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
                        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
                        override fun afterTextChanged(s: Editable?) {
                            query = s.toString().trim()
                            filter()
                        }
                    },
                )
            },
            LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                marginStart = pad
                marginEnd = pad
            },
        )
        empty = TextView(this).apply {
            text = "Loading apps…"
            gravity = Gravity.CENTER
            setPadding(pad, dp(32), pad, pad)
        }
        column.addView(empty)
        adapter = AppAdapter()
        val list = ListView(this).apply {
            adapter = this@AppPickerActivity.adapter
            setOnItemClickListener { _, _, position, _ -> toggle(visible[position]) }
        }
        column.addView(list, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(column)

        worker.execute {
            val loaded = loadApps()
            runOnUiThread {
                apps = loaded
                filter()
            }
        }
    }

    override fun onDestroy() {
        worker.shutdownNow()
        super.onDestroy()
    }

    private fun toggle(app: App) {
        if (!selected.remove(app.packageName)) selected += app.packageName
        prefs.filterApps = selected.toSet()
        adapter.notifyDataSetChanged()
    }

    private fun filter() {
        visible = if (query.isEmpty()) {
            apps
        } else {
            apps.filter { it.label.contains(query, ignoreCase = true) || it.packageName.contains(query, ignoreCase = true) }
        }
        empty.text = if (query.isEmpty()) "No apps found" else "No apps match \"$query\""
        empty.visibility = if (visible.isEmpty()) View.VISIBLE else View.GONE
        adapter.notifyDataSetChanged()
    }

    /** Launchable apps plus anything already ticked, ticked first, then by name. Sorted once, so rows don't jump when ticked. */
    private fun loadApps(): List<App> {
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val packages = packageManager.queryIntentActivities(launcher, 0).map { it.activityInfo.packageName }.toSet() + selected
        return (packages - packageName)
            .map { App(it, appLabel(this, it)) }
            .sortedWith(compareBy<App> { it.packageName !in selected }.thenBy(String.CASE_INSENSITIVE_ORDER) { it.label })
    }

    private inner class AppAdapter : BaseAdapter() {
        override fun getCount() = visible.size
        override fun getItem(position: Int) = visible[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val row = convertView as? Row ?: Row()
            val app = visible[position]
            row.label.text = app.label
            row.packageText.text = app.packageName
            row.check.isChecked = app.packageName in selected
            row.icon.setImageDrawable(icons.getOrPut(app.packageName) { runCatching { packageManager.getApplicationIcon(app.packageName) }.getOrNull() })
            return row
        }
    }

    private inner class Row : LinearLayout(this@AppPickerActivity) {
        val icon = ImageView(context)
        val label = TextView(context).apply { textSize = 16f }
        val packageText = TextView(context).apply {
            textSize = 12f
            alpha = 0.6f
        }
        // The row handles the tap, so the box itself mustn't take it.
        val check = CheckBox(context).apply {
            isClickable = false
            isFocusable = false
        }

        init {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(10), dp(8), dp(10))
            addView(icon, LayoutParams(dp(36), dp(36)))
            val text = LinearLayout(context).apply {
                orientation = VERTICAL
                addView(label)
                addView(packageText)
            }
            addView(text, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(16) })
            addView(check)
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    companion object {
        fun appLabel(context: Context, packageName: String): String = runCatching {
            val pm = context.packageManager
            pm.getApplicationInfo(packageName, 0).loadLabel(pm).toString()
        }.getOrDefault(packageName)
    }
}
