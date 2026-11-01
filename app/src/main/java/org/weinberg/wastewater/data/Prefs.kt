package org.weinberg.wastewater.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.doublePreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import org.weinberg.wastewater.analytics.Thresholds

private val Context.store: DataStore<Preferences> by preferencesDataStore("settings")

data class Settings(
    val selectedCity: String = Cities.defaultCity,
    val enabledCities: Set<String> = Cities.all.toSet(),
    val notifyCities: Set<String> = emptySet(),
    val highPercentile: Int = 75,
    val refLineEnabled: Boolean = false,
    val refLineValue: Double = 100_000.0,
    val showNationalReference: Boolean = true,
    val lastRefreshMillis: Long = 0L,
) {
    val thresholds get() = Thresholds(highPercentile = highPercentile)
}

class Prefs(private val context: Context) {
    private object K {
        val city = stringPreferencesKey("selectedCity")
        val enabled = stringSetPreferencesKey("enabledCities")
        val notify = stringSetPreferencesKey("notifyCities")
        val highPct = intPreferencesKey("highPercentile")
        val refOn = booleanPreferencesKey("refLineEnabled")
        val refVal = doublePreferencesKey("refLineValue")
        val national = booleanPreferencesKey("showNational")
        val lastRefresh = longPreferencesKey("lastRefresh")
        val phases = stringPreferencesKey("lastPhases")
    }

    val settings: Flow<Settings> = context.store.data.map { p ->
        Settings(
            selectedCity = p[K.city] ?: Cities.defaultCity,
            enabledCities = p[K.enabled] ?: Cities.all.toSet(),
            notifyCities = p[K.notify] ?: emptySet(),
            highPercentile = p[K.highPct] ?: 75,
            refLineEnabled = p[K.refOn] ?: false,
            refLineValue = p[K.refVal] ?: 100_000.0,
            showNationalReference = p[K.national] ?: true,
            lastRefreshMillis = p[K.lastRefresh] ?: 0L,
        )
    }

    suspend fun current(): Settings = settings.first()

    suspend fun setSelectedCity(id: String) = context.store.edit { it[K.city] = id }
    suspend fun setEnabledCities(ids: Set<String>) = context.store.edit { it[K.enabled] = ids }
    suspend fun setNotifyCities(ids: Set<String>) = context.store.edit { it[K.notify] = ids }
    suspend fun setHighPercentile(v: Int) = context.store.edit { it[K.highPct] = v.coerceIn(50, 95) }
    suspend fun setRefLine(enabled: Boolean, value: Double) = context.store.edit { it[K.refOn] = enabled; it[K.refVal] = value }
    suspend fun setShowNational(v: Boolean) = context.store.edit { it[K.national] = v }
    suspend fun markRefreshed(millis: Long = System.currentTimeMillis()) = context.store.edit { it[K.lastRefresh] = millis }

    /** Phase per city from the last background run, used to notify only on change. */
    suspend fun lastPhases(): Map<String, String> =
        (context.store.data.first()[K.phases] ?: "").split(',').filter { ':' in it }
            .associate { val (k, v) = it.split(':', limit = 2); k to v }
    suspend fun saveLastPhases(m: Map<String, String>) =
        context.store.edit { p -> p[K.phases] = m.entries.joinToString(",") { "${it.key}:${it.value}" } }
}
