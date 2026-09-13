package com.fairytrick.fairymahjong

import android.content.Context
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.LightingColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import kotlin.math.min
import kotlin.math.roundToInt

/** Shared generated stone buttons, including the guide's close action. */
internal fun meadowControl(context: Context, label: Int, artwork: Int, action: () -> Unit) = ImageButton(context).apply {
    background = MeadowSpriteDrawable(context, artwork, interactive = true)
    setPadding(0, 0, 0, 0)
    contentDescription = context.getString(label)
    tooltipText = context.getString(label)
    isFocusable = true
    stateListAnimator = null
    setOnClickListener { action() }
}

/** Action stones and a four-place tray: above/below portrait, left/right of landscape. */
class MeadowHudView @JvmOverloads constructor(
    context: Context, onHint: () -> Unit = {}, onNewBoard: () -> Unit = {}, onRotate: () -> Unit = {},
    onInstructions: () -> Unit = {},
) : ViewGroup(context) {
    val hintButton = meadowControl(context, R.string.hint, R.drawable.meadow_hint_v1, onHint)
    val newBoardButton = meadowControl(context, R.string.new_board, R.drawable.meadow_regenerate_v2, onNewBoard)
    val rotateButton = meadowControl(context, R.string.rotate_landscape, R.drawable.meadow_rotate_v1, onRotate)
    val instructionsButton = meadowControl(context, R.string.instructions_title, R.drawable.meadow_instructions_v1, onInstructions).apply {
        id = R.id.instructions_button
    }
    val actionBar = object : FrameLayout(context) {
        override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
            val vertical = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
            for (index in 0 until childCount) {
                val child = getChildAt(index)
                val fraction = index.toFloat() / (childCount - 1)
                val x = ((width - child.measuredWidth) * if (vertical) .5f else fraction).roundToInt()
                val y = ((height - child.measuredHeight) * if (vertical) fraction else .5f).roundToInt()
                child.layout(x, y, x + child.measuredWidth, y + child.measuredHeight)
            }
        }
    }.apply {
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
        listOf(hintButton, instructionsButton, rotateButton, newBoardButton).forEach {
            addView(it, FrameLayout.LayoutParams(dp(52), dp(52)))
            it.isEnabled = false
        }
    }
    private val hand = MeadowHandView(context)

    internal fun handSlots(): List<MeadowHandSlot> = hand.tileSlots()

    init {
        clipChildren = false
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_YES
        accessibilityLiveRegion = ACCESSIBILITY_LIVE_REGION_POLITE
        addView(hand)
    }

    fun render(game: MahjongGame, hint: GameHint?, findingHint: Boolean, orientation: BoardOrientation) {
        hand.render(game, hint)
        hintButton.isEnabled = !findingHint && !game.isComplete && !game.isGameOver
        hintButton.contentDescription = context.getString(if (findingHint) R.string.hint_working else R.string.hint)
        newBoardButton.isEnabled = true
        instructionsButton.isEnabled = true
        val landscape = orientation == BoardOrientation.LANDSCAPE
        val rotationLabel = context.getString(if (landscape) R.string.rotate_portrait else R.string.rotate_landscape)
        rotateButton.contentDescription = rotationLabel
        rotateButton.tooltipText = rotationLabel
        rotateButton.isEnabled = landscape == (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE)
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        val landscape = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        var handWidth = min(width, dp(if (landscape) SIDE_HAND_WIDTH_DP else 520)).coerceAtLeast(1)
        val aspect = if (landscape) MeadowHandView.COLUMN_ASPECT else MeadowHandView.ASPECT
        var handHeight = (handWidth / aspect).roundToInt()
        val height = resolveSize(handHeight + if (landscape) 0 else dp(8), heightMeasureSpec)
        if (landscape && handHeight > height) {
            handHeight = height
            handWidth = (handHeight * aspect).roundToInt().coerceAtLeast(1)
        }
        hand.measure(exact(handWidth), exact(handHeight))
        setMeasuredDimension(width, height)
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        val handLeft = (width - hand.measuredWidth) / 2
        val bottomSpace = if (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE) 0 else dp(8)
        val handTop = (height - bottomSpace - hand.measuredHeight) / 2
        hand.layout(handLeft, handTop, handLeft + hand.measuredWidth, handTop + hand.measuredHeight)
    }

    override fun generateDefaultLayoutParams() = LayoutParams(-2, -2)
    private fun exact(size: Int) = MeasureSpec.makeMeasureSpec(size, MeasureSpec.EXACTLY)
    private fun dp(value: Int) = (value * resources.displayMetrics.density + .5f).toInt()
    companion object {
        const val ACTION_BAR_SIZE_DP = 56
        const val SIDE_HAND_WIDTH_DP = 60
    }
}

