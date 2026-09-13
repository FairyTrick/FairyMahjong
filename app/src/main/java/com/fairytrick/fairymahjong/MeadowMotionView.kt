package com.fairytrick.fairymahjong

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Rect
import android.graphics.RectF
import android.view.View
import android.view.animation.LinearInterpolator
import kotlin.math.roundToInt

/** Finite decoration of already committed picks. This view never owns a game or handles input. */
internal class MeadowMotionView(context: Context) : View(context) {
    internal data class TilePose(val id: Int, val face: Int, val bounds: RectF, val pixels: Bitmap? = null)
    internal data class PickCapture(val picked: TilePose, val hand: List<TilePose>)
    private data class Glide(val picture: TilePicture, val from: RectF, val to: RectF)

    private var animator: ValueAnimator? = null
    private var incoming: TilePicture? = null
    private var partner: TilePicture? = null
    private val glides = ArrayList<Glide>(3)
    private val hiddenSlots = ArrayList<MeadowHandSlot>(4)
    private val source = RectF()
    private val destination = RectF()
    private val drawingBounds = RectF()
    private val petalPaint = Paint().apply { isAntiAlias = false }
    private val petal = Path().apply {
        moveTo(-2f, 0f); lineTo(0f, -1.5f); lineTo(2f, -1f)
        lineTo(2f, 0f); lineTo(0f, 2f); lineTo(-2f, 1f); close()
    }
    private var elapsed = 0f
    private var matching = false

    init {
        isClickable = false
        isFocusable = false
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
    }

    /** Called before mutation; a partially clipped source simply uses the immediate end state. */
    fun capturePick(tile: View?, slots: List<MeadowHandSlot>, game: MahjongGame, index: Int): PickCapture? {
        if (!canAnimate() || tile == null) return null
        val picked = capture(tile, index, game.faceAt(index), withPicture = true) ?: return null
        val matchedSlot = game.hand.indexOfFirst { game.faceAt(it) == picked.face }
        val hand = ArrayList<TilePose>(game.hand.size)
        game.hand.forEachIndexed { slot, id ->
            // Only a matching partner and the slots that will shift need a picture.
            hand.add(capture(slots[slot], id, game.faceAt(id),
                withPicture = matchedSlot >= 0 && slot >= matchedSlot) ?: return null)
        }
        return PickCapture(picked, hand)
    }

