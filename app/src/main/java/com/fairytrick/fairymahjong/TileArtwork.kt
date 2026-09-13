package com.fairytrick.fairymahjong

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.LightingColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.drawable.Drawable
import android.util.LruCache
import android.view.View
import android.widget.ImageView
import org.json.JSONObject
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.min
import kotlin.math.roundToInt

/** Shared face placement for board tiles and the four hand slots. */
internal object TileArtwork {
    // Measured from the selected Cool limestone sprite, excluding its gray/blue sidewalls.
    const val FACE_WIDTH_FRACTION = 0.868f
    const val FACE_HEIGHT_FRACTION = 0.891f
    const val COLUMN_PITCH_FRACTION = 0.905f
    const val ROW_PITCH_FRACTION = 0.930f
    private val legacyIcons = listOf(R.drawable.tile_bamboo, R.drawable.tile_flower, R.drawable.tile_sun,
        R.drawable.tile_waves, R.drawable.tile_leaf, R.drawable.tile_star)
    private val legacyColors = listOf(Color.rgb(35, 112, 69), Color.rgb(161, 77, 101), Color.rgb(174, 124, 39),
        Color.rgb(38, 111, 132), Color.rgb(92, 120, 50), Color.rgb(131, 88, 153))

    fun background(context: Context): Drawable = HairlineTileDrawable(context)

    fun bind(view: ImageView, faceId: Int) {
        val face = TileCatalog.face(faceId)
        view.tag = faceId
        if (face.assetPath == null) {
            view.scaleType = ImageView.ScaleType.FIT_CENTER
            view.setImageResource(legacyIcons[faceId])
            view.imageTintList = ColorStateList.valueOf(legacyColors[faceId])
        } else {
            // Independent drawable state, shared decoded pixels. Never tint colored portraits.
            view.imageTintList = null
            view.scaleType = ImageView.ScaleType.FIT_XY
            view.setImageDrawable(FairyFaceDrawable(FairyBitmapCache.get(view.context, face)))
        }
        if (view.width > 0 && view.height > 0) applyIconPadding(view, view.width, view.height)
        else view.post { if (view.tag == faceId) applyIconPadding(view, view.width, view.height) }
    }

    fun applyIconPadding(view: View, widthPx: Int, heightPx: Int) {
        if (widthPx <= 0 || heightPx <= 0) return
        val rightWall = (widthPx * (1f - FACE_WIDTH_FRACTION)).roundToInt()
        val bottomWall = (heightPx * (1f - FACE_HEIGHT_FRACTION)).roundToInt()
        val fairy = (view.tag as? Int)?.let { TileCatalog.face(it).assetPath != null } == true
        if (fairy) {
            // Keep pictures on the clean face, inside the bevel and clear of the blue backing.
            view.setPadding((widthPx * 0.035f).roundToInt(), (heightPx * 0.025f).roundToInt(),
                (widthPx * 0.142f).roundToInt(), (heightPx * 0.118f).roundToInt())
            return
        }
        val horizontalInset = ((widthPx - rightWall) * 0.15f).roundToInt()
        val verticalInset = ((heightPx - bottomWall) * 0.145f).roundToInt()
        view.setPadding(horizontalInset, verticalInset,
            horizontalInset + rightWall, verticalInset + bottomWall)
    }
}

private data class FairySprite(val bitmap: Bitmap, val contentBounds: Rect, val background: Int)

/** Fit the subject's measured frame into the tile. Original image pixels stay unchanged. */
private class FairyFaceDrawable(private val sprite: FairySprite) : Drawable() {
    private val bitmap get() = sprite.bitmap
    private val paint = Paint().apply { isAntiAlias = false; isFilterBitmap = false; isDither = false }
    private val backdrop = Paint().apply { color = sprite.background }
    private val destination = RectF()
    private val clip = Path()
    private var pressed = false
    private var externalFilter: ColorFilter? = null

