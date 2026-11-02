package org.weinberg.wastewater.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import org.weinberg.wastewater.App
import org.weinberg.wastewater.analytics.Analytics
import org.weinberg.wastewater.analytics.Assessment
import org.weinberg.wastewater.data.CityData
import org.weinberg.wastewater.data.Pathogens
import org.weinberg.wastewater.data.Settings
import org.weinberg.wastewater.widget.WastewaterWidget
import androidx.glance.appwidget.updateAll

data class UiState(
    val settings: Settings = Settings(),
    val cities: Map<String, CityData> = emptyMap(),
    val refreshing: Boolean = false,
    val error: String? = null,
) {
    val city: CityData? get() = cities[settings.selectedCity]
    fun assessment(pathogenId: String): Assessment? = city?.pathogen(pathogenId)?.let { Analytics.assess(it, settings.thresholds) }
}

class MainViewModel(app: Application) : AndroidViewModel(app) {
    private val repo = (app as App).repository
    val prefs = (app as App).prefs
    private val refreshing = MutableStateFlow(false)
    val selectedPathogen = MutableStateFlow(Pathogens.COVID)

    val state: StateFlow<UiState> = combine(prefs.settings, repo.cities, refreshing, repo.lastError) { s, c, r, e ->
        UiState(s, c, r, e)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), UiState())

    init { refresh() }

    fun refresh() = viewModelScope.launch {
        if (refreshing.value) return@launch
        refreshing.value = true
        val r = repo.refresh()
        if (r.isSuccess) { prefs.markRefreshed(); WastewaterWidget().updateAll(getApplication()) }
        refreshing.value = false
    }

    fun selectCity(id: String) = viewModelScope.launch { prefs.setSelectedCity(id); selectedPathogen.value = Pathogens.COVID }
    fun setEnabledCities(ids: Set<String>) = viewModelScope.launch { prefs.setEnabledCities(ids) }
    fun setNotifyCities(ids: Set<String>) = viewModelScope.launch { prefs.setNotifyCities(ids) }
    fun setHighPercentile(v: Int) = viewModelScope.launch { prefs.setHighPercentile(v); WastewaterWidget().updateAll(getApplication()) }
    fun setShowNational(v: Boolean) = viewModelScope.launch { prefs.setShowNational(v) }
}
