package org.weinberg.wastewater.ui

import androidx.compose.ui.graphics.Color
import org.weinberg.wastewater.analytics.Alert
import org.weinberg.wastewater.analytics.Phase
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

object Fmt {
    private val md = DateTimeFormatter.ofPattern("MMM d", Locale.US)
    private val mdy = DateTimeFormatter.ofPattern("MMM d, yyyy", Locale.US)

    fun date(d: LocalDate?, withYear: Boolean = false): String = d?.format(if (withYear) mdy else md) ?: "–"

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

    fun pct(v: Double?): String = v?.let { "${it.roundToInt()}%" } ?: "–"
    fun growth(v: Double?): String = v?.let { String.format(Locale.US, "%+.0f%%", it) } ?: "–"

    fun phaseLabel(p: Phase) = when (p) {
        Phase.QUIET -> "Quiet"; Phase.RISING -> "Rising"; Phase.PEAKING -> "Peaking"
        Phase.DECLINING -> "Declining"; Phase.UNKNOWN -> "No data"
    }

    fun alertLabel(a: Alert) = when (a) {
        Alert.NONE -> "No alert"; Alert.WATCH -> "Watch: wave starting"; Alert.ALERT -> "Alert: high period"
    }
}

/** One palette for phases and alerts, shared by the dashboard and the widget. */
object Palette {
    val quiet = Color(0xFF2E9E6B)
    val rising = Color(0xFFE09A1B)
    val peaking = Color(0xFFD9442B)
    val declining = Color(0xFF3B82C4)
    val unknown = Color(0xFF8A94A6)

    fun phase(p: Phase) = when (p) {
        Phase.QUIET -> quiet; Phase.RISING -> rising; Phase.PEAKING -> peaking
        Phase.DECLINING -> declining; Phase.UNKNOWN -> unknown
    }

    fun alert(a: Alert, p: Phase) = when (a) {
        Alert.ALERT -> peaking; Alert.WATCH -> rising; Alert.NONE -> phase(p)
    }
}
