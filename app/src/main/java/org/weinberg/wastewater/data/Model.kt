package org.weinberg.wastewater.data

import kotlinx.serialization.Serializable

@Serializable
data class CityData(
    val schemaVersion: Int = 1,
    val generatedAt: String = "",
    val cityId: String,
    val cityName: String,
    val country: String = "",
    val attribution: String = "",
    val sourceUrl: String = "",
    val pathogens: List<PathogenData> = emptyList(),
) {
    fun pathogen(id: String): PathogenData? = pathogens.firstOrNull { it.id == id }
}

@Serializable
data class PathogenData(
    val id: String,
    val label: String,
    /** "concentration" (gene copies / L, log scale) or "wval" (CDC level, linear). */
    val scale: String,
    val unit: String = "",
    val latestSampleDate: String? = null,
    /** First week whose values are comparable with the newest (lab/method period). Nothing before it is shown. */
    val comparableSince: String? = null,
    val weekly: List<WeekPoint> = emptyList(),
    val sites: List<SiteData> = emptyList(),
    val labChanges: List<String> = emptyList(),
    val officialCategory: String? = null,
    val officialCategoryWeek: String? = null,
    val officialCategoryN: Int = 0,
    val reference: ReferenceSeries? = null,
    /** COVID only: prevalence readout against the reference threshold, from the methods project's frozen fit. */
    val threshold: ThresholdBlock? = null,
    val note: String = "",
) {
    val isConcentration get() = scale == "concentration"
    val isWval get() = scale == "wval"
}

@Serializable
data class WeekPoint(
    val weekEnd: String,
    /** Smoothed blend (2-week trailing mean for COVID); what the analytics run on. */
    val value: Double? = null,
    /** That week's own value, if any samples were taken. */
    val raw: Double? = null,
    val n: Int = 0,
    val belowLodShare: Double? = null,
    /** NYC: filled from CDC's level because NYC has not published this week yet. */
    val provisional: Boolean = false,
    /** Best-guess share of people infectious, from the frozen conversion. */
    val prevalence: Double? = null,
)

@Serializable
data class SiteData(
    val name: String,
    val population: Int = 0,
    val points: List<SitePoint> = emptyList(),
)

@Serializable
data class SitePoint(
    val date: String,
    val value: Double,
    val smoothed: Double? = null,
    val belowLod: Boolean = false,
)

@Serializable
data class ReferenceSeries(val label: String, val weekly: List<WeekPoint> = emptyList())

@Serializable
data class ThresholdBlock(
    val comparableSince: String? = null,
    val readout: Readout? = null,
    val chart: List<ChartPoint> = emptyList(),
)

/** Output of wastewater_threshold.readout(), camelCased. When [available] is false, [status]/[message] carry an alarm. */
@Serializable
data class Readout(
    val available: Boolean = false,
    val status: String = "ok",
    val message: String? = null,
    val categoryIndex: Int? = null,
    val mainLine: String? = null,
    val shortLabel: String? = null,
    val confidenceLabel: String? = null,
    val prevalence: Double? = null,
    val oneInN: String? = null,
    val trend: String? = null,
    val trendAsOf: String? = null,
    val trendProvisional: Boolean = false,
    val percentile: Int? = null,
    val percentileSince: String? = null,
    val secondLine: String? = null,
    val asOf: String? = null,
    val newestWeekEnd: String? = null,
    val projected: Boolean = false,
    val weeksBehind: Int = 0,
    val dataDelayed: Boolean = false,
    val singleWeekAfterGap: Boolean = false,
    val gapWeeks: Int? = null,
    val provisional: Boolean = false,
    val alertLevelOk: Boolean = false,
    val thresholdPrevalence: Double? = null,
    val bandEdgesPrevalence: List<Double> = emptyList(),
    /** Band edges in the series' own units (copies/L or WVAL). */
    val bandEdgesLevel: List<Double> = emptyList(),
) {
    val isAlarm get() = !available && status != "ok"
}

@Serializable
data class ChartPoint(
    val week: String,
    val value: Double? = null,
    val raw: Double? = null,
    val prevalence: Double? = null,
    val provisional: Boolean = false,
)

@Serializable
data class Manifest(
    val schemaVersion: Int = 1,
    val generatedAt: String = "",
    val cities: List<ManifestCity> = emptyList(),
)

@Serializable
data class ManifestCity(
    val cityId: String,
    val cityName: String,
    val file: String,
    val pathogens: List<String> = emptyList(),
    val latestSampleDate: String? = null,
    val readout: String? = null,
)

object Cities {
    val all = listOf("berlin", "nyc", "dc")
    val names = mapOf("berlin" to "Berlin", "nyc" to "New York City", "dc" to "Washington DC")
    val codes = mapOf("berlin" to "BER", "nyc" to "NYC", "dc" to "DC")
    val defaultCity = "berlin"
}

object Pathogens {
    const val COVID = "covid"
    val order = listOf("covid", "fluA", "fluB", "rsv")
    val labels = mapOf("covid" to "COVID-19", "fluA" to "Influenza A", "fluB" to "Influenza B", "rsv" to "RSV")
}
