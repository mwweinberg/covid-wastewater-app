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
        Text("Notify when a city's COVID state changes: the threshold category, the wave phase (Quiet, Rising, Peaking, Declining) or an alert. A handful per year at most. " +
            "Data alarms (a lab or method change that invalidates the readout) are always notified, whatever is set here.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
        Text("The level that counts as \"high\" for the wave phase label and for flu and RSV alerts: the ${pct.toInt()}th percentile of each series' comparable history. " +
            "COVID alerts do not use it; they fire when a sustained rise is under way and the estimated share of people infectious is at or near the reference threshold.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Slider(value = pct, onValueChange = { pct = it }, onValueChangeFinished = { vm.setHighPercentile(pct.toInt()) }, valueRange = 50f..95f, steps = 8)

        Section("Chart extras")
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("National reference line")
                Text("For Berlin: the Germany-wide average across all RKI sites (which includes Berlin).", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(checked = s.showNationalReference, onCheckedChange = vm::setShowNational)
        }

        Section("About")
        val last = if (s.lastRefreshMillis > 0) DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(s.lastRefreshMillis)) else "never"
        Text("Last successful refresh: $last. Data refreshes in the background about every 12 hours; the feeds themselves update weekly (NYC monthly, with CDC filling the gap).", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(8.dp))
        Text("Wastewater measures virus shed into sewers, not illness. The share-of-people-infectious estimate converts each city's wastewater with a conversion fitted once and frozen " +
            "(Berlin against GrippeWeb × sentinel positivity, checked against England's random-sample survey; NYC and DC against hospital admissions via Berlin's hospitalisation risk, hence their confidence labels). " +
            "The reference threshold is Bob Wachter's 2022 cutoff of 10 reported cases per 100,000 per day, converted to about 0.28% of people infectious. " +
            "Only data from the current laboratory period is shown; older values are not comparable. Sources: Robert Koch-Institut AMELAG (Berlin), NYC Open Data (New York), CDC National Wastewater Surveillance System (DC, NYC fill-in, flu/RSV).",
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
