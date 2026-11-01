package org.weinberg.wastewater.analytics

import org.weinberg.wastewater.data.PathogenData
import org.weinberg.wastewater.data.WeekPoint
import java.time.LocalDate
import java.time.temporal.ChronoUnit
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.exp
import kotlin.math.ln

/** Where the wave is. */
enum class Phase { QUIET, RISING, PEAKING, DECLINING, UNKNOWN }

/** How much to worry. WATCH = sustained rise under way; ALERT = at or about to cross the high mark. */
enum class Alert { NONE, WATCH, ALERT }

data class Thresholds(
    /** Percentile of the site's own history that counts as "high". */
    val highPercentile: Int = 75,
    /** Weekly log growth that counts as a real rise (0.10 = +10.5 % per week, doubling in ~7 weeks). */
    val minGrowth: Double = 0.10,
    /** Level must be this multiple of the trailing 12 week minimum before a rise counts as onset. */
    val troughMultiple: Double = 3.0,
    val consecutiveRises: Int = 3,
    /** Projected weeks to high mark that still counts as "entering a dangerous period". */
    val projectionHorizonWeeks: Int = 3,
    /** Days after the last sample before the data is flagged stale. */
    val staleAfterDays: Int = 21,
)

data class Assessment(
    val latestWeekEnd: LocalDate?,
    val latestValue: Double?,
    val latestRaw: Double?,
    /** Share of all historical weeks with a lower value, 0..100. */
    val percentile: Double?,
    /** Weekly log growth over the last 3 weeks; exp(g)-1 is the % change per week. */
    val weeklyGrowth: Double?,
    val consecutiveRises: Int,
    /** latest / min of the preceding 12 weeks. */
    val troughRatio: Double?,
    val highMark: Double?,
    /** 0 = already above; null = not projected to cross (flat or falling). */
    val projectedWeeksToHigh: Int?,
    val phase: Phase,
    val alert: Alert,
    val belowDetection: Boolean,
    val staleDays: Long?,
    val officialCategory: String?,
    val onsetFired: Boolean,
) {
    val growthPercentPerWeek: Double? get() = weeklyGrowth?.let { (exp(it) - 1) * 100 }
    val isStale: Boolean get() = (staleDays ?: 0) > 21
}

object Analytics {

    fun assess(p: PathogenData, t: Thresholds = Thresholds(), today: LocalDate = LocalDate.now()): Assessment {
        val pts = p.weekly.filter { it.value != null && it.value > 0 }
        if (pts.size < 4) return empty(p, pts, today)
        val values = pts.map { it.value!! }
        val i = values.lastIndex
        val latest = values[i]
        val latestPt = pts[i]

        val percentile = percentileOf(latest, values)
        val highMark = quantile(values, t.highPercentile / 100.0)

        val back = minOf(3, i)
        val growth = if (back > 0) ln(latest / values[i - back]) / back else null
        var rises = 0
        var j = i
        while (j > 0 && values[j] > values[j - 1]) { rises++; j-- }
        val troughWindow = values.subList(maxOf(0, i - 12), i)
        val troughRatio = troughWindow.minOrNull()?.let { if (it > 0) latest / it else null }

        val belowDetection = (latestPt.belowLodShare ?: 0.0) >= 0.999 && p.isConcentration
        val onset = rises >= t.consecutiveRises && (growth ?: 0.0) >= t.minGrowth && (troughRatio ?: 0.0) >= t.troughMultiple

        val projected: Int? = when {
            highMark == null -> null
            latest >= highMark -> 0
            growth != null && growth > 0.02 -> ceil(ln(highMark / latest) / growth).toInt()
            else -> null
        }

        val g = growth ?: 0.0
        val aboveHigh = highMark != null && latest >= highMark
        val phase = when {
            belowDetection -> Phase.QUIET
            g <= -0.07 && rises == 0 -> Phase.DECLINING
            onset || (g >= t.minGrowth && rises >= 2 && (troughRatio ?: 0.0) >= 2.0) -> Phase.RISING
            aboveHigh && abs(g) < 0.07 -> Phase.PEAKING
            aboveHigh && g > 0 -> Phase.RISING
            else -> Phase.QUIET
        }
        val alert = when {
            belowDetection -> Alert.NONE
            aboveHigh && g > -0.07 -> Alert.ALERT
            phase == Phase.RISING && projected != null && projected <= t.projectionHorizonWeeks -> Alert.ALERT
            phase == Phase.RISING -> Alert.WATCH
            else -> Alert.NONE
        }

        val latestDate = LocalDate.parse(latestPt.weekEnd)
        val sampleDate = p.latestSampleDate?.let { runCatching { LocalDate.parse(it) }.getOrNull() } ?: latestDate
        return Assessment(
            latestWeekEnd = latestDate,
            latestValue = latest,
            latestRaw = latestPt.raw,
            percentile = percentile,
            weeklyGrowth = growth,
            consecutiveRises = rises,
            troughRatio = troughRatio,
            highMark = highMark,
            projectedWeeksToHigh = projected,
            phase = phase,
            alert = alert,
            belowDetection = belowDetection,
            staleDays = ChronoUnit.DAYS.between(sampleDate, today),
            officialCategory = p.officialCategory,
            onsetFired = onset,
        )
    }

    private fun empty(p: PathogenData, pts: List<WeekPoint>, today: LocalDate): Assessment {
        val last = pts.lastOrNull()
        val d = last?.let { LocalDate.parse(it.weekEnd) }
        return Assessment(d, last?.value, last?.raw, null, null, 0, null, null, null, Phase.UNKNOWN, Alert.NONE,
            false, d?.let { ChronoUnit.DAYS.between(it, today) }, p.officialCategory, false)
    }

    /** Share of values strictly below x, as 0..100. */
    fun percentileOf(x: Double, values: List<Double>): Double =
        if (values.isEmpty()) 0.0 else 100.0 * values.count { it < x } / values.size

    /** Linear-interpolated quantile, q in 0..1. */
    fun quantile(values: List<Double>, q: Double): Double? {
        if (values.isEmpty()) return null
        val s = values.sorted()
        val pos = q.coerceIn(0.0, 1.0) * (s.size - 1)
        val lo = pos.toInt(); val hi = minOf(lo + 1, s.size - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)
    }

    /** Run the state machine over history: list of (weekEnd, phase, alert) for each week with enough data. */
    fun history(p: PathogenData, t: Thresholds = Thresholds()): List<Triple<LocalDate, Phase, Alert>> {
        val pts = p.weekly.filter { it.value != null && it.value > 0 }
        return (4..pts.size).map { n ->
            val a = assess(p.copy(weekly = pts.take(n)), t, LocalDate.parse(pts[n - 1].weekEnd))
            Triple(a.latestWeekEnd!!, a.phase, a.alert)
        }
    }

    /** CDC Wastewater Viral Activity Level categories, August 2026 cutoffs. */
    fun cdcCategory(pathogenId: String, wval: Double): String {
        val cuts = when (pathogenId) {
            "covid" -> listOf(2.6 to "Very Low", 4.9 to "Low", 7.9 to "Moderate", 11.6 to "High")
            "fluA" -> listOf(1.6 to "Very Low", 3.1 to "Low", 5.0 to "Moderate", 7.6 to "High")
            else -> listOf(1.6 to "Very Low", 2.6 to "Low", 3.8 to "Moderate", 5.6 to "High")
        }
        return cuts.firstOrNull { wval <= it.first }?.second ?: "Very High"
    }
}
