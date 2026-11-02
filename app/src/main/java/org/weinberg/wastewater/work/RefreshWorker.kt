package org.weinberg.wastewater.work

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.glance.appwidget.updateAll
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import org.weinberg.wastewater.App
import org.weinberg.wastewater.R
import org.weinberg.wastewater.analytics.Alert
import org.weinberg.wastewater.analytics.Analytics
import org.weinberg.wastewater.analytics.Assessment
import org.weinberg.wastewater.data.Cities
import org.weinberg.wastewater.data.Pathogens
import org.weinberg.wastewater.ui.Fmt
import org.weinberg.wastewater.ui.MainActivity
import org.weinberg.wastewater.widget.WastewaterWidget
import java.util.concurrent.TimeUnit

class RefreshWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as App
        val result = app.repository.refresh()
        if (result.isFailure) return if (runAttemptCount < 3) Result.retry() else Result.failure()
        app.prefs.markRefreshed()

        val settings = app.prefs.current()
        val previous = app.prefs.lastPhases()
        val now = mutableMapOf<String, String>()
        for (city in Cities.all) {
            val p = app.repository.cached(city)?.pathogen(Pathogens.COVID) ?: continue
            val a = Analytics.assess(p, settings.thresholds)
            // phase / alert / threshold category / alarm status: any change is a state change
            val state = "${a.phase}/${a.alert}/${a.readout?.categoryIndex ?: "-"}/${if (a.alarm) a.readout?.status else "ok"}"
            now[city] = state
            val before = previous[city]
            if (before == null || before == state) continue
            val alarmBefore = before.substringAfterLast('/') != "ok"
            if (a.alarm && !alarmBefore) notifyAlarm(city, a)          // loud, regardless of the per-city switch
            else if (!a.alarm && city in settings.notifyCities) notify(city, a)
        }
        app.prefs.saveLastPhases(now)
        WastewaterWidget().updateAll(applicationContext)
        return Result.success()
    }

    private fun canNotify() = ContextCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun openIntent(city: String): PendingIntent = PendingIntent.getActivity(applicationContext, city.hashCode(),
        Intent(applicationContext, MainActivity::class.java).putExtra("city", city).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    private fun notify(city: String, a: Assessment) {
        if (!canNotify()) return
        val name = Cities.names[city] ?: city
        val r = a.readout?.takeIf { it.available }
        val title = when {
            a.alert == Alert.ALERT -> "$name: COVID entering a high period"
            a.alert == Alert.WATCH -> "$name: COVID wastewater is rising"
            r != null -> "$name: COVID ${r.shortLabel?.lowercase()} the threshold"
            else -> "$name: COVID wastewater ${a.phase.name.lowercase()}"
        }
        val body = buildString {
            if (r != null) {
                append(r.mainLine); r.confidenceLabel?.let { append(" ($it)") }
                r.prevalence?.let { append(". About ${Fmt.oneIn(it)} people infectious") }
                r.trend?.let { append(", $it") }
            } else {
                append(a.phase.name.lowercase().replaceFirstChar { it.uppercase() })
                a.growthPercentPerWeek?.let { append(", %+.0f%% per week".format(it)) }
                a.percentile?.let { append(", higher than %.0f%% of comparable weeks".format(it)) }
            }
        }
        val n = NotificationCompat.Builder(applicationContext, App.CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification).setContentTitle(title).setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(openIntent(city)).setAutoCancel(true).build()
        NotificationManagerCompat.from(applicationContext).notify(city.hashCode(), n)
    }

    private fun notifyAlarm(city: String, a: Assessment) {
        if (!canNotify()) return
        val name = Cities.names[city] ?: city
        val body = a.readout?.message ?: "The COVID readout for $name has been withheld (${a.readout?.status})."
        val n = NotificationCompat.Builder(applicationContext, App.ALARM_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification).setContentTitle("$name: wastewater data alarm")
            .setContentText(body).setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH).setCategory(NotificationCompat.CATEGORY_ERROR)
            .setContentIntent(openIntent(city)).setAutoCancel(true).build()
        NotificationManagerCompat.from(applicationContext).notify(city.hashCode() + 1, n)
    }

    companion object {
        private const val PERIODIC = "refresh-periodic"
        private const val NOW = "refresh-now"

        fun schedule(ctx: Context) {
            val constraints = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            val req = PeriodicWorkRequestBuilder<RefreshWorker>(12, TimeUnit.HOURS)
                .setConstraints(constraints).build()
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, req)
        }

        fun runNow(ctx: Context) {
            val req = OneTimeWorkRequestBuilder<RefreshWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build()
            WorkManager.getInstance(ctx).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE, req)
        }
    }
}
