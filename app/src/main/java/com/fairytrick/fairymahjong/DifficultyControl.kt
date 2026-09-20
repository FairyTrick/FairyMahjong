package com.fairytrick.fairymahjong

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PathMeasure
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.os.Build
import android.util.TypedValue
import android.view.View
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Button
import kotlin.math.ceil
import kotlin.math.min

/** Curved native text stays legible when system text is enlarged. */
class DifficultyControl @JvmOverloads constructor(context: Context, action: () -> Unit = {}) : View(context) {
    private val face = DifficultyFaceDrawable(context)

    init {
        id = R.id.difficulty_button
        background = face
        isClickable = true
        isFocusable = true
        setOnClickListener { action() }
        render(GameDifficulty.NORMAL)
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(resolveSize(face.preferredSize, widthMeasureSpec),
            resolveSize(face.preferredSize, heightMeasureSpec))
    }

    fun render(difficulty: GameDifficulty) {
        face.difficulty = difficulty
        val name = context.getString(difficulty.labelResource())
        contentDescription = context.getString(R.string.difficulty_action,
            name, context.getString(difficulty.next().labelResource()))
        tooltipText = contentDescription
        if (Build.VERSION.SDK_INT >= 30) stateDescription = name
    }

    override fun onInitializeAccessibilityNodeInfo(info: AccessibilityNodeInfo) {
        super.onInitializeAccessibilityNodeInfo(info)
        info.className = Button::class.java.name
    }
}

internal fun GameDifficulty.labelResource(): Int = when (this) {
    GameDifficulty.EASY -> R.string.difficulty_easy
    GameDifficulty.NORMAL -> R.string.difficulty_normal
    GameDifficulty.HARD -> R.string.difficulty_hard
}

/** Artwork leaves an upper stone crescent for the current difficulty's curved label. */
internal class DifficultyFaceDrawable(context: Context, private val interactive: Boolean = true) : Drawable() {
    private val names = GameDifficulty.entries.associateWith { context.getString(it.labelResource()) }
    private val faces = mapOf(
        GameDifficulty.EASY to R.drawable.meadow_difficulty_easy_v2,
        GameDifficulty.NORMAL to R.drawable.meadow_difficulty_normal_v2,
        GameDifficulty.HARD to R.drawable.meadow_difficulty_hard_v2,
    ).mapValues { (_, resource) -> MeadowSpriteDrawable(context, resource, interactive = interactive) }
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(20, 51, 34)
        typeface = Typeface.create("sans-serif-condensed", Typeface.BOLD)
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 11f, context.resources.displayMetrics)
    }
    private val requestedTextSize = paint.textSize
    // Grow the whole medallion with its text, retaining the clear space around the face.
    // All states have the same size, so cycling never changes the toolbar layout.
    val preferredSize = ceil(maxOf(52f * context.resources.displayMetrics.density,
        52f * requestedTextSize / 11f)).toInt()
    private val labelPath = Path()
    private val pathMeasure = PathMeasure()
    private var opacity = 255
    private var tint: ColorFilter? = null
    val labelText: String get() = names.getValue(difficulty)
    val labelTextSize: Float get() = paint.textSize
    val labelWidth: Float get() = paint.measureText(labelText)
    var labelPathLength = 0f
        private set
    var difficulty = GameDifficulty.NORMAL
        set(value) { if (field != value) { field = value; invalidateSelf() } }

    init {
        val forwardingCallback = object : Callback {
            override fun invalidateDrawable(who: Drawable) = invalidateSelf()
            override fun scheduleDrawable(who: Drawable, what: Runnable, `when`: Long) = scheduleSelf(what, `when`)
            override fun unscheduleDrawable(who: Drawable, what: Runnable) = unscheduleSelf(what)
        }
        faces.values.forEach { it.callback = forwardingCallback }
    }

    override fun onBoundsChange(bounds: android.graphics.Rect) {
        val side = min(bounds.width(), bounds.height()).toFloat()
        labelPath.reset()
        val radius = side * .31f
        val centerX = side * .5f
        val centerY = side * .64f
        labelPath.addArc(centerX - radius, centerY - radius, centerX + radius, centerY + radius, 190f, 160f)
        pathMeasure.setPath(labelPath, false)
        labelPathLength = pathMeasure.length
        // Guide illustrations and very short toolbars may need a smaller medallion.
        paint.textSize = requestedTextSize * min(1f, side / preferredSize)
        faces.values.forEach { it.bounds = bounds }
    }

    override fun draw(canvas: Canvas) {
        faces.getValue(difficulty).draw(canvas)
        val side = min(bounds.width(), bounds.height()).toFloat()
        val enabled = !interactive || android.R.attr.state_enabled in state
        val pressed = interactive && enabled && android.R.attr.state_pressed in state
        val save = canvas.save()
        canvas.translate(bounds.exactCenterX() - side / 2f,
            bounds.exactCenterY() - side / 2f + if (pressed) side * .025f else 0f)
        paint.colorFilter = tint
        paint.alpha = if (enabled) opacity else opacity * 135 / 255
        canvas.drawTextOnPath(labelText, labelPath, (labelPathLength - labelWidth) / 2f, 0f, paint)
        canvas.restoreToCount(save)
    }

    override fun isStateful() = interactive
    override fun onStateChange(state: IntArray): Boolean {
        faces.values.forEach { it.state = state }
        invalidateSelf()
        return true
    }
    override fun setAlpha(alpha: Int) {
        opacity = alpha.coerceIn(0, 255)
        faces.values.forEach { it.alpha = opacity }
        invalidateSelf()
    }
    override fun getAlpha() = opacity
    override fun setColorFilter(colorFilter: ColorFilter?) {
        tint = colorFilter
        faces.values.forEach { it.colorFilter = colorFilter }
        invalidateSelf()
    }
    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun getOpacity() = PixelFormat.TRANSLUCENT
}
