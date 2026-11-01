package org.weinberg.wastewater.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.weinberg.wastewater.data.WeekPoint
import java.time.LocalDate
import java.time.temporal.ChronoUnit
import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.pow

data class Line(val points: List<Pair<LocalDate, Double>>, val color: Color, val width: Dp = 1.dp, val dashed: Boolean = false)
data class HLine(val value: Double, val color: Color, val label: String, val dashed: Boolean = true)

/**
 * Time series chart: log y for concentrations, linear for CDC levels.
 * Shades the band above [highMark], draws raw samples as dots, vertical markers for lab changes.
 */
@Composable
fun LevelChart(
    main: Line,
    extras: List<Line> = emptyList(),
    dots: List<Pair<LocalDate, Double>> = emptyList(),
    hLines: List<HLine> = emptyList(),
    highMark: Double? = null,
    highColor: Color = Palette.peaking,
    markers: List<Pair<LocalDate, String>> = emptyList(),
    logScale: Boolean = true,
    from: LocalDate, to: LocalDate,
    concentration: Boolean = true,
    height: Dp = 220.dp,
    modifier: Modifier = Modifier,
) {
    val measurer = rememberTextMeasurer()
    val axis = MaterialTheme.colorScheme.onSurfaceVariant
    val grid = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)
    val labelStyle = TextStyle(fontSize = 10.sp, color = axis)

    Canvas(modifier.fillMaxWidth().height(height)) {
        val left = 44.dp.toPx(); val bottom = 18.dp.toPx(); val top = 6.dp.toPx(); val right = 8.dp.toPx()
        val plot = Rect(left, top, size.width - right, size.height - bottom)

        val all = (listOf(main) + extras).flatMap { l -> l.points.filter { it.first in from..to }.map { it.second } } +
            dots.filter { it.first in from..to }.map { it.second } + hLines.map { it.value } + listOfNotNull(highMark)
        val pos = all.filter { it > 0 }
        if (pos.size < 2) return@Canvas
        var lo = pos.min(); var hi = pos.max()
        if (logScale) { lo = 10.0.pow(floor(log10(lo))); hi = 10.0.pow(floor(log10(hi)) + 1) } else { lo = 0.0; hi = (floor(hi / 2) + 1) * 2 }
        if (hi <= lo) hi = lo + 1

        fun ty(v: Double): Float {
            val f = if (logScale) (log10(v.coerceAtLeast(lo)) - log10(lo)) / (log10(hi) - log10(lo)) else (v - lo) / (hi - lo)
            return plot.bottom - (f.toFloat().coerceIn(0f, 1f)) * plot.height
        }
        val days = ChronoUnit.DAYS.between(from, to).coerceAtLeast(1)
        fun tx(d: LocalDate) = plot.left + (ChronoUnit.DAYS.between(from, d).toFloat() / days) * plot.width

        // y grid + labels
        val ticks: List<Double> = if (logScale) {
            generateSequence(lo) { it * 10 }.takeWhile { it <= hi }.toList()
        } else (0..5).map { lo + (hi - lo) * it / 5 }
        ticks.forEach { v ->
            val y = ty(v)
            drawLine(grid, Offset(plot.left, y), Offset(plot.right, y), 1f)
            val t = measurer.measure(Fmt.level(v, concentration), labelStyle)
            drawText(t, topLeft = Offset(plot.left - t.size.width - 4.dp.toPx(), y - t.size.height / 2))
        }
        // x labels: month starts, thinned
        var d = from.withDayOfMonth(1).plusMonths(1)
        val step = when { days > 900 -> 6L; days > 400 -> 3L; days > 150 -> 1L; else -> 1L }
        var k = 0
        while (d <= to) {
            if (k % step == 0L) {
                val x = tx(d)
                drawLine(grid, Offset(x, plot.top), Offset(x, plot.bottom), 1f)
                val lbl = if (days > 400) (if (d.monthValue == 1) d.year.toString() else d.month.name.take(3).lowercase().replaceFirstChar { it.uppercase() }) else d.month.name.take(3).lowercase().replaceFirstChar { it.uppercase() }
                val t = measurer.measure(lbl, labelStyle)
                drawText(t, topLeft = Offset(x - t.size.width / 2, plot.bottom + 3.dp.toPx()))
            }
            d = d.plusMonths(1); k++
        }

        // high band
        highMark?.let { hm ->
            val y = ty(hm)
            drawRect(highColor.copy(alpha = 0.08f), Offset(plot.left, plot.top), Size(plot.width, (y - plot.top).coerceAtLeast(0f)))
            drawLine(highColor.copy(alpha = 0.7f), Offset(plot.left, y), Offset(plot.right, y), 1.5f,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 6f)))
        }
        hLines.forEach { h ->
            val y = ty(h.value)
            drawLine(h.color, Offset(plot.left, y), Offset(plot.right, y), 1.5f,
                pathEffect = if (h.dashed) PathEffect.dashPathEffect(floatArrayOf(4f, 6f)) else null)
            val t = measurer.measure(h.label, labelStyle.copy(color = h.color))
            drawText(t, topLeft = Offset(plot.right - t.size.width - 2.dp.toPx(), y - t.size.height - 1.dp.toPx()))
        }
        // markers
        markers.filter { it.first in from..to }.forEach { (md, label) ->
            val x = tx(md)
            drawLine(axis.copy(alpha = 0.6f), Offset(x, plot.top), Offset(x, plot.bottom), 1f, pathEffect = PathEffect.dashPathEffect(floatArrayOf(3f, 5f)))
            val t = measurer.measure(label, labelStyle)
            drawText(t, topLeft = Offset(x + 2.dp.toPx(), plot.top))
        }
        // lines
        extras.forEach { drawSeries(it, from, to, ::tx, ::ty) }
        drawSeries(main, from, to, ::tx, ::ty)
        // dots
        dots.filter { it.first in from..to && it.second > 0 }.forEach { (dd, v) ->
            drawCircle(main.color.copy(alpha = 0.55f), 2.2.dp.toPx(), Offset(tx(dd), ty(v)))
        }
    }
}

