package org.weinberg.wastewater.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.weinberg.wastewater.analytics.Alert
import org.weinberg.wastewater.analytics.Analytics
import org.weinberg.wastewater.analytics.Assessment
import org.weinberg.wastewater.analytics.Phase
import org.weinberg.wastewater.data.Cities
import org.weinberg.wastewater.data.PathogenData
import org.weinberg.wastewater.data.Pathogens
import org.weinberg.wastewater.data.Readout
import java.time.LocalDate

@Composable
fun DashboardScreen(state: UiState, pathogenId: String, onPathogen: (String) -> Unit, onCity: (String) -> Unit, onRefresh: () -> Unit) {
    val city = state.city
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
        // city chips
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Cities.all.filter { it in state.settings.enabledCities || it == state.settings.selectedCity }.forEach { id ->
                FilterChip(selected = id == state.settings.selectedCity, onClick = { onCity(id) }, label = { Text(Cities.names[id] ?: id) })
            }
        }
        if (city == null) {
            Box(Modifier.fillMaxWidth().padding(top = 48.dp), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    if (state.refreshing) CircularProgressIndicator() else {
                        Text(state.error?.let { "Could not load data: $it" } ?: "No data yet.", textAlign = TextAlign.Center)
                        TextButton(onClick = onRefresh) { Text("Try again") }
                    }
                }
            }
            return@Column
        }
        val available = Pathogens.order.filter { city.pathogen(it) != null }
        val pid = if (pathogenId in available) pathogenId else Pathogens.COVID
        val p = city.pathogen(pid) ?: return@Column
        val a = Analytics.assess(p, state.settings.thresholds)

        if (pid != Pathogens.COVID) {
            TextButton(onClick = { onPathogen(Pathogens.COVID) }, contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) { Text("← Back to COVID-19") }
        }
        if (a.alarm) {
            AlarmCard(a.readout!!)
        } else {
            HeadlineCard(p, a, state.refreshing, city.generatedAt)
            if (a.hasReadout) { Spacer(Modifier.height(12.dp)); ThresholdCard(p, a) }
            Spacer(Modifier.height(12.dp))
            TrendCard(p, a, state.settings.highPercentile)
            Spacer(Modifier.height(12.dp))
            HistoryCard(p, a, state)
            Spacer(Modifier.height(12.dp))
            SeasonCard(p, a)
            if (p.sites.any { it.points.isNotEmpty() }) { Spacer(Modifier.height(12.dp)); PlantsCard(p) }
        }
        if (pid == Pathogens.COVID && available.size > 1) {
            Spacer(Modifier.height(16.dp))
            Text("Other viruses", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(8.dp))
            available.filter { it != Pathogens.COVID }.forEach { other ->
                val op = city.pathogen(other)!!
                CompactPathogenCard(op, Analytics.assess(op, state.settings.thresholds)) { onPathogen(other) }
                Spacer(Modifier.height(8.dp))
            }
        }
        Spacer(Modifier.height(16.dp))
        Text(p.note, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        p.comparableSince?.let {
            Spacer(Modifier.height(4.dp))
            Text("Shown from ${Fmt.date(it, withYear = true)}, the start of the current laboratory period. Percentiles compare against that period only.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.height(4.dp))
        Text("Data: ${city.attribution}. Feed built ${city.generatedAt.take(10)}.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun PhaseBadge(a: Assessment) {
    val color = Palette.alert(a.alert, a.phase)
    Row(verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.clip(RoundedCornerShape(50)).background(color.copy(alpha = 0.15f)).padding(horizontal = 10.dp, vertical = 4.dp)) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(6.dp))
        Text(if (a.alert == Alert.NONE) Fmt.phaseLabel(a.phase) else Fmt.alertLabel(a.alert), color = color, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.labelLarge)
    }
}

@Composable
private fun AlarmCard(r: Readout) {
    Card(colors = CardDefaults.cardColors(containerColor = Palette.alarm.copy(alpha = 0.14f))) {
        Column(Modifier.padding(16.dp)) {
            Text("Data alarm: readout withheld", style = MaterialTheme.typography.titleLarge, color = Palette.alarm, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Text(r.message ?: r.status, style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(8.dp))
            Text("A laboratory or method change (or a CDC history rewrite) means new values are no longer comparable with the frozen calibration. " +
                "The threshold readout, level, trend and alerts for COVID are withheld until the conversion is refitted in the methods project (status: ${r.status}).",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun HeadlineCard(p: PathogenData, a: Assessment, refreshing: Boolean, generatedAt: String) {
    val r = a.readout?.takeIf { it.available }
    val tint = if (a.alert != Alert.NONE) Palette.alert(a.alert, a.phase) else r?.let { Palette.band(it.categoryIndex) } ?: Palette.alert(a.alert, a.phase)
    Card(colors = CardDefaults.cardColors(containerColor = tint.copy(alpha = 0.10f))) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(p.label, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.weight(1f))
                if (refreshing) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp) else PhaseBadge(a)
            }
            Spacer(Modifier.height(10.dp))
            if (r != null) {
                Text(r.mainLine ?: "", fontSize = 26.sp, lineHeight = 30.sp, fontWeight = FontWeight.Bold, color = Palette.band(r.categoryIndex))
                r.confidenceLabel?.let { Text(it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                Spacer(Modifier.height(6.dp))
                Text(r.secondLine ?: "", style = MaterialTheme.typography.bodyLarge)
                Spacer(Modifier.height(10.dp))
            }
            Row(verticalAlignment = Alignment.Bottom) {
                val big = when { a.belowDetection -> "Below detection"; a.atFloor -> "At minimum"; else -> Fmt.level(a.latestValue, p.isConcentration) }
                Text(big, fontSize = if (r != null) 30.sp else 40.sp, fontWeight = FontWeight.Bold, lineHeight = if (r != null) 34.sp else 44.sp)
                Spacer(Modifier.width(8.dp))
                Column(Modifier.padding(bottom = 4.dp)) {
                    Text(p.unit + if (r != null) ", 2-week mean" else "", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    a.latestRaw?.let { Text("latest week ${Fmt.level(it, p.isConcentration)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(summary(a), style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(6.dp))
            val flags = buildList {
                if (r != null) {
                    add("Based on samples through ${Fmt.date(r.asOf)}")
                    if (r.dataDelayed) add("data delayed (${r.weeksBehind} weeks)")
                    if (r.provisional) add("newest week provisional (from CDC)")
                    if (r.singleWeekAfterGap) add("based on one week after a ${r.gapWeeks ?: "multi"}-week gap")
                    if (r.trendAsOf != null && r.trend != null) add("trend from measured weeks to ${Fmt.date(r.trendAsOf)}")
                    val built = generatedAt.take(10)
                    if (built.isNotEmpty() && built != LocalDate.now().toString()) add("readout computed ${Fmt.date(built)}")
                } else {
                    add("Week ending ${Fmt.date(a.latestWeekEnd, withYear = true)}")
                    if (a.isStale) add("Data is ${a.staleDays} days old")
                }
                a.officialCategory?.let {
                    val week = p.officialCategoryWeek?.let { w -> " (week of ${Fmt.date(w)}" + (if (p.officialCategoryN > 1) ", ${p.officialCategoryN} plants)" else ")") } ?: ""
                    add("CDC category: $it$week")
                }
            }.joinToString(" · ")
            val warn = (r?.dataDelayed == true) || (r == null && a.isStale)
            Text(flags, style = MaterialTheme.typography.bodySmall, color = if (warn) Palette.rising else MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun summary(a: Assessment): String {
    if (a.belowDetection) return "Not detectable in wastewater right now."
    if (a.atFloor) return "At CDC's minimum level. Percentile and growth are not meaningful at the floor."
    if (a.hasReadout) return when (a.alert) {
        Alert.ALERT -> "A sustained rise is under way and the level is at or near the threshold band. Treat this as entering a high-risk period."
        Alert.WATCH -> "A sustained rise has started, but the level is still below the threshold band."
        Alert.NONE -> when (a.phase) {
            Phase.DECLINING -> "Levels are falling, ${Fmt.growth(a.growthPercentPerWeek)} per week."
            Phase.PEAKING -> "Levels are high for this period but no longer rising."
            Phase.RISING -> "Levels are rising."
            else -> "No sustained rise."
        }
    }
    val pct = a.percentile?.let { "higher than ${Fmt.pct(it)} of comparable weeks" } ?: ""
    return when (a.alert) {
        Alert.ALERT -> if (a.projectedWeeksToHigh == 0) "Levels are in the high band and $pct. Treat this as an active wave."
            else "A sustained rise is under way and levels are projected to reach the high band within ${a.projectedWeeksToHigh} week${if (a.projectedWeeksToHigh == 1) "" else "s"}. Currently $pct."
        Alert.WATCH -> "A sustained rise has started from a low base. Levels are still $pct; the wave is early."
        Alert.NONE -> when (a.phase) {
            Phase.DECLINING -> "Levels are falling, ${Fmt.growth(a.growthPercentPerWeek)} per week. Currently $pct."
            Phase.PEAKING -> "Levels are high but no longer rising. Currently $pct."
            else -> "No sustained rise. Levels are $pct."
        }
    }
}

private enum class Range(val label: String, val months: Long?) { M3("3M", 3), Y1("1Y", 12), ALL("All", null) }

/** Chart 1 of the handoff: best-guess prevalence against the five bands, "1 in N" axis, dashed projection to today. */
@Composable
private fun ThresholdCard(p: PathogenData, a: Assessment) {
    val r = a.readout ?: return
    var range by rememberSaveable { mutableStateOf(Range.Y1) }
    val pts = p.weekly.filter { it.prevalence != null && it.prevalence > 0 }
    if (pts.size < 2 || r.bandEdgesPrevalence.size != 4) return
    val last = LocalDate.parse(pts.last().weekEnd)
    val first = LocalDate.parse(pts.first().weekEnd)
    val today = LocalDate.now()
    val from = range.months?.let { last.minusMonths(it) }?.coerceAtLeast(first) ?: first
    val to = if (r.projected) maxOf(last, today) else last
    val color = Palette.band(r.categoryIndex)
    val main = Line(pts.map { LocalDate.parse(it.weekEnd) to it.prevalence!! }, color, 2.5.dp)
    val extras = buildList {
        if (r.projected && r.prevalence != null) add(Line(listOf(last to pts.last().prevalence!!, today to r.prevalence), color, 2.dp, dashed = true))
    }
    val hollow = pts.filter { it.provisional }.map { LocalDate.parse(it.weekEnd) to it.prevalence!! } +
        (if (r.projected && r.prevalence != null) listOf(today to r.prevalence) else emptyList())
    Card {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Share of people infectious", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.weight(1f))
                Range.entries.forEach { rg -> FilterChip(selected = rg == range, onClick = { range = rg }, label = { Text(rg.label) }, modifier = Modifier.padding(start = 6.dp)) }
            }
            Spacer(Modifier.height(8.dp))
            LevelChart(main, extras, hollowDots = hollow, logScale = true, from = from, to = to,
                bands = Bands(r.bandEdgesPrevalence, Palette.bands, labels = Fmt.bandShort), yLabel = { Fmt.oneInShort(it) })
            Spacer(Modifier.height(6.dp))
            val th = r.thresholdPrevalence?.let { "${Fmt.oneIn(it)} (${String.format(java.util.Locale.US, "%.2f", it * 100)}%)" } ?: "–"
            val legend = buildList {
                add("Line: estimated share of people infectious, from a frozen conversion fitted to this city's wastewater. Stripes: the five bands around the reference threshold of $th, Bob Wachter's 2022 cutoff for unmasking in low-risk indoor settings.")
                if (r.projected) add("Dashed: damped projection from the newest sample to today.")
                if (pts.any { it.provisional }) add("Hollow dots: provisional weeks filled from CDC.")
                r.confidenceLabel?.let { add("$it: the conversion for this city rests on hospital admissions and an untested transfer of Berlin's hospitalisation risk.") }
            }.joinToString(" ")
            Text(legend, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun TrendCard(p: PathogenData, a: Assessment, highPct: Int) {
    val r = a.readout?.takeIf { it.available }
    val edge = r?.bandEdgesLevel?.getOrNull(1)
    Card {
        Column(Modifier.padding(16.dp)) {
            Text("Trend", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Stat("per week", if (a.atFloor) "–" else Fmt.growth(a.growthPercentPerWeek), Palette.phase(a.phase))
                Stat("weeks rising", "${a.consecutiveRises}")
                Stat("vs 12-wk low", a.troughRatio?.takeIf { !a.atFloor }?.let { String.format(java.util.Locale.US, "%.1f×", it) } ?: "–")
                if (edge != null) Stat("threshold", Fmt.level(edge, p.isConcentration)) else Stat("high mark", Fmt.level(a.highMark, p.isConcentration))
            }
            Spacer(Modifier.height(8.dp))
            val proj = when {
                a.belowDetection -> "Rules are paused while the virus is below the detection limit."
                a.atFloor -> "Rules are paused while CDC's level sits at its floor of 1.0."
                r != null && edge != null -> {
                    val lvl = "${Fmt.level(edge, p.isConcentration)} ${p.unit}"
                    (if (r.alertLevelOk) "The level is at or near the threshold band, which starts at $lvl" + (if (r.projected || p.id == Pathogens.COVID && a.comparableSince != null && r.weeksBehind > 0 && !r.dataDelayed) " (or projected to reach it within 3 weeks)." else ".")
                     else "The level is below the threshold band, which starts at $lvl.") +
                    (r.trend?.let { " Three-week trend: $it." } ?: " Three-week trend: not available (gap, floor values or provisional weeks).")
                }
                a.projectedWeeksToHigh == 0 -> "Above the high mark, which is the ${ordinal(highPct)} percentile of comparable history."
                a.projectedWeeksToHigh != null -> "At the current growth rate the high mark (${ordinal(highPct)} percentile of comparable history) would be reached in about ${a.projectedWeeksToHigh} weeks."
                else -> "Not on course to reach the high mark (${ordinal(highPct)} percentile of comparable history)."
            }
            Text(proj, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("Onset rule: 3 consecutive weekly rises, at least +10% per week, and 3× the trailing 12-week low. " + (if (a.onsetFired) "Currently met." else "Not currently met.") +
                if (r != null) " Alert = a rise under way (Rising) and the level at or near the threshold band." else "",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun ordinal(n: Int) = "$n" + when { n % 100 in 11..13 -> "th"; n % 10 == 1 -> "st"; n % 10 == 2 -> "nd"; n % 10 == 3 -> "rd"; else -> "th" }

@Composable
private fun Stat(label: String, value: String, color: Color = MaterialTheme.colorScheme.onSurface) {
    Column {
        Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold, color = color)
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private fun PathogenData.comparableWeekly(): List<org.weinberg.wastewater.data.WeekPoint> {
    val since = comparableSince?.let { runCatching { LocalDate.parse(it) }.getOrNull() }
    return weekly.filter { it.value != null && it.value > 0 && (since == null || !LocalDate.parse(it.weekEnd).isBefore(since)) }
}

@Composable
private fun HistoryCard(p: PathogenData, a: Assessment, state: UiState) {
    var range by rememberSaveable { mutableStateOf(Range.Y1) }
    val weekly = p.comparableWeekly()
    if (weekly.size < 2) return
    val r = a.readout?.takeIf { it.available }
    val last = LocalDate.parse(weekly.last().weekEnd)
    val first = LocalDate.parse(weekly.first().weekEnd)
    val from = range.months?.let { last.minusMonths(it) }?.coerceAtLeast(first) ?: first
    val main = Line(weekly.map { LocalDate.parse(it.weekEnd) to it.value!! }, Palette.alert(a.alert, a.phase), 2.5.dp)
    val since = a.comparableSince
    val extras = buildList {
        if (p.isConcentration) p.sites.filter { it.points.isNotEmpty() }.forEach { s ->
            val pts = s.points.filter { !it.belowLod && (since == null || !LocalDate.parse(it.date).isBefore(since)) }.map { LocalDate.parse(it.date) to (it.smoothed ?: it.value) }
            if (pts.size > 2 && p.sites.size <= 3) add(Line(pts, MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.35f), 1.dp))
        }
        if (state.settings.showNationalReference) p.reference?.let { ref ->
            add(Line(ref.weekly.filter { it.value != null && (since == null || !LocalDate.parse(it.weekEnd).isBefore(since)) }.map { LocalDate.parse(it.weekEnd) to it.value!! }, Palette.declining.copy(alpha = 0.6f), 1.5.dp, dashed = true))
        }
    }
    val dots = weekly.filter { it.raw != null && !it.provisional && (it.belowLodShare ?: 0.0) < 0.999 }.map { LocalDate.parse(it.weekEnd) to it.raw!! }
    val hollow = weekly.filter { it.provisional && it.raw != null }.map { LocalDate.parse(it.weekEnd) to it.raw!! }
    val edge = r?.bandEdgesLevel?.getOrNull(1)
    val hLines = buildList { if (edge != null) add(HLine(edge, Palette.bands[2], "threshold band")) }
    val markers = p.labChanges.filter { since == null || !LocalDate.parse(it).isBefore(since) }.map { LocalDate.parse(it) to "lab" }

    Card {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("History", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.weight(1f))
                Range.entries.forEach { rg ->
                    FilterChip(selected = rg == range, onClick = { range = rg }, label = { Text(rg.label) }, modifier = Modifier.padding(start = 6.dp))
                }
            }
            Spacer(Modifier.height(8.dp))
            LevelChart(main, extras, dots, hLines, highMark = if (r == null) a.highMark else null, highColor = Palette.peaking, markers = markers,
                logScale = p.isConcentration, from = from, to = last, concentration = p.isConcentration, hollowDots = hollow)
            Spacer(Modifier.height(6.dp))
            val legend = buildList {
                add(if (r != null) "Thick line: 2-week trailing mean. Dots: each week's own value. Dashed line: where the \"Right around the threshold\" band starts."
                    else "Thick line: smoothed blend. Dots: weekly samples. Shaded: above the high mark.")
                if (hollow.isNotEmpty()) add("Hollow dots: provisional weeks from CDC.")
                if (extras.any { !it.dashed }) add("Thin lines: individual plants.")
                if (extras.any { it.dashed }) add("Dashed blue: ${p.reference?.label}.")
                if (markers.isNotEmpty()) add("'lab' markers: laboratory changes that can shift levels.")
            }.joinToString(" ")
            Text(legend, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SeasonCard(p: PathogenData, a: Assessment) {
    val weekly = p.comparableWeekly()
    if (weekly.size < 20) return
    Card {
        Column(Modifier.padding(16.dp)) {
            Text("This year against previous years", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(8.dp))
            YearOverlayChart(weekly, logScale = p.isConcentration, concentration = p.isConcentration, highlightColor = Palette.alert(a.alert, a.phase))
            Spacer(Modifier.height(6.dp))
            Text("Each line is one calendar year, aligned by week, comparable laboratory period only. The bold line is the current year, so a wave starting early or late stands out against earlier years.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun PlantsCard(p: PathogenData) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Text("By treatment plant", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(8.dp))
            p.sites.filter { it.points.isNotEmpty() }.sortedByDescending { it.population }.forEach { s ->
                val lastPt = s.points.maxByOrNull { it.date } ?: return@forEach
                val recent = s.points.filter { !it.belowLod }.takeLast(4).map { it.value }
                val prev = s.points.filter { !it.belowLod }.dropLast(4).takeLast(4).map { it.value }
                val change = if (recent.isNotEmpty() && prev.isNotEmpty()) (geo(recent) / geo(prev) - 1) * 100 else null
                Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(s.name, style = MaterialTheme.typography.bodyMedium)
                        Text("${Fmt.level(s.population.toDouble(), true)} people · ${Fmt.date(LocalDate.parse(lastPt.date))}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Text(if (lastPt.belowLod) "below LOQ" else Fmt.level(lastPt.value, p.isConcentration), style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.width(10.dp))
                    Text(change?.let { Fmt.growth(it) } ?: "", modifier = Modifier.width(52.dp), textAlign = TextAlign.End,
                        style = MaterialTheme.typography.bodyMedium, color = change?.let { if (it > 15) Palette.rising else if (it < -15) Palette.declining else MaterialTheme.colorScheme.onSurfaceVariant } ?: Color.Unspecified)
                }
            }
            Text("Change compares the last four samples with the four before them.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun geo(v: List<Double>): Double = Math.exp(v.filter { it > 0 }.map(Math::log).average())

@Composable
private fun CompactPathogenCard(p: PathogenData, a: Assessment, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(p.label, style = MaterialTheme.typography.titleSmall)
                val line = when {
                    a.belowDetection -> "Below detection"
                    a.atFloor -> "At CDC's minimum level" + (a.officialCategory?.let { " · CDC: $it" } ?: "")
                    else -> "${Fmt.level(a.latestValue, p.isConcentration)} · ${Fmt.growth(a.growthPercentPerWeek)}/wk · above ${Fmt.pct(a.percentile)} of comparable weeks" + (a.officialCategory?.let { " · CDC: $it" } ?: "")
                }
                Text(line, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            PhaseBadge(a)
        }
    }
}
