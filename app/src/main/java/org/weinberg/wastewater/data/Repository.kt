package org.weinberg.wastewater.data

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import org.weinberg.wastewater.BuildConfig
import java.io.File
import java.util.concurrent.TimeUnit

/** Fetches per-city JSON from the data branch and keeps a copy on disk so the widget and app work offline. */
class Repository(context: Context) {
    private val dir = File(context.filesDir, "data").apply { mkdirs() }
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }
    private val http = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build()

    private val _cities = MutableStateFlow(loadCached())
    val cities: StateFlow<Map<String, CityData>> = _cities

    private val _lastError = MutableStateFlow<String?>(null)
    val lastError: StateFlow<String?> = _lastError

    fun cached(cityId: String): CityData? = _cities.value[cityId]

    private fun loadCached(): Map<String, CityData> = Cities.all.mapNotNull { id ->
        val f = File(dir, "$id.json")
        if (!f.exists()) null else runCatching { id to json.decodeFromString<CityData>(f.readText()) }
            .onFailure { Log.w(TAG, "bad cache for $id", it) }.getOrNull()
    }.toMap()

    /** Downloads every city. Returns the ids that were refreshed; a single failing city does not fail the others. */
    suspend fun refresh(): Result<Set<String>> = withContext(Dispatchers.IO) {
        val ok = mutableSetOf<String>()
        var firstError: Throwable? = null
        for (id in Cities.all) {
            runCatching {
                val body = get("$id.json")
                val city = json.decodeFromString<CityData>(body)      // validate before overwriting the cache
                File(dir, "$id.json").writeText(body)
                city
            }.onSuccess { city ->
                _cities.value = _cities.value + (id to city); ok += id
            }.onFailure { e ->
                Log.w(TAG, "refresh $id failed", e); if (firstError == null) firstError = e
            }
        }
        _lastError.value = firstError?.let { it.message ?: it.javaClass.simpleName }?.takeIf { ok.isEmpty() }
        if (ok.isEmpty() && firstError != null) Result.failure(firstError!!) else Result.success(ok)
    }

    private fun get(path: String): String {
        val req = Request.Builder().url(BuildConfig.DATA_BASE_URL + path)
            .header("User-Agent", "WastewaterWatch/${BuildConfig.VERSION_NAME}")
            .header("Cache-Control", "no-cache").build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) error("HTTP ${resp.code} for $path")
            return resp.body!!.string()
        }
    }

    companion object { private const val TAG = "Repository" }
}