    /** Slot views already describe the final hand; only their pictures are temporarily hidden. */
    fun playPick(before: PickCapture?, slots: List<MeadowHandSlot>, game: MahjongGame) {
        cancel()
        if (before == null || !canAnimate()) return
        val after = game.hand
        val matched = before.hand.firstOrNull { it.face == before.picked.face }
        val target = if (matched != null) matched.bounds else {
            val slot = after.indexOf(before.picked.id)
            if (slot < 0) return
            capture(slots[slot], before.picked.id, before.picked.face)?.bounds ?: return
        }
        source.set(before.picked.bounds)
        destination.set(target)
        incoming = TilePicture(requireNotNull(before.picked.pixels))
        matching = matched != null
        if (matched != null) partner = TilePicture(requireNotNull(matched.pixels))

        // Slot identity changes immediately in the real UI. Copies depict only the old-to-new path.
        after.forEachIndexed { slot, id ->
            val old = before.hand.firstOrNull { it.id == id }
            val newPose = capture(slots[slot], id, game.faceAt(id))
            if (newPose == null) { cancel(); return }
            if (id == before.picked.id) hide(slots[slot])
            else if (old != null && old.bounds != newPose.bounds) {
                hide(slots[slot])
                glides.add(Glide(TilePicture(requireNotNull(old.pixels)), old.bounds, newPose.bounds))
            }
        }
        val duration = if (matching) MATCH_DURATION_MS else FLIGHT_MS
        elapsed = 0f
        animator = ValueAnimator.ofFloat(0f, duration.toFloat()).apply {
            this.duration = duration
            interpolator = LinearInterpolator()
            addUpdateListener {
                if (!canAnimate()) this@MeadowMotionView.cancel() else {
                    elapsed = it.animatedValue as Float
                    invalidate()
                }
            }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    if (animator === animation) this@MeadowMotionView.cancel()
                }
            })
        }
        animator?.start()
    }

    fun cancel() {
        if (animator == null && incoming == null && hiddenSlots.isEmpty()) return
        val previous = animator
        animator = null
        previous?.removeAllListeners()
        previous?.removeAllUpdateListeners()
        previous?.cancel()
        for (slot in hiddenSlots) slot.motionHidden = false
        hiddenSlots.clear()
        glides.clear()
        incoming = null
        partner = null
        elapsed = 0f
        invalidate()
    }

    private fun hide(view: MeadowHandSlot) {
        hiddenSlots.add(view)
        view.motionHidden = true
    }

    private fun canAnimate() = isShown && hasWindowFocus() && isAttachedToWindow &&
        ValueAnimator.areAnimatorsEnabled()

    private fun capture(view: View, id: Int, face: Int, withPicture: Boolean = false): TilePose? {
        if (view.width <= 0 || view.height <= 0 || !view.isShown) return null
        if (withPicture && view is MeadowHandSlot) {
            val artwork = view.getChildAt(0)
            // A second pick can arrive before a newly occupied slot's first layout frame.
            if (artwork.visibility != VISIBLE || artwork.width != view.width || artwork.height != view.height) return null
        }
        val visible = Rect()
        if (!view.getLocalVisibleRect(visible) || visible.left > 1 || visible.top > 1 ||
            visible.right < view.width - 1 || visible.bottom < view.height - 1) return null
        val point = IntArray(2)
        val origin = IntArray(2)
        view.getLocationInWindow(point)
        getLocationInWindow(origin)
        val left = (point[0] - origin[0]).toFloat()
        val top = (point[1] - origin[1]).toFloat()
        val pixels = if (withPicture) Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888).apply {
            val canvas = Canvas(this)
            if (view is MeadowHandSlot) {
                // Copy the limestone and artwork, leaving the live hint foreground in its slot.
                view.background?.setBounds(0, 0, view.width, view.height)
                view.background?.draw(canvas)
                view.getChildAt(0).draw(canvas)
            } else view.draw(canvas)
            prepareToDraw()
        } else null
        return TilePose(id, face, RectF(left, top, left + view.width, top + view.height), pixels)
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val moving = incoming ?: return
        val time = (elapsed / FLIGHT_MS).coerceIn(0f, 1f)
        val flight = time * time * (3f - 2f * time)
        val fade = if (matching) (1f - (elapsed - FLIGHT_MS) / FADE_MS).coerceIn(0f, 1f) else 1f
        val opacity = (255 * fade).roundToInt()
        partner?.draw(canvas, destination, opacity)

        for (index in glides.indices) {
            val glide = glides[index]
            val amount = ease(((elapsed - FLIGHT_MS) / GLIDE_MS).coerceIn(0f, 1f))
            interpolate(glide.from, glide.to, amount)
            glide.picture.draw(canvas, drawingBounds, 255)
        }

        if (opacity > 0) {
            interpolate(source, destination, flight)
            // A shallow leaf-like curve, bounded in dp even when the board is far from the hand.
            val curve = 4f * flight * (1f - flight)
            drawingBounds.offset(dp(9f) * curve, -dp(5f) * curve)
            moving.draw(canvas, drawingBounds, opacity)
        }
        if (matching && elapsed > FLIGHT_MS) drawPetals(canvas)
    }

    private fun drawPetals(canvas: Canvas) {
        val amount = ((elapsed - FLIGHT_MS) / PETAL_MS).coerceIn(0f, 1f)
        val travel = ease(amount)
        val radius = destination.width() * .31f
        for (index in 0..3) {
            petalPaint.color = if (index % 2 == 0) Color.rgb(215, 177, 191) else Color.rgb(182, 202, 150)
            petalPaint.alpha = (170 * (1f - amount)).roundToInt()
            val side = if (index < 2) -1f else 1f
            val height = if (index % 2 == 0) -.8f else .45f
            val checkpoint = canvas.save()
            canvas.translate(destination.centerX() + side * radius * travel,
                destination.centerY() + height * radius * travel + dp(4f) * amount * amount)
            canvas.rotate(side * (15f + 25f * travel))
            val size = dp(.85f) * (1f - amount * .25f)
            canvas.scale(size, size)
            canvas.drawPath(petal, petalPaint)
            canvas.restoreToCount(checkpoint)
        }
    }

    private fun interpolate(from: RectF, to: RectF, amount: Float) {
        drawingBounds.set(from.left + (to.left - from.left) * amount,
            from.top + (to.top - from.top) * amount,
            from.right + (to.right - from.right) * amount,
            from.bottom + (to.bottom - from.bottom) * amount)
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        if (changed) cancel()
    }

    override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
        super.onWindowFocusChanged(hasWindowFocus)
        if (!hasWindowFocus) cancel()
    }

    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView, visibility)
        // Android can invoke this from View's constructor before this subclass is initialized.
        if (visibility != VISIBLE && incoming != null) cancel()
    }

    override fun onDetachedFromWindow() {
        cancel()
        super.onDetachedFromWindow()
    }

    private fun dp(value: Float) = value * resources.displayMetrics.density
    private fun ease(value: Float) = 1f - (1f - value) * (1f - value) * (1f - value)

    private class TilePicture(private val pixels: Bitmap) {
        // One small, flattened tile per moving picture. Frames do not rebind artwork or redraw Views.
        // Filtering only during travel softens subpixel stepping; settled tiles retain crisp native pixels.
        private val paint = Paint(Paint.FILTER_BITMAP_FLAG)

        fun draw(canvas: Canvas, bounds: RectF, opacity: Int) {
            if (opacity <= 0) return
            paint.alpha = opacity
            canvas.drawBitmap(pixels, null, bounds, paint)
        }
    }

    private companion object {
        const val FLIGHT_MS = 220L
        const val FADE_MS = 90f
        const val GLIDE_MS = 140f
        const val PETAL_MS = 300f
        const val MATCH_DURATION_MS = 520L
    }
}
