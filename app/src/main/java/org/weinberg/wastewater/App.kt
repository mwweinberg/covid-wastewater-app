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
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "State changes", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Sent when a city's COVID threshold category, wave phase or alert changes, if enabled for that city."
            })
        nm.createNotificationChannel(
            NotificationChannel(ALARM_CHANNEL_ID, "Data alarms", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "A laboratory or method change has made the wastewater data non-comparable; the threshold readout is withheld until refit."
            })
        RefreshWorker.schedule(this)
    }

    companion object {
        const val CHANNEL_ID = "state_changes"
        const val ALARM_CHANNEL_ID = "data_alarms"
        lateinit var instance: App
    }
}