    override fun onBoundsChange(bounds: Rect) {
        // Give the actual subject a slight safety margin, instead of scaling the source's empty canvas.
        val scale = min(bounds.width() * 0.94f / sprite.contentBounds.width(),
            bounds.height() * 0.94f / sprite.contentBounds.height())
        val width = sprite.contentBounds.width() * scale
        val height = sprite.contentBounds.height() * scale
        destination.set(bounds.exactCenterX() - width / 2, bounds.exactCenterY() - height / 2,
            bounds.exactCenterX() + width / 2, bounds.exactCenterY() + height / 2)
        val cut = min(bounds.width(), bounds.height()) * 0.035f
        val l = bounds.left.toFloat(); val t = bounds.top.toFloat()
        val r = bounds.right.toFloat(); val b = bounds.bottom.toFloat()
        clip.rewind()
        clip.moveTo(l + cut, t); clip.lineTo(r - cut, t)
        clip.lineTo(r, t + cut); clip.lineTo(r, b - cut)
        clip.lineTo(r - cut, b); clip.lineTo(l + cut, b)
        clip.lineTo(l, b - cut); clip.lineTo(l, t + cut); clip.close()
    }

    override fun draw(canvas: Canvas) {
        if (bounds.isEmpty) return
        val save = canvas.save()
        canvas.clipPath(clip)
        canvas.drawRect(bounds, backdrop)
        canvas.drawBitmap(bitmap, sprite.contentBounds, destination, paint)
        canvas.restoreToCount(save)
    }

    override fun isStateful() = true
    override fun onStateChange(state: IntArray): Boolean {
        val next = android.R.attr.state_enabled in state && android.R.attr.state_pressed in state
        if (next == pressed) return false
        pressed = next
        updateFilter()
        return true
    }
    override fun setAlpha(alpha: Int) {
        paint.alpha = alpha.coerceIn(0, 255)
        backdrop.alpha = paint.alpha
        invalidateSelf()
    }
    override fun getAlpha() = paint.alpha
    override fun setColorFilter(colorFilter: ColorFilter?) { externalFilter = colorFilter; updateFilter() }
    override fun getColorFilter() = externalFilter
    private fun updateFilter() {
        paint.colorFilter = externalFilter ?: if (pressed) PRESSED_TINT else null
        backdrop.colorFilter = paint.colorFilter
        invalidateSelf()
    }
    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun getOpacity() = PixelFormat.TRANSLUCENT
    private companion object {
        val PRESSED_TINT = LightingColorFilter(Color.rgb(235, 235, 235), Color.BLACK)
    }
}

/** Bounded cache of sampled sprites; original bundled files remain unchanged. */
private object FairyBitmapCache {
    private val cache = object : LruCache<Int, FairySprite>(8 * 1024 * 1024) {
        override fun sizeOf(key: Int, value: FairySprite) = value.bitmap.allocationByteCount
    }
    private var framing: JSONObject? = null

    @Synchronized fun get(context: Context, face: TileFace): FairySprite {
        cache.get(face.id)?.let { return it }
        val assets = context.applicationContext.assets
        val path = requireNotNull(face.assetPath)
        val dimensions = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        assets.open(path).use { BitmapFactory.decodeStream(it, null, dimensions) }
        check(dimensions.outWidth > 0 && dimensions.outHeight > 0) { "Invalid fairy artwork: $path" }
        var sample = 1
        while (maxOf(dimensions.outWidth, dimensions.outHeight) / (sample * 2) >= 256) sample *= 2
        val bitmap = checkNotNull(assets.open(path).use {
            BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply {
                inScaled = false
                inSampleSize = sample
                inPreferredConfig = Bitmap.Config.ARGB_8888
            })
        }) { "Could not decode fairy artwork: $path" }
        val metadata = framing ?: assets.open("tiles/framing.json").bufferedReader().use {
            JSONObject(it.readText())
        }.also { framing = it }
        val filename = path.substringAfterLast('/')
        val coordinates = metadata.getJSONObject("frames").optJSONArray(filename)
        val color = metadata.optJSONObject("backgrounds")?.optJSONArray(filename)
        val background = if (color == null) bitmap.getPixel(0, 0)
            else Color.rgb(color.getInt(0), color.getInt(1), color.getInt(2))
        val source = if (coordinates == null) Rect(0, 0, bitmap.width, bitmap.height) else Rect(
            floor(coordinates.getDouble(0) * bitmap.width).toInt().coerceIn(0, bitmap.width - 1),
            floor(coordinates.getDouble(1) * bitmap.height).toInt().coerceIn(0, bitmap.height - 1),
            ceil(coordinates.getDouble(2) * bitmap.width).toInt().coerceIn(1, bitmap.width),
            ceil(coordinates.getDouble(3) * bitmap.height).toInt().coerceIn(1, bitmap.height),
        )
        check(!source.isEmpty) { "Invalid fairy content frame: $path" }
        return FairySprite(bitmap, source, background).also { cache.put(face.id, it) }
    }
}
