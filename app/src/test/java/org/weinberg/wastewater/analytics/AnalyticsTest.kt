package org.weinberg.wastewater.analytics

import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test
import org.weinberg.wastewater.data.CityData
import org.weinberg.wastewater.data.PathogenData
import org.weinberg.wastewater.data.Readout
import org.weinberg.wastewater.data.ThresholdBlock
import org.weinberg.wastewater.data.WeekPoint
import org.weinberg.wastewater.ui.Fmt
import java.io.File
import java.time.LocalDate

class AnalyticsTest {
    private val json = Json { ignoreUnknownKeys = true }

    private fun series(vararg v: Double, start: String = "2025-01-05", lod: Double? = null, scale: String = "concentration", prevalence: ((Double) -> Double)? = null) = PathogenData(
        id = "covid", label = "x", scale = scale,
        weekly = v.mapIndexed { i, x -> WeekPoint(LocalDate.parse(start).plusWeeks(i.toLong()).toString(), x, x, 1, lod, prevalence = prevalence?.invoke(x)) },
        latestSampleDate = LocalDate.parse(start).plusWeeks((v.size - 1).toLong()).toString(),
    )

    /** A readout block like the pipeline's: prevalence = value / 1e6 (so 2,000 copies/L = 0.2% = the "Right around" edge). */
    private fun withReadout(p: PathogenData, alertLevelOk: Boolean, newestWeekEnd: String? = p.weekly.last().weekEnd, status: String = "ok", available: Boolean = true) = p.copy(
        comparableSince = p.weekly.first().weekEnd,
        threshold = ThresholdBlock(readout = Readout(available = available, status = status, message = if (available) null else "Lab change at X on Y",
            categoryIndex = 2, mainLine = "Right around the threshold", shortLabel = "Right around", prevalence = p.weekly.last().prevalence,
            alertLevelOk = alertLevelOk, newestWeekEnd = newestWeekEnd,
            bandEdgesPrevalence = listOf(0.0014, 0.002, 0.00392, 0.0056), bandEdgesLevel = listOf(1400.0, 2000.0, 3920.0, 5600.0))))

    @Test fun quietWhenFlat() {
        val a = Analytics.assess(series(10.0, 11.0, 10.0, 10.5, 10.0, 10.2, 10.1, 10.0), today = LocalDate.parse("2025-03-01"))
        assertEquals(Phase.QUIET, a.phase); assertEquals(Alert.NONE, a.alert)
    }

    @Test fun risingFiresOnSustainedGrowthFromTrough() {
        val base = DoubleArray(12) { 10.0 }.toList() + listOf(20.0, 40.0, 80.0)
        val a = Analytics.assess(series(*base.toDoubleArray()))
        assertTrue(a.onsetFired); assertEquals(Phase.RISING, a.phase); assertEquals(3, a.consecutiveRises)
        assertEquals(8.0, a.troughRatio!!, 1e-9)
        // no readout (flu-style series): percentile high mark rule -> ALERT
        assertEquals(Alert.ALERT, a.alert); assertEquals(0, a.projectedWeeksToHigh)
    }

    @Test fun watchWhenRisingButFarFromHigh() {
        val old = listOf(500.0, 800.0, 1000.0, 900.0, 600.0, 300.0, 150.0, 80.0)
        val trough = DoubleArray(12) { 5.0 }.toList()
        val rise = listOf(6.0, 7.0, 8.0, 10.0, 13.0, 16.0)
        val a = Analytics.assess(series(*(old + trough + rise).toDoubleArray()))
        assertEquals(Phase.RISING, a.phase)
        assertEquals(Alert.WATCH, a.alert)
        assertNotNull(a.projectedWeeksToHigh); assertTrue(a.projectedWeeksToHigh!! > 3)
    }

    @Test fun decliningAfterPeak() {
        val a = Analytics.assess(series(10.0, 20.0, 40.0, 80.0, 100.0, 90.0, 70.0, 50.0, 35.0))
        assertEquals(Phase.DECLINING, a.phase); assertEquals(Alert.NONE, a.alert)
    }

    @Test fun peakingWhenHighAndFlat() {
        val a = Analytics.assess(series(10.0, 20.0, 40.0, 80.0, 100.0, 102.0, 101.0, 103.0))
        assertEquals(Phase.PEAKING, a.phase); assertEquals(Alert.ALERT, a.alert)
    }

    @Test fun belowDetectionIsQuiet() {
        val a = Analytics.assess(series(6253.0, 6253.0, 6253.0, 6253.0, 6253.0, lod = 1.0))
        assertTrue(a.belowDetection); assertEquals(Phase.QUIET, a.phase)
    }

    @Test fun wvalFloorIsQuietWithoutAlert() {
        val a = Analytics.assess(series(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, scale = "wval"))
        assertTrue(a.atFloor); assertEquals(Phase.QUIET, a.phase); assertEquals(Alert.NONE, a.alert)
    }

