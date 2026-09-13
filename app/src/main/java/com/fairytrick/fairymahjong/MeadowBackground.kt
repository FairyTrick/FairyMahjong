package com.fairytrick.fairymahjong

import android.content.Context
import android.content.res.Resources
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.LightingColorFilter
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.Shader
import android.graphics.drawable.Drawable
import kotlin.math.roundToInt

/** Static grass with foliage attached to the window corners at a consistent pixel scale. */
class MeadowBackground(context: Context) : Drawable() {
    private val bitmaps = MeadowBitmapCache.get(context)
    private val pixelScale = (1.5f * context.resources.displayMetrics.density)
        .roundToInt().coerceAtLeast(1).toFloat()
    private val grassShader = BitmapShader(bitmaps.grass, Shader.TileMode.MIRROR, Shader.TileMode.MIRROR)
    private val shaderTransform = Matrix()
    private val cornerTransforms = Array(4) { Matrix() }
    private val grassPaint = pixelPaint().apply { shader = grassShader }
    private val foliagePaint = pixelPaint()

    override fun onBoundsChange(bounds: Rect) {
        super.onBoundsChange(bounds)
        if (bounds.isEmpty) return

        // The texture starts at the window edge, independent of its size or aspect ratio.
        shaderTransform.setScale(pixelScale, pixelScale)
        shaderTransform.postTranslate(bounds.left.toFloat(), bounds.top.toFloat())
        grassShader.setLocalMatrix(shaderTransform)

        val foliageHeight = bitmaps.foliage.height * pixelScale
        val left = bounds.left.toFloat()
        val right = bounds.right.toFloat()
        val bottomOrigin = bounds.bottom - foliageHeight
        val topOrigin = bounds.top + foliageHeight
        // The source motif enters from the left and bottom; reflections attach it to each corner.
        placeCorner(0, pixelScale, pixelScale, left, bottomOrigin)
        placeCorner(1, -pixelScale, pixelScale, right, bottomOrigin)
        placeCorner(2, pixelScale, -pixelScale, left, topOrigin)
        placeCorner(3, -pixelScale, -pixelScale, right, topOrigin)
    }

    override fun draw(canvas: Canvas) {
        if (bounds.isEmpty || grassPaint.alpha == 0) return
        val checkpoint = canvas.save()
        canvas.clipRect(bounds)
        canvas.drawRect(bounds, grassPaint)
        for (index in cornerTransforms.indices) {
            canvas.drawBitmap(bitmaps.foliage, cornerTransforms[index], foliagePaint)
        }
        canvas.restoreToCount(checkpoint)
    }

    override fun setAlpha(alpha: Int) {
        val clamped = alpha.coerceIn(0, 255)
        if (grassPaint.alpha == clamped) return
        grassPaint.alpha = clamped
        foliagePaint.alpha = clamped
        invalidateSelf()
    }

    override fun getAlpha(): Int = grassPaint.alpha

    override fun setColorFilter(colorFilter: ColorFilter?) {
        val filter = colorFilter ?: MEADOW_SHADE
        grassPaint.colorFilter = filter
        foliagePaint.colorFilter = filter
        invalidateSelf()
    }

    override fun getColorFilter(): ColorFilter? = grassPaint.colorFilter

    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun getOpacity(): Int =
        if (grassPaint.alpha == 255 && !bitmaps.grass.hasAlpha()) PixelFormat.OPAQUE
        else PixelFormat.TRANSLUCENT

    private fun placeCorner(index: Int, scaleX: Float, scaleY: Float, x: Float, y: Float) {
        cornerTransforms[index].setScale(scaleX, scaleY)
        cornerTransforms[index].postTranslate(x, y)
    }

    private fun pixelPaint() = Paint().apply {
        isAntiAlias = false
        isFilterBitmap = false
        isDither = false
        colorFilter = MEADOW_SHADE
    }

    private companion object {
        // Equivalent to a 12% black wash, without a separate overlay draw.
        val MEADOW_SHADE = LightingColorFilter(Color.rgb(224, 224, 224), Color.BLACK)
    }
}

private data class MeadowBitmaps(val grass: Bitmap, val foliage: Bitmap)

/** Retains only sampled pixels; shader scale and placement belong to each drawable. */
private object MeadowBitmapCache {
    @Volatile private var cached: MeadowBitmaps? = null

    fun get(context: Context): MeadowBitmaps = cached ?: synchronized(this) {
        cached ?: context.applicationContext.resources.let { resources ->
            MeadowBitmaps(
                grass = decode(resources, R.drawable.meadow_grass_v2, sampleSize = 4),
                foliage = decode(resources, R.drawable.meadow_foliage_v2, sampleSize = 8),
            )
        }.also { cached = it }
    }

    private fun decode(resources: Resources, drawableId: Int, sampleSize: Int): Bitmap = checkNotNull(
        BitmapFactory.decodeResource(
            resources,
            drawableId,
            BitmapFactory.Options().apply {
                inScaled = false
                inSampleSize = sampleSize
                inPreferredConfig = Bitmap.Config.ARGB_8888
            },
        ),
    ) { "A meadow background layer could not be decoded" }
}