/** Hide only decoration during travel; the committed hand remains visible to accessibility. */
internal class MeadowHandSlot(context: Context) : FrameLayout(context) {
    var motionHidden = false
        set(value) {
            if (field == value) return
            field = value
            invalidate()
        }

    override fun draw(canvas: Canvas) {
        if (!motionHidden) super.draw(canvas)
    }
}

private class MeadowHandView(context: Context) : ViewGroup(context) {
    private val vertical = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
    private val slots = List(MahjongGame.HAND_CAPACITY) {
        MeadowHandSlot(context).apply { importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_YES }
    }
    private val images = slots.map { slot ->
        ImageView(context).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER
            importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
            slot.addView(this, FrameLayout.LayoutParams(-1, -1))
        }
    }

    fun tileSlots(): List<MeadowHandSlot> = slots

    init {
        background = MeadowSpriteDrawable(context, R.drawable.meadow_hand_pedestal_v1, vertical = vertical)
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
        slots.forEach { addView(it) }
    }

    fun render(game: MahjongGame, hint: GameHint?) {
        slots.forEachIndexed { index, slot ->
            val tile = game.hand.getOrNull(index)
            val picture = images[index]
            picture.visibility = if (tile == null) GONE else VISIBLE
            if (tile == null) slot.background = null
            else {
                if (slot.background == null) slot.background = TileArtwork.background(context)
                val face = game.faceAt(tile)
                if (picture.tag != face) TileArtwork.bind(picture, face)
            }
            slot.contentDescription = context.getString(R.string.hand_slot, index + 1,
                if (tile == null) context.getString(R.string.empty_slot) else TileCatalog.face(game.faceAt(tile)).name)
            val highlighted = tile != null && hint?.matchingTiles?.contains(tile) == true
            slot.foreground = if (highlighted) GradientDrawable().apply {
                setColor(Color.TRANSPARENT)
                cornerRadius = dp(4).toFloat()
                setStroke(dp(2), Color.rgb(105, 221, 244))
            } else null
            if (highlighted) slot.contentDescription = "${slot.contentDescription}, ${context.getString(R.string.hint_tile_match)}"
        }
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        val height = MeasureSpec.getSize(heightMeasureSpec)
        val tileWidth = (width * if (vertical) .8f else .15f).roundToInt().coerceAtLeast(1)
        val tileHeight = (tileWidth * 1.22f).roundToInt()
        slots.forEachIndexed { index, slot ->
            TileArtwork.applyIconPadding(images[index], tileWidth, tileHeight)
            slot.measure(MeasureSpec.makeMeasureSpec(tileWidth, MeasureSpec.EXACTLY),
                MeasureSpec.makeMeasureSpec(tileHeight, MeasureSpec.EXACTLY))
        }
        setMeasuredDimension(width, height)
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        // These centers are calibrated to the four beds in the generated pedestal.
        slots.forEachIndexed { index, slot ->
            val x = (width * (if (vertical) .461f else .198f + .203f * index) - slot.measuredWidth / 2f).roundToInt()
            val y = (height * (if (vertical) .198f + .203f * index else .461f) - slot.measuredHeight / 2f).roundToInt()
            slot.layout(x, y, x + slot.measuredWidth, y + slot.measuredHeight)
        }
    }

    override fun generateDefaultLayoutParams() = LayoutParams(-2, -2)
    private fun dp(value: Int) = (value * resources.displayMetrics.density + .5f).toInt()
    companion object {
        const val ASPECT = 1750f / 523f
        // Upright tiles stay separated while fitting the shorter landscape window.
        const val COLUMN_ASPECT = 1f / 5.2f
    }
}

