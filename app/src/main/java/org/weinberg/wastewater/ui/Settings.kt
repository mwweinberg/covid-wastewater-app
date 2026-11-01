package org.weinberg.wastewater.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.weinberg.wastewater.data.Cities
import java.text.DateFormat
import java.util.Date

@Composable
fun SettingsScreen(state: UiState, vm: MainViewModel, requestNotifications: () -> Unit) {
    val s = state.settings
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
        Section("Cities")
        Text("Shown as tabs on the dashboard. Each home-screen widget picks its own city when you place it.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Cities.all.forEach { id ->
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = id in s.enabledCities, onCheckedChange = { on ->
                    val set = if (on) s.enabledCities + id else (s.enabledCities - id).ifEmpty { setOf(id) }
                    vm.setEnabledCities(set)
                })
                Text(Cities.names[id] ?: id, Modifier.weight(1f))
            }
        }

        Section("Notifications")
        Text("Notify when a city's COVID wave state changes, for example from Quiet to Rising or into the high band. A handful per year at most.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Cities.all.forEach { id ->
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(Cities.names[id] ?: id, Modifier.weight(1f))
                Switch(checked = id in s.notifyCities, onCheckedChange = { on ->
                    if (on) requestNotifications()
                    vm.setNotifyCities(if (on) s.notifyCities + id else s.notifyCities - id)
                })
            }
        }

        Section("High mark")
        var pct by rememberSaveable(s.highPercentile) { mutableStateOf(s.highPercentile.toFloat()) }
        Text("The level that counts as \"high\": the ${pct.toInt()}th percentile of each site's own history. Alerts fire when levels are above it, or are rising fast enough to reach it within three weeks.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Slider(value = pct, onValueChange = { pct = it }, onValueChangeFinished = { vm.setHighPercentile(pct.toInt()) }, valueRange = 50f..95f, steps = 8)

        Section("Chart extras")
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Reference line on the COVID chart")
                Text("A fixed gene copies / L value drawn for orientation only. It never triggers alerts.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(checked = s.refLineEnabled, onCheckedChange = { vm.setRefLine(it, s.refLineValue) })
        }
        if (s.refLineEnabled) {
            var txt by rememberSaveable(s.refLineValue) { mutableStateOf(s.refLineValue.toLong().toString()) }
            OutlinedTextField(value = txt, onValueChange = { v -> txt = v.filter { it.isDigit() }; txt.toDoubleOrNull()?.let { vm.setRefLine(true, it) } },
                label = { Text("gene copies / L") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        }
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("National reference line")
                Text("For Berlin: the Germany-wide average across all RKI sites.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(checked = s.showNationalReference, onCheckedChange = vm::setShowNational)
        }

        Section("About")
        val last = if (s.lastRefreshMillis > 0) DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(s.lastRefreshMillis)) else "never"
        Text("Last successful refresh: $last. Data refreshes in the background about every 12 hours; the feeds themselves update weekly.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(8.dp))
        Text("Wastewater measures virus shed into sewers, not illness. Levels are only comparable with the same site's own past, which is why every number here is framed against that history. Sources: Robert Koch-Institut AMELAG (Berlin), NYC Open Data (New York), CDC National Wastewater Surveillance System (DC and flu/RSV for New York).",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun Section(title: String) {
    Spacer(Modifier.height(16.dp))
    Text(title, style = MaterialTheme.typography.titleMedium)
    HorizontalDivider(Modifier.padding(vertical = 6.dp))
}
