package net.huuhka.whisprmic

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Drawable
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.TextView
import java.util.concurrent.ExecutorService

/** An app's name as the launcher shows it, or its package name if it has none. */
fun appLabel(context: Context, packageName: String): String = runCatching {
    val pm = context.packageManager
    pm.getApplicationInfo(packageName, 0).loadLabel(pm).toString()
}.getOrDefault(packageName)

/**
 * The Apps tab: only chosen apps or every app, and a searchable list of
 * installed apps with checkboxes. The ticked ones are the current filter's list.
 * [onChange] runs when either changes.
 */
class AppsPage(
    private val activity: Activity,
    private val prefs: Prefs,
    private val worker: ExecutorService,
    private val onChange: () -> Unit,
) : LinearLayout(activity) {
    private class App(val packageName: String, val label: String)

    private val filter: RadioGroup
    private val explanation: TextView
    private val search: EditText
    private val empty: TextView
    private val adapter = AppAdapter()
    private var selected = mutableSetOf<String>()
    /** Launchable apps and their names, loaded in the background. */
    private var installed = emptyMap<String, String>()
    private var apps = emptyList<App>()
    private var visible = emptyList<App>()
    private var loaded = false
    private val icons = HashMap<String, Drawable?>()

    init {
        orientation = VERTICAL
        val pad = dp(16)
        filter = RadioGroup(context).apply {
            setPadding(pad, dp(8), pad, 0)
            addView(RadioButton(context).apply {
                id = View.generateViewId()
                text = "Only in apps I choose"
                tag = AppFilter.ONLY_SHOWN
            })
            addView(RadioButton(context).apply {
                id = View.generateViewId()
                text = "In every app"
                tag = AppFilter.ALL_EXCEPT_HIDDEN
            })
        }
        addView(filter)
        explanation = TextView(context).apply {
            textSize = 13f
            alpha = 0.7f
            setPadding(pad, dp(4), pad, dp(8))
        }
        addView(explanation)
        search = EditText(context).apply {
            hint = "Search apps"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            isSingleLine = true
            addTextChangedListener(
                object : TextWatcher {
                    override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
                    override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
                    override fun afterTextChanged(s: Editable?) = render()
                },
            )
        }
        addView(
            search,
            LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
                marginStart = pad
                marginEnd = pad
            },
        )
        empty = TextView(context).apply {
            gravity = Gravity.CENTER
            setPadding(pad, dp(32), pad, pad)
        }
        addView(empty)
        addView(
            ListView(context).apply {
                adapter = this@AppsPage.adapter
                setOnItemClickListener { _, _, position, _ -> toggle(visible[position]) }
            },
            LayoutParams(LayoutParams.MATCH_PARENT, 0, 1f),
        )
        worker.execute {
            val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            val packages = activity.packageManager.queryIntentActivities(launcher, 0).map { it.activityInfo.packageName }
            val labels = packages.associateWith { appLabel(activity, it) }
            activity.runOnUiThread {
                installed = labels
                loaded = true
                refresh()
            }
        }
        refresh()
    }

    /** Rereads the settings, which the mic's "Hide in" can change while this shows. */
    fun refresh() {
        val current = prefs.appFilter
        filter.setOnCheckedChangeListener(null)
        filter.check(filter.findViewWithTag<RadioButton>(current).id)
        filter.setOnCheckedChangeListener { group, id ->
            prefs.appFilter = group.findViewById<RadioButton>(id).tag as AppFilter
            refresh()
            onChange()
        }
        explanation.text = if (current == AppFilter.ONLY_SHOWN) {
            "The mic shows only in the apps you tick, so it can't type into anything else. " +
                "It never shows in password fields."
        } else {
            "The mic shows in every app except the ones you tick. It never shows in password fields."
        }
        selected = prefs.filterApps.toMutableSet()
        // Sorted here, not on every tick, so rows don't jump while you tick them.
        apps = ((installed.keys + selected) - activity.packageName)
            .map { App(it, installed[it] ?: appLabel(context, it)) }
            .sortedWith(compareBy<App> { it.packageName !in selected }.thenBy(String.CASE_INSENSITIVE_ORDER) { it.label })
        render()
    }

    private fun toggle(app: App) {
        if (!selected.remove(app.packageName)) selected += app.packageName
        prefs.filterApps = selected.toSet()
        adapter.notifyDataSetChanged()
        onChange()
    }

    private fun render() {
        val query = search.text.toString().trim()
        visible = if (query.isEmpty()) {
            apps
        } else {
            apps.filter { it.label.contains(query, ignoreCase = true) || it.packageName.contains(query, ignoreCase = true) }
        }
        empty.text = when {
            !loaded -> "Loading apps…"
            query.isEmpty() -> "No apps found"
            else -> "No apps match \"$query\""
        }
        empty.visibility = if (visible.isEmpty()) View.VISIBLE else View.GONE
        adapter.notifyDataSetChanged()
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
            row.icon.setImageDrawable(
                icons.getOrPut(app.packageName) { runCatching { context.packageManager.getApplicationIcon(app.packageName) }.getOrNull() },
            )
            return row
        }
    }

    private inner class Row : LinearLayout(activity) {
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
}
