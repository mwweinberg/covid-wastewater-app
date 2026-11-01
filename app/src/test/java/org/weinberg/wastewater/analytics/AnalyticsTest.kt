package org.weinberg.wastewater.analytics

import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test
import org.weinberg.wastewater.data.CityData
import org.weinberg.wastewater.data.PathogenData
import org.weinberg.wastewater.data.WeekPoint
import java.io.File
import java.time.LocalDate

class AnalyticsTest {
    private val json = Json { ignoreUnknownKeys = true }

    private fun series(vararg v: Double, start: String = "2025-01-05", lod: Double? = null) = PathogenData(
        id = "covid", label = "x", scale = "concentration",
        weekly = v.mapIndexed { i, x -> WeekPoint(LocalDate.parse(start).plusWeeks(i.toLong()).toString(), x, x, 1, lod) },
        latestSampleDate = LocalDate.parse(start).plusWeeks((v.size - 1).toLong()).toString(),
    )

    @Test fun quietWhenFlat() {
        val a = Analytics.assess(series(10.0, 11.0, 10.0, 10.5, 10.0, 10.2, 10.1, 10.0), today = LocalDate.parse("2025-03-01"))
        assertEquals(Phase.QUIET, a.phase); assertEquals(Alert.NONE, a.alert)
    }

    @Test fun risingFiresOnSustainedGrowthFromTrough() {
        // 12 flat weeks at 10, then 10 -> 20 -> 40 -> 80 (ln2 per week growth, 8x trough)
        val base = DoubleArray(12) { 10.0 }.toList() + listOf(20.0, 40.0, 80.0)
        val a = Analytics.assess(series(*base.toDoubleArray()))
        assertTrue(a.onsetFired); assertEquals(Phase.RISING, a.phase); assertEquals(3, a.consecutiveRises)
        assertEquals(8.0, a.troughRatio!!, 1e-9)
        // 80 is the max of history -> above the 75th percentile -> ALERT
        assertEquals(Alert.ALERT, a.alert); assertEquals(0, a.projectedWeeksToHigh)
    }

    @Test fun watchWhenRisingButFarFromHigh() {
        // history has a big old wave (so the high mark is far away), then a fresh rise from a deep trough
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

    @Test fun percentileAndQuantile() {
        val v = listOf(1.0, 2.0, 3.0, 4.0, 5.0)
        assertEquals(80.0, Analytics.percentileOf(5.0, v), 1e-9)
        assertEquals(4.0, Analytics.quantile(v, 0.75)!!, 1e-9)
    }

    @Test fun cdcCategories() {
        assertEquals("Low", Analytics.cdcCategory("covid", 3.94))
        assertEquals("Very High", Analytics.cdcCategory("covid", 22.0))
        assertEquals("Very Low", Analytics.cdcCategory("rsv", 1.0))
    }

    /** Replays the real Berlin feed and checks the onset dates found in the design backtest. */
    @Test fun berlinBacktestMatchesDesign() {
        val f = File("../data/berlin.json")
        if (!f.exists()) return
        val city = json.decodeFromString<CityData>(f.readText())
        val covid = city.pathogen("covid")!!
        val hist = Analytics.history(covid)
        val onsets = hist.windowed(2).filter { (a, b) -> a.second != Phase.RISING && b.second == Phase.RISING }.map { it[1].first }
        // the April 2024 rise and the July 2026 rise must both be caught
        assertTrue("2024 onset", onsets.any { it.year == 2024 && it.monthValue in 4..5 })
        assertTrue("2026 onset", onsets.any { it.year == 2026 && it.monthValue in 7..8 })
        val now = Analytics.assess(covid, today = LocalDate.parse("2026-11-01"))
        assertEquals(Phase.RISING, now.phase)
        assertTrue(now.alert != Alert.NONE)
    }
}
