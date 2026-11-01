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
    val weekly: List<WeekPoint> = emptyList(),
    val sites: List<SiteData> = emptyList(),
    val labChanges: List<String> = emptyList(),
    val officialCategory: String? = null,
    val reference: ReferenceSeries? = null,
    val note: String = "",
) {
    val isConcentration get() = scale == "concentration"
}

@Serializable
data class WeekPoint(
    val weekEnd: String,
    /** Smoothed blend; what the analytics run on. */
    val value: Double? = null,
    /** Blend of raw samples for that week, if any were taken. */
    val raw: Double? = null,
    val n: Int = 0,
    val belowLodShare: Double? = null,
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
)

object Cities {
    val all = listOf("berlin", "nyc", "dc")
    val names = mapOf("berlin" to "Berlin", "nyc" to "New York City", "dc" to "Washington DC")
    val defaultCity = "berlin"
}

object Pathogens {
    const val COVID = "covid"
    val order = listOf("covid", "fluA", "fluB", "rsv")
    val labels = mapOf("covid" to "COVID-19", "fluA" to "Influenza A", "fluB" to "Influenza B", "rsv" to "RSV")
}
