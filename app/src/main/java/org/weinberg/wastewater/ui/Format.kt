package org.weinberg.wastewater.ui

import androidx.compose.ui.graphics.Color
import org.weinberg.wastewater.analytics.Alert
import org.weinberg.wastewater.analytics.Phase
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.roundToLong

object Fmt {
    private val md = DateTimeFormatter.ofPattern("MMM d", Locale.US)
    private val mdy = DateTimeFormatter.ofPattern("MMM d, yyyy", Locale.US)

    fun date(d: LocalDate?, withYear: Boolean = false): String = d?.format(if (withYear) mdy else md) ?: "–"
    fun date(s: String?, withYear: Boolean = false): String = s?.let { runCatching { LocalDate.parse(it.take(10)) }.getOrNull() }?.let { date(it, withYear) } ?: "–"

    /** 107,145 -> "107k", 1,690,000 -> "1.7M", 6253 -> "6.3k", 3.94 (wval) -> "3.9". */
    fun level(v: Double?, concentration: Boolean): String {
        if (v == null) return "–"
        if (!concentration) return String.format(Locale.US, "%.1f", v)
        val a = abs(v)
        return when {
            a >= 1_000_000 -> String.format(Locale.US, "%.1fM", v / 1e6)
            a >= 100_000 -> "${(v / 1000).roundToInt()}k"
            a >= 10_000 -> String.format(Locale.US, "%.0fk", v / 1000)
            a >= 1_000 -> String.format(Locale.US, "%.1fk", v / 1000)
            else -> "${v.roundToInt()}"
        }
    }

    /** Prevalence (fraction) as "1 in N", N to 2 significant figures: 0.0019 -> "1 in 530". */
    fun oneIn(prevalence: Double?): String {
        if (prevalence == null || prevalence <= 0) return "–"
        val n = 1 / prevalence
        if (n > 10_000) return "fewer than 1 in 10,000"
        if (n < 20) return "more than 1 in 20"
        val mag = 10.0.pow(floor(log10(n)) - 1)
        val rounded = (n / mag).roundToLong() * mag
        return "1 in ${String.format(Locale.US, "%,d", rounded.roundToLong())}"
    }

    /** Short axis label for a prevalence tick: "1 in 1,000". */
    fun oneInShort(prevalence: Double): String {
        val n = (1 / prevalence).roundToLong()
        return "1 in ${String.format(Locale.US, "%,d", n)}"
    }

    fun pct(v: Double?): String = v?.let { "${it.roundToInt()}%" } ?: "–"
    fun growth(v: Double?): String = v?.let { String.format(Locale.US, "%+.0f%%", it) } ?: "–"

    fun phaseLabel(p: Phase) = when (p) {
        Phase.QUIET -> "Quiet"; Phase.RISING -> "Rising"; Phase.PEAKING -> "Peaking"
        Phase.DECLINING -> "Declining"; Phase.UNKNOWN -> "No data"
    }

    fun alertLabel(a: Alert) = when (a) {
        Alert.NONE -> "No alert"; Alert.WATCH -> "Watch: wave starting"; Alert.ALERT -> "Alert: entering a high period"
    }

    val bandShort = listOf("Well below", "Just below", "Right around", "Just above", "Well above")
}

/** One palette for phases, alerts and threshold bands, shared by the dashboard and the widget. */
object Palette {
    val quiet = Color(0xFF2E9E6B)
    val rising = Color(0xFFE09A1B)
    val peaking = Color(0xFFD9442B)
    val declining = Color(0xFF3B82C4)
    val unknown = Color(0xFF8A94A6)
    val alarm = Color(0xFFB3261E)

    /** Five threshold bands, Well below .. Well above. */
    val bands = listOf(Color(0xFF2E9E6B), Color(0xFF8DB84A), Color(0xFFE09A1B), Color(0xFFE2662A), Color(0xFFD9442B))
    fun band(index: Int?) = index?.let { bands.getOrNull(it) } ?: unknown

    fun phase(p: Phase) = when (p) {
        Phase.QUIET -> quiet; Phase.RISING -> rising; Phase.PEAKING -> peaking
        Phase.DECLINING -> declining; Phase.UNKNOWN -> unknown
    }

    fun alert(a: Alert, p: Phase) = when (a) {
        Alert.ALERT -> peaking; Alert.WATCH -> rising; Alert.NONE -> phase(p)
    }
}
