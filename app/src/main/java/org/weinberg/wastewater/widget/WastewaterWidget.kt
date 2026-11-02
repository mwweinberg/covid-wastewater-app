package org.weinberg.wastewater.widget

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Paint
import android.graphics.Path
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalSize
import androidx.glance.action.actionParametersOf
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.currentState
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.ContentScale
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.width
import androidx.glance.state.PreferencesGlanceStateDefinition
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextAlign
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import org.weinberg.wastewater.App
import org.weinberg.wastewater.analytics.Alert
import org.weinberg.wastewater.analytics.Analytics
import org.weinberg.wastewater.analytics.Assessment
import org.weinberg.wastewater.data.Cities
import org.weinberg.wastewater.data.Pathogens
import org.weinberg.wastewater.data.WeekPoint
import org.weinberg.wastewater.ui.Fmt
import org.weinberg.wastewater.ui.MainActivity
import org.weinberg.wastewater.ui.Palette
import kotlin.math.ln

val CITY_KEY = stringPreferencesKey("city")

class WastewaterWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = WastewaterWidget()
}

class WastewaterWidget : GlanceAppWidget() {
    companion object {
        /** One launcher cell: category word and "1 in N" only. */
        val TINY = DpSize(40.dp, 40.dp)
        val SMALL = DpSize(110.dp, 40.dp)
        val MEDIUM = DpSize(180.dp, 100.dp)
        val LARGE = DpSize(250.dp, 150.dp)
    }