    @Test fun comparableSinceDropsOlderWeeks() {
        // a huge old wave before comparableSince must not count towards the percentile
        val p = series(*(listOf(1e6, 1e6, 1e6, 1e6) + DoubleArray(10) { 100.0 }.toList() + listOf(110.0, 120.0)).toDoubleArray())
        val since = p.weekly[4].weekEnd
        val a = Analytics.assess(p.copy(comparableSince = since))
        assertEquals(LocalDate.parse(since), a.comparableSince)
        assertTrue("percentile should be high within comparable period", a.percentile!! > 80)
        val b = Analytics.assess(p)
        assertTrue(b.percentile!! < a.percentile!!)
    }

    @Test fun thresholdRule_alertNeedsRisingAndLevel() {
        val base = DoubleArray(12) { 100.0 }.toList() + listOf(200.0, 400.0, 800.0)   // rising, but 800 copies/L = 0.08%, below the band
        val p = series(*base.toDoubleArray(), prevalence = { it / 1e6 })
        val watch = Analytics.assess(withReadout(p, alertLevelOk = false))
        assertEquals(Phase.RISING, watch.phase); assertEquals(Alert.WATCH, watch.alert)
        val alert = Analytics.assess(withReadout(p, alertLevelOk = true))     // module says the projection reaches the band
        assertEquals(Alert.ALERT, alert.alert)
        // high and flat with the level condition met: Peaking, no alert (alert = entering, not being there)
        val flat = series(*(listOf(100.0, 200.0, 400.0, 800.0, 3000.0, 3050.0, 3020.0, 3040.0)).toDoubleArray(), prevalence = { it / 1e6 })
        val peak = Analytics.assess(withReadout(flat, alertLevelOk = true))
        assertEquals(Phase.PEAKING, peak.phase); assertEquals(Alert.NONE, peak.alert)
    }

    @Test fun thresholdRule_historyUsesWeekPrevalence() {
        // replayed weeks (not the newest) use their own prevalence against the band edge
        val base = DoubleArray(12) { 500.0 }.toList() + listOf(1000.0, 2000.0, 4000.0, 4100.0)
        val p = withReadout(series(*base.toDoubleArray(), prevalence = { it / 1e6 }), alertLevelOk = false)
        val h = Analytics.history(p)
        val atWeek4000 = h.first { it.first == LocalDate.parse(p.weekly[14].weekEnd) }
        assertEquals(Phase.RISING, atWeek4000.second); assertEquals(Alert.ALERT, atWeek4000.third)
    }

    @Test fun alarmWithholdsEverything() {
        val p = withReadout(series(10.0, 20.0, 40.0, 80.0, 160.0), alertLevelOk = true, status = "LAB_CHANGE_ALARM", available = false)
        val a = Analytics.assess(p)
        assertTrue(a.alarm); assertEquals(Phase.UNKNOWN, a.phase); assertEquals(Alert.NONE, a.alert); assertNull(a.latestValue)
    }

    @Test fun percentileAndQuantile() {
        val v = listOf(1.0, 2.0, 3.0, 4.0, 5.0)
        assertEquals(80.0, Analytics.percentileOf(5.0, v), 1e-9)
        assertEquals(4.0, Analytics.quantile(v, 0.75)!!, 1e-9)
    }

    @Test fun oneInFormatting() {
        assertEquals("1 in 520", Fmt.oneIn(1 / 523.6))
        assertEquals("1 in 77", Fmt.oneIn(0.012949))
        assertEquals("1 in 4,900", Fmt.oneIn(1 / 4876.0))
        assertEquals("fewer than 1 in 10,000", Fmt.oneIn(1e-5))
        assertEquals("more than 1 in 20", Fmt.oneIn(0.1))
    }

    /** Replays the real feeds: the comparable period is honoured and the readouts parse. */
    @Test fun feedsParseAndReplay() {
        for (city in listOf("berlin", "nyc", "dc")) {
            val f = File("../data/$city.json")
            if (!f.exists()) continue
            val data = json.decodeFromString<CityData>(f.readText())
            val covid = data.pathogen("covid")!!
            if (data.schemaVersion < 2) continue
            assertNotNull("readout present", covid.threshold?.readout)
            val since = LocalDate.parse(covid.comparableSince!!)
            assertTrue("no weeks before comparableSince", covid.weekly.none { LocalDate.parse(it.weekEnd).isBefore(since) })
            val a = Analytics.assess(covid, today = LocalDate.parse("2026-11-02"))
            assertTrue(a.hasReadout || a.alarm)
            if (city == "berlin") {
                // Berlin, from the corrected series: the Aug–Sep 2026 rise must be caught; no onset from the removed lab jump
                val hist = Analytics.history(covid)
                val onsets = hist.windowed(2).filter { (x, y) -> x.second != Phase.RISING && y.second == Phase.RISING }.map { it[1].first }
                assertTrue("2026 onset", onsets.any { it.year == 2026 && it.monthValue in 7..9 })
                assertTrue(hist.first().first >= since)
            }
        }
    }
}