/** Preserves the generated PNG and trims only its transparent framing at draw time. */
private class MeadowSpriteDrawable(
    context: Context, resource: Int, private val interactive: Boolean = false,
    private val vertical: Boolean = false,
) : Drawable() {
    private val asset = MeadowSpriteCache.get(context, resource)
    private val paint = Paint().apply { isFilterBitmap = false; isAntiAlias = false }
    private val destination = RectF()
    private var customFilter: ColorFilter? = null
    private var pressed = false
    private var enabled = true
    private var focused = false
    private var opacity = 255

    override fun draw(canvas: Canvas) {
        if (vertical) {
            // Transpose the tray so its bottom sidewall becomes the right sidewall,
            // matching the upright tiles. Its four beds still run top to bottom.
            val checkpoint = canvas.save()
            canvas.translate(bounds.left.toFloat(), bounds.top.toFloat())
            canvas.rotate(90f)
            canvas.scale(1f, -1f)
            destination.set(0f, 0f, bounds.height().toFloat(), bounds.width().toFloat())
            paint.alpha = opacity
            paint.colorFilter = customFilter
            canvas.drawBitmap(asset.bitmap, asset.bounds, destination, paint)
            canvas.restoreToCount(checkpoint)
            return
        }
        val scale = min(bounds.width().toFloat() / asset.bounds.width(), bounds.height().toFloat() / asset.bounds.height())
        val width = asset.bounds.width() * scale
        val height = asset.bounds.height() * scale
        val push = if (pressed) height * .025f else 0f
        destination.set(bounds.exactCenterX() - width / 2f, bounds.exactCenterY() - height / 2f + push,
            bounds.exactCenterX() + width / 2f, bounds.exactCenterY() + height / 2f + push)
        paint.alpha = if (enabled) opacity else opacity * 135 / 255
        paint.colorFilter = customFilter ?: if (pressed) PRESSED else null
        canvas.drawBitmap(asset.bitmap, asset.bounds, destination, paint)
        if (focused && interactive) {
            val focusPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.rgb(255, 219, 125)
                style = Paint.Style.STROKE
                strokeWidth = maxOf(2f, width * .025f)
            }
            canvas.drawOval(destination, focusPaint)
        }
    }

    override fun isStateful() = interactive
    override fun onStateChange(state: IntArray): Boolean {
        if (!interactive) return false
        val nextEnabled = android.R.attr.state_enabled in state
        val nextPressed = nextEnabled && android.R.attr.state_pressed in state
        val nextFocused = android.R.attr.state_focused in state
        if (enabled == nextEnabled && pressed == nextPressed && focused == nextFocused) return false
        enabled = nextEnabled; pressed = nextPressed; focused = nextFocused
        invalidateSelf()
        return true
    }
    override fun setAlpha(alpha: Int) { opacity = alpha.coerceIn(0, 255); invalidateSelf() }
    override fun getAlpha() = opacity
    override fun setColorFilter(colorFilter: ColorFilter?) { customFilter = colorFilter; invalidateSelf() }
    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun getOpacity() = PixelFormat.TRANSLUCENT
    companion object { private val PRESSED = LightingColorFilter(Color.rgb(218, 225, 210), Color.BLACK) }
}

private data class MeadowSprite(val bitmap: Bitmap, val bounds: Rect)
private object MeadowSpriteCache {
    private val images = mutableMapOf<Int, MeadowSprite>()
    @Synchronized fun get(context: Context, resource: Int): MeadowSprite = images.getOrPut(resource) {
        val resources = context.applicationContext.resources
        val dimensions = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeResource(resources, resource, dimensions)
        var sample = 1
        while (maxOf(dimensions.outWidth, dimensions.outHeight) / (sample * 2) >= 512) sample *= 2
        val bitmap = checkNotNull(BitmapFactory.decodeResource(resources, resource,
            BitmapFactory.Options().apply { inScaled = false; inSampleSize = sample }))
        val row = IntArray(bitmap.width)
        var left = bitmap.width; var top = bitmap.height; var right = 0; var bottom = 0
        for (y in 0 until bitmap.height) {
            bitmap.getPixels(row, 0, bitmap.width, 0, y, bitmap.width, 1)
            for (x in row.indices) if (row[x] ushr 24 >= 16) {
                left = minOf(left, x); top = minOf(top, y)
                right = maxOf(right, x + 1); bottom = y + 1
            }
        }
        check(right > left && bottom > top) { "Meadow control sprite is empty" }
        MeadowSprite(bitmap, Rect(left, top, right, bottom))
    }
}