    override val sizeMode = SizeMode.Responsive(setOf(TINY, SMALL, MEDIUM, LARGE))
    override val stateDefinition = PreferencesGlanceStateDefinition

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val app = context.applicationContext as App
        val settings = app.prefs.current()
        provideContent {
            val prefs = currentState<Preferences>()
            val cityId = prefs[CITY_KEY] ?: settings.selectedCity
            val city = app.repository.cached(cityId)
            val p = city?.pathogen(Pathogens.COVID)
            val a = p?.let { Analytics.assess(it, settings.thresholds) }
            GlanceTheme { Content(cityId, a, p?.weekly ?: emptyList(), p?.isConcentration ?: true) }
        }
    }

    @Composable
    private fun Content(cityId: String, a: Assessment?, weekly: List<WeekPoint>, concentration: Boolean) {
        val size = LocalSize.current
        val r = a?.readout?.takeIf { it.available }
        val accent = when {
            a == null -> Palette.unknown
            a.alarm -> Palette.alarm
            a.alert != Alert.NONE -> Palette.alert(a.alert, a.phase)
            r != null -> Palette.band(r.categoryIndex)
            else -> Palette.phase(a.phase)
        }
        val bg = ColorProvider(accent.copy(alpha = 0.18f).compositeOver(Color(0xFF14181F)))
        val onBg = ColorProvider(Color.White)
        val sub = ColorProvider(Color(0xFFC9D1DC))
        val name = Cities.names[cityId] ?: cityId
        val code = Cities.codes[cityId] ?: cityId.uppercase()
        val open = actionStartActivity<MainActivity>(actionParametersOf(CityParam to cityId))
        val tiny = size.width < SMALL.width

        Box(GlanceModifier.fillMaxSize().background(bg).cornerRadius(if (tiny) 16.dp else 20.dp).clickable(open).padding(if (tiny) 6.dp else 10.dp)) {
            if (a == null) { Text(if (tiny) "$code\nno data" else "$name: no data yet", style = TextStyle(color = onBg, fontSize = 11.sp, textAlign = TextAlign.Center)); return@Box }
            if (a.alarm) {
                Column(GlanceModifier.fillMaxSize(), verticalAlignment = Alignment.CenterVertically, horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(if (tiny) code else name, style = TextStyle(color = sub, fontSize = 10.sp))
                    Text(if (tiny) "DATA\nALARM" else "Data alarm: readout withheld", style = TextStyle(color = ColorProvider(Palette.alarm.lighten()), fontSize = if (tiny) 12.sp else 14.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center))
                    if (!tiny) Text(a.readout?.status ?: "", style = TextStyle(color = sub, fontSize = 10.sp))
                }
                return@Box
            }
            val headline = when { a.belowDetection -> "Below detection"; a.atFloor -> "At minimum"; else -> Fmt.level(a.latestValue, concentration) }
            val arrow = arrowFor(a)
            val oneIn = r?.prevalence?.let { Fmt.oneIn(it) }
            val readoutLine = r?.let { listOfNotNull(it.shortLabel, oneIn).joinToString(" · ") }
            when {
                tiny -> Column(GlanceModifier.fillMaxSize(), verticalAlignment = Alignment.CenterVertically, horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(code, style = TextStyle(color = sub, fontSize = 9.sp))
                    if (r != null) {
                        Text(oneIn ?: "–", style = TextStyle(color = onBg, fontSize = 12.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center))
                        Text(r.shortLabel ?: "", style = TextStyle(color = ColorProvider(accent.lighten()), fontSize = 9.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.Center))
                    } else {
                        Text("$headline $arrow", style = TextStyle(color = onBg, fontSize = 12.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center))
                        Text(Fmt.phaseLabel(a.phase), style = TextStyle(color = ColorProvider(accent.lighten()), fontSize = 9.sp, textAlign = TextAlign.Center))
                    }
                }
                size.height < MEDIUM.height -> Row(GlanceModifier.fillMaxSize(), verticalAlignment = Alignment.CenterVertically) {
                    Dot(accent)
                    Spacer(GlanceModifier.width(8.dp))
                    Column {
                        Text(name, style = TextStyle(color = sub, fontSize = 11.sp))
                        Text(readoutLine ?: "$headline $arrow", style = TextStyle(color = onBg, fontSize = 15.sp, fontWeight = FontWeight.Bold))
                    }
                }
                else -> Column(GlanceModifier.fillMaxSize()) {
                    Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Dot(accent); Spacer(GlanceModifier.width(6.dp))
                        Text("$name · COVID", style = TextStyle(color = sub, fontSize = 11.sp))
                    }
                    Spacer(GlanceModifier.height(2.dp))
                    if (r != null) {
                        Text("${r.shortLabel} the threshold" + (if (r.projected) " (est. today)" else ""), style = TextStyle(color = ColorProvider(Palette.band(r.categoryIndex).lighten()), fontSize = 16.sp, fontWeight = FontWeight.Bold))
                        Text(listOfNotNull(oneIn?.let { "About $it infectious" }, r.confidenceLabel?.lowercase()).joinToString(" · "), style = TextStyle(color = onBg, fontSize = 12.sp))
                        Spacer(GlanceModifier.height(2.dp))
                    }
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text(headline, style = TextStyle(color = onBg, fontSize = if (r != null) 18.sp else 24.sp, fontWeight = FontWeight.Bold))
                        Spacer(GlanceModifier.width(6.dp))
                        Text("$arrow ${Fmt.growth(a.growthPercentPerWeek)}/wk", style = TextStyle(color = sub, fontSize = 12.sp))
                    }
                    Text("${if (a.alert == Alert.NONE) Fmt.phaseLabel(a.phase) else Fmt.alertLabel(a.alert)} · above ${Fmt.pct(a.percentile)} of weeks",
                        style = TextStyle(color = ColorProvider(Palette.alert(a.alert, a.phase).lighten()), fontSize = 12.sp, fontWeight = FontWeight.Medium))
                    if (size.height >= LARGE.height) {
                        Spacer(GlanceModifier.height(4.dp))
                        val mark = r?.bandEdgesLevel?.getOrNull(1) ?: a.highMark
                        val bmp = sparkline(weekly, mark, concentration, accent.toArgb(), (size.width.value * 2.5).toInt(), 110)
                        Image(ImageProvider(bmp), contentDescription = "last 6 months", modifier = GlanceModifier.fillMaxWidth().height(44.dp), contentScale = ContentScale.FillBounds)
                    }
                    Spacer(GlanceModifier.height(2.dp))
                    val foot = if (r != null) "samples through ${Fmt.date(r.asOf)}" + (if (r.dataDelayed) " · delayed" else "") + (if (r.provisional) " · provisional" else "")
                               else "sample ${Fmt.date(a.latestWeekEnd)}" + if (a.isStale) " · stale" else ""
                    Text(foot, style = TextStyle(color = sub, fontSize = 10.sp))
                }
            }
        }
    }

    @Composable
    private fun Dot(color: Color) {
        Box(GlanceModifier.width(10.dp).height(10.dp).background(ColorProvider(color)).cornerRadius(5.dp)) {}
    }

    private fun arrowFor(a: Assessment): String = when {
        (a.weeklyGrowth ?: 0.0) >= 0.10 -> "↑"
        (a.weeklyGrowth ?: 0.0) <= -0.07 -> "↓"
        else -> "→"
    }

    /** Last 26 weeks, log scale for concentrations, threshold edge (or high mark) as dashed line. */
    private fun sparkline(weekly: List<WeekPoint>, mark: Double?, concentration: Boolean, color: Int, w: Int, h: Int): Bitmap {
        val bmp = Bitmap.createBitmap(w.coerceAtLeast(50), h, Bitmap.Config.ARGB_8888)
        val c = android.graphics.Canvas(bmp)
        val pts = weekly.takeLast(26).mapNotNull { it.value?.takeIf { v -> v > 0 } }
        if (pts.size < 2) return bmp
        fun f(v: Double) = if (concentration) ln(v) else v
        val vals = pts.map(::f) + listOfNotNull(mark?.let(::f))
        val lo = vals.min(); val hi = vals.max().let { if (it - lo < 1e-9) lo + 1 else it }
        fun y(v: Double) = (h - 4) - ((f(v) - lo) / (hi - lo) * (h - 8)).toFloat()
        mark?.let { hm ->
            c.drawLine(0f, y(hm), w.toFloat(), y(hm), Paint().apply { this.color = color; alpha = 110; strokeWidth = 2f; pathEffect = android.graphics.DashPathEffect(floatArrayOf(8f, 8f), 0f) })
        }
        val path = Path()
        pts.forEachIndexed { i, v -> val x = i.toFloat() / (pts.size - 1) * w; if (i == 0) path.moveTo(x, y(v)) else path.lineTo(x, y(v)) }
        c.drawPath(path, Paint().apply { this.color = color; style = Paint.Style.STROKE; strokeWidth = 4f; isAntiAlias = true; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND })
        return bmp
    }
}

val CityParam = androidx.glance.action.ActionParameters.Key<String>("city")

private fun Color.compositeOver(bg: Color): Color {
    val a = alpha
    return Color(red * a + bg.red * (1 - a), green * a + bg.green * (1 - a), blue * a + bg.blue * (1 - a), 1f)
}

private fun Color.lighten(): Color = Color(
    (red + (1 - red) * 0.35f), (green + (1 - green) * 0.35f), (blue + (1 - blue) * 0.35f), 1f)
