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
import org.weinberg.wastewater.analytics.Phase
import org.weinberg.wastewater.data.Cities
import org.weinberg.wastewater.data.Pathogens
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
            val state = "${a.phase}/${a.alert}"
            now[city] = state
            val before = previous[city]
            if (before != null && before != state && city in settings.notifyCities) {
                notify(city, a.phase, a.alert, a.growthPercentPerWeek, a.percentile)
            }
        }
        app.prefs.saveLastPhases(now)
        WastewaterWidget().updateAll(applicationContext)
        return Result.success()
    }

    private fun notify(city: String, phase: Phase, alert: Alert, growth: Double?, pct: Double?) {
        val ctx = applicationContext
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val name = Cities.names[city] ?: city
        val title = when (alert) {
            Alert.ALERT -> "$name: COVID wastewater is high"
            Alert.WATCH -> "$name: COVID wastewater is rising"
            Alert.NONE -> "$name: COVID wastewater ${phase.name.lowercase()}"
        }
        val body = buildString {
            append(phase.name.lowercase().replaceFirstChar { it.uppercase() })
            growth?.let { append(", %+.0f%% per week".format(it)) }
            pct?.let { append(", higher than %.0f%% of past weeks".format(it)) }
        }
        val open = PendingIntent.getActivity(ctx, city.hashCode(),
            Intent(ctx, MainActivity::class.java).putExtra("city", city).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(ctx, App.CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification).setContentTitle(title).setContentText(body)
            .setContentIntent(open).setAutoCancel(true).build()
        NotificationManagerCompat.from(ctx).notify(city.hashCode(), n)
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
