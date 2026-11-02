package org.weinberg.wastewater.analytics

import org.weinberg.wastewater.data.PathogenData
import org.weinberg.wastewater.data.Readout
import org.weinberg.wastewater.data.WeekPoint
import java.time.LocalDate
import java.time.temporal.ChronoUnit
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.exp
import kotlin.math.ln

/** Where the wave is. */
enum class Phase { QUIET, RISING, PEAKING, DECLINING, UNKNOWN }

/**
 * How much to worry. WATCH = sustained rise under way.
 * ALERT = a rise is under way AND the level is at (or, Berlin, projected within 3 weeks to reach) "Right around the threshold"
 * when a threshold readout exists; otherwise (flu, RSV) at or about to cross the percentile high mark.
 */
enum class Alert { NONE, WATCH, ALERT }

data class Thresholds(
    /** Percentile of the comparable history that counts as "high" (phase label; alert for flu/RSV). */
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
    /** Share of comparable-period weeks with a lower value, 0..100. */
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
    /** CDC level at its floor of 1.0 (flu A, RSV most of the year): percentile and growth are meaningless. */
    val atFloor: Boolean,
    val staleDays: Long?,
    val officialCategory: String?,
    val onsetFired: Boolean,
    /** Threshold readout (COVID), or null. Check [alarm] first. */
    val readout: Readout? = null,
    /** The methods module withheld the readout (lab change, method or restatement check). Nothing else should be trusted. */
    val alarm: Boolean = false,
    val comparableSince: LocalDate? = null,
) {
    val growthPercentPerWeek: Double? get() = weeklyGrowth?.let { (exp(it) - 1) * 100 }
    val isStale: Boolean get() = (staleDays ?: 0) > 21
    val hasReadout: Boolean get() = readout?.available == true
}

object Analytics {

    fun assess(p: PathogenData, t: Thresholds = Thresholds(), today: LocalDate = LocalDate.now()): Assessment {
        val since = p.comparableSince?.let { runCatching { LocalDate.parse(it) }.getOrNull() }
        val readout = p.threshold?.readout
        if (readout?.isAlarm == true) return empty(p, emptyList(), today, readout, since, alarm = true)

        val pts = p.weekly.filter { it.value != null && it.value > 0 && (since == null || !LocalDate.parse(it.weekEnd).isBefore(since)) }
        if (pts.size < 4) return empty(p, pts, today, readout, since)
        val values = pts.map { it.value!! }
        val i = values.lastIndex
        val latest = values[i]
        val latestPt = pts[i]

        val percentile = percentileOf(latest, values)
        val highMark = quantile(values, t.highPercentile / 100.0)

        // growth per calendar week, so gaps in reporting (common in the CDC feed) do not inflate the rate
        val back = minOf(3, i)
        val weeksBack = ChronoUnit.WEEKS.between(LocalDate.parse(pts[i - back].weekEnd), LocalDate.parse(pts[i].weekEnd)).coerceAtLeast(1)
        val growth = if (back > 0) ln(latest / values[i - back]) / weeksBack else null
        var rises = 0
        var j = i
        while (j > 0 && values[j] > values[j - 1]) { rises++; j-- }
        val troughWindow = values.subList(maxOf(0, i - 12), i)
        val troughRatio = troughWindow.minOrNull()?.let { if (it > 0) latest / it else null }

        val belowDetection = (latestPt.belowLodShare ?: 0.0) >= 0.999 && p.isConcentration
        val atFloor = p.isWval && latest <= 1.0
        val onset = rises >= t.consecutiveRises && (growth ?: 0.0) >= t.minGrowth && (troughRatio ?: 0.0) >= t.troughMultiple

        val projected: Int? = when {
            highMark == null -> null
            latest >= highMark -> 0
            growth != null && growth > 0.02 -> ceil(ln(highMark / latest) / growth).toInt().coerceAtMost(52)
            else -> null
        }

        val g = growth ?: 0.0
        val aboveHigh = highMark != null && latest >= highMark
        val phase = when {
            belowDetection || atFloor -> Phase.QUIET
            g <= -0.07 && rises == 0 -> Phase.DECLINING
            onset || (g >= t.minGrowth && rises >= 2 && (troughRatio ?: 0.0) >= 2.0) -> Phase.RISING
            aboveHigh && abs(g) < 0.07 -> Phase.PEAKING
            aboveHigh && g > 0 -> Phase.RISING
            else -> Phase.QUIET
        }

        val alert = if (readout?.available == true) {
            // Threshold rule (methods handoff, option A): the level condition replaces the percentile high mark.
            // For the newest week use the module's own flag (it includes Berlin's projection); for replayed history, the week's prevalence.
            val edge = readout.bandEdgesPrevalence.getOrNull(1)
            val levelOk = if (latestPt.weekEnd == readout.newestWeekEnd || readout.newestWeekEnd == null) readout.alertLevelOk
                          else edge != null && (latestPt.prevalence ?: 0.0) >= edge
            when {
                belowDetection -> Alert.NONE
                phase == Phase.RISING && levelOk -> Alert.ALERT
                phase == Phase.RISING -> Alert.WATCH
                else -> Alert.NONE
            }
        } else when {
            belowDetection || atFloor -> Alert.NONE
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
            atFloor = atFloor,
            staleDays = ChronoUnit.DAYS.between(sampleDate, today),
            officialCategory = p.officialCategory,
            onsetFired = onset,
            readout = readout,
            alarm = false,
            comparableSince = since,
        )
    }

    private fun empty(p: PathogenData, pts: List<WeekPoint>, today: LocalDate, readout: Readout?, since: LocalDate?, alarm: Boolean = false): Assessment {
        val last = pts.lastOrNull()
        val d = last?.let { LocalDate.parse(it.weekEnd) }
        return Assessment(d, last?.value, last?.raw, null, null, 0, null, null, null, Phase.UNKNOWN, Alert.NONE,
            false, false, d?.let { ChronoUnit.DAYS.between(it, today) }, p.officialCategory, false, readout, alarm, since)
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
        val since = p.comparableSince?.let { runCatching { LocalDate.parse(it) }.getOrNull() }
        val pts = p.weekly.filter { it.value != null && it.value > 0 && (since == null || !LocalDate.parse(it.weekEnd).isBefore(since)) }
        return (4..pts.size).map { n ->
            val a = assess(p.copy(weekly = pts.take(n)), t, LocalDate.parse(pts[n - 1].weekEnd))
            Triple(a.latestWeekEnd!!, a.phase, a.alert)
        }
    }
}
