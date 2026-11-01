package org.weinberg.wastewater.widget

import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ListItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.HorizontalDivider
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.material3.MaterialTheme
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.state.updateAppWidgetState
import kotlinx.coroutines.launch
import androidx.lifecycle.lifecycleScope
import org.weinberg.wastewater.data.Cities
import org.weinberg.wastewater.ui.WastewaterTheme

/** Lets the user pick which city a newly placed widget shows. */
class WidgetConfigActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val widgetId = intent?.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
            ?: AppWidgetManager.INVALID_APPWIDGET_ID
        setResult(RESULT_CANCELED)
        if (widgetId == AppWidgetManager.INVALID_APPWIDGET_ID) { finish(); return }

        setContent {
            WastewaterTheme {
                Scaffold { pad ->
                    Column(Modifier.padding(pad).padding(16.dp)) {
                        Text("Which city should this widget show?", style = MaterialTheme.typography.titleMedium)
                        Cities.all.forEach { id ->
                            ListItem(headlineContent = { Text(Cities.names[id] ?: id) },
                                modifier = Modifier.fillMaxWidth().clickable { choose(widgetId, id) })
                            HorizontalDivider()
                        }
                    }
                }
            }
        }
    }

    private fun choose(widgetId: Int, cityId: String) {
        lifecycleScope.launch {
            val glanceId = GlanceAppWidgetManager(this@WidgetConfigActivity).getGlanceIdBy(widgetId)
            updateAppWidgetState(this@WidgetConfigActivity, glanceId) { it[CITY_KEY] = cityId }
            WastewaterWidget().update(this@WidgetConfigActivity, glanceId)
            setResult(RESULT_OK, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId))
            finish()
        }
    }
}