private fun DrawScope.drawSeries(l: Line, from: LocalDate, to: LocalDate, tx: (LocalDate) -> Float, ty: (Double) -> Float) {
    val pts = l.points.filter { it.first in from..to && it.second > 0 }
    if (pts.size < 2) return
    val path = Path()
    pts.forEachIndexed { i, (d, v) -> if (i == 0) path.moveTo(tx(d), ty(v)) else path.lineTo(tx(d), ty(v)) }
    drawPath(path, l.color, style = Stroke(width = l.width.toPx(), pathEffect = if (l.dashed) PathEffect.dashPathEffect(floatArrayOf(6f, 6f)) else null))
}

/** Each year as a line over week-of-year; the current year bold. */
@Composable
fun YearOverlayChart(weekly: List<WeekPoint>, logScale: Boolean, concentration: Boolean, highlightColor: Color, modifier: Modifier = Modifier, height: Dp = 200.dp) {
    val measurer = rememberTextMeasurer()
    val axis = MaterialTheme.colorScheme.onSurfaceVariant
    val grid = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)
    val labelStyle = TextStyle(fontSize = 10.sp, color = axis)
    val byYear = weekly.filter { it.value != null && it.value > 0 }
        .groupBy { LocalDate.parse(it.weekEnd).year }
        .mapValues { (_, v) -> v.map { LocalDate.parse(it.weekEnd).dayOfYear to it.value!! } }
    val years = byYear.keys.sorted()
    val fades = listOf(0.25f, 0.4f, 0.55f, 0.7f)

    Canvas(modifier.fillMaxWidth().height(height)) {
        val left = 44.dp.toPx(); val bottom = 18.dp.toPx(); val top = 6.dp.toPx(); val right = 8.dp.toPx()
        val plot = Rect(left, top, size.width - right, size.height - bottom)
        val all = byYear.values.flatten().map { it.second }
        if (all.size < 2) return@Canvas
        var lo = all.min(); var hi = all.max()
        if (logScale) { lo = 10.0.pow(floor(log10(lo))); hi = 10.0.pow(floor(log10(hi)) + 1) } else { lo = 0.0; hi = (floor(hi / 2) + 1) * 2 }
        fun ty(v: Double): Float {
            val f = if (logScale) (log10(v.coerceAtLeast(lo)) - log10(lo)) / (log10(hi) - log10(lo)) else (v - lo) / (hi - lo)
            return plot.bottom - f.toFloat().coerceIn(0f, 1f) * plot.height
        }
        fun tx(doy: Int) = plot.left + (doy - 1) / 365f * plot.width
        val ticks: List<Double> = if (logScale) generateSequence(lo) { it * 10 }.takeWhile { it <= hi }.toList() else (0..5).map { lo + (hi - lo) * it / 5 }
        ticks.forEach { v ->
            val y = ty(v); drawLine(grid, Offset(plot.left, y), Offset(plot.right, y), 1f)
            val t = measurer.measure(Fmt.level(v, concentration), labelStyle)
            drawText(t, topLeft = Offset(plot.left - t.size.width - 4.dp.toPx(), y - t.size.height / 2))
        }
        listOf("Jan", "Mar", "May", "Jul", "Sep", "Nov").forEachIndexed { i, m ->
            val x = tx(1 + i * 61); drawLine(grid, Offset(x, plot.top), Offset(x, plot.bottom), 1f)
            val t = measurer.measure(m, labelStyle); drawText(t, topLeft = Offset(x - t.size.width / 2, plot.bottom + 3.dp.toPx()))
        }
        years.forEachIndexed { idx, y ->
            val pts = byYear[y]!!.sortedBy { it.first }
            if (pts.size < 2) return@forEachIndexed
            val current = idx == years.lastIndex
            val color = if (current) highlightColor else axis.copy(alpha = fades.getOrElse(idx - (years.size - 1 - fades.size)) { 0.25f })
            val path = Path()
            pts.forEachIndexed { i, (doy, v) -> if (i == 0) path.moveTo(tx(doy), ty(v)) else path.lineTo(tx(doy), ty(v)) }
            drawPath(path, color, style = Stroke(if (current) 3.dp.toPx() else 1.2.dp.toPx()))
            val last = pts.last()
            val t = measurer.measure(y.toString(), labelStyle.copy(color = color))
            drawText(t, topLeft = Offset((tx(last.first) + 3.dp.toPx()).coerceAtMost(plot.right - t.size.width), ty(last.second) - t.size.height / 2))
        }
    }
}
