package com.fairytrick.fairymahjong

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.LightingColorFilter
import android.graphics.Outline
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.drawable.Drawable
import android.os.Build
import kotlin.math.max
import kotlin.math.min

/** Selected Cool limestone pixels with independent native pressed state per tile. */
class HairlineTileDrawable(context: Context) : Drawable() {
    private val asset = HairlineTileCache.get(context)
    private val silhouette = Path()
    private val paint = Paint().apply {
        isAntiAlias = false
        isFilterBitmap = false
        isDither = false
    }
    private var pressed = false
    private var customFilter: ColorFilter? = null

    override fun onBoundsChange(bounds: Rect) {
        super.onBoundsChange(bounds)
        val x = bounds.left.toFloat()
        val y = bounds.top.toFloat()
        val width = bounds.width().toFloat()
        val height = bounds.height().toFloat()
        // Calibrated against Cool limestone's visible bounds (224,195)-(922,1177).
        // Follow the clipped corners and the exposed lower/right blue backing.
        silhouette.rewind()
        silhouette.moveTo(x + width * 0.072f, y)
        silhouette.lineTo(x + width * 0.899f, y)
        silhouette.lineTo(x + width, y + height * 0.084f)
        silhouette.lineTo(x + width, y + height * 0.949f)
        silhouette.lineTo(x + width * 0.938f, y + height)
        silhouette.lineTo(x + width * 0.113f, y + height)
        silhouette.lineTo(x, y + height * 0.891f)
        silhouette.lineTo(x, y + height * 0.055f)
        silhouette.close()
    }

    override fun draw(canvas: Canvas) {
        if (!bounds.isEmpty) canvas.drawBitmap(asset.bitmap, asset.sourceBounds, bounds, paint)
    }

    override fun isStateful(): Boolean = true

    override fun onStateChange(state: IntArray): Boolean {
        val nextPressed = android.R.attr.state_enabled in state && android.R.attr.state_pressed in state
        if (pressed == nextPressed) return false
        pressed = nextPressed
        updateFilter()
        return true
    }

    override fun setAlpha(alpha: Int) {
        val clamped = alpha.coerceIn(0, 255)
        if (paint.alpha == clamped) return
        paint.alpha = clamped
        invalidateSelf()
    }

    override fun getAlpha(): Int = paint.alpha

    override fun setColorFilter(colorFilter: ColorFilter?) {
        customFilter = colorFilter
        updateFilter()
    }

    override fun getColorFilter(): ColorFilter? = customFilter

    @Suppress("DEPRECATION")
    override fun getOutline(outline: Outline) {
        if (bounds.isEmpty) {
            outline.setEmpty()
        } else {
            if (Build.VERSION.SDK_INT >= 30) outline.setPath(silhouette)
            else outline.setConvexPath(silhouette)
            outline.alpha = paint.alpha / 255f
        }
    }

    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT

    private fun updateFilter() {
        paint.colorFilter = customFilter ?: if (pressed) PRESSED_TINT else null
        invalidateSelf()
    }

    private companion object {
        val PRESSED_TINT = LightingColorFilter(Color.rgb(235, 235, 235), Color.BLACK)
    }
}

private data class HairlineTileAsset(val bitmap: Bitmap, val sourceBounds: Rect)

/** Decode and inspect alpha once per process, retaining no Context or resource owner. */
private object HairlineTileCache {
    @Volatile private var cached: HairlineTileAsset? = null

    fun get(context: Context): HairlineTileAsset = cached ?: synchronized(this) {
        cached ?: load(context).also { cached = it }
    }

    private fun load(context: Context): HairlineTileAsset {
        val resources = context.applicationContext.resources
        val dimensions = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeResource(resources, R.drawable.tile_body_cool_limestone_v1, dimensions)
        var sampleSize = 1
        val longestSide = max(dimensions.outWidth, dimensions.outHeight)
        while (longestSide / (sampleSize * 2) >= 256) sampleSize *= 2
        val bitmap = checkNotNull(BitmapFactory.decodeResource(
            resources,
            R.drawable.tile_body_cool_limestone_v1,
            BitmapFactory.Options().apply {
                inScaled = false
                inSampleSize = sampleSize
                inPreferredConfig = Bitmap.Config.ARGB_8888
            },
        )) { "The Cool limestone tile body could not be decoded" }

        var left = bitmap.width
        var top = bitmap.height
        var right = -1
        var bottom = -1
        val row = IntArray(bitmap.width)
        for (y in 0 until bitmap.height) {
            bitmap.getPixels(row, 0, bitmap.width, 0, y, bitmap.width, 1)
            for (x in row.indices) {
                // Ignore almost invisible mask noise while keeping the sprite pixels unchanged.
                if ((row[x] ushr 24) >= 16) {
                    left = min(left, x)
                    top = min(top, y)
                    right = max(right, x)
                    bottom = y
                }
            }
        }
        check(right >= left && bottom >= top) { "The Cool limestone tile body is empty" }
        return HairlineTileAsset(bitmap, Rect(left, top, right + 1, bottom + 1))
    }
}
