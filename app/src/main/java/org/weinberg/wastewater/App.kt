package org.weinberg.wastewater

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import org.weinberg.wastewater.data.Prefs
import org.weinberg.wastewater.data.Repository
import org.weinberg.wastewater.work.RefreshWorker

class App : Application() {
    lateinit var repository: Repository
    lateinit var prefs: Prefs

    override fun onCreate() {
        super.onCreate()
        instance = this
        repository = Repository(this)
        prefs = Prefs(this)
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Wave state changes", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Sent when a city's wastewater trend changes state, if enabled for that city."
            })
        RefreshWorker.schedule(this)
    }

    companion object {
        const val CHANNEL_ID = "state_changes"
        lateinit var instance: App
    }
}
