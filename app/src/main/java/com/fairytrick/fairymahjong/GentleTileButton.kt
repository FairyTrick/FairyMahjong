package com.fairytrick.fairymahjong

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.widget.ImageButton

/** Moves only the drawn limestone, leaving native tile geometry and hit testing fixed. */
internal class GentleTileButton(context: Context) : ImageButton(context) {
    private var pressAnimator: ValueAnimator? = null
    private var pressOffset = 0f
    private var suppressPressMotion = false
    // View construction can call overridden state methods before these fields are ready.
    private var motionReady = true
    private val maximumDip = resources.displayMetrics.density * 1.25f

    override fun setPressed(pressed: Boolean) {
        val changed = pressed != isPressed
        super.setPressed(pressed)
        if (!motionReady || suppressPressMotion || !changed) return
        if (!isEnabled || !isShown || !isAttachedToWindow || !hasWindowFocus() ||
            !ValueAnimator.areAnimatorsEnabled()
        ) {
            clearOffset()
            return
        }
        val start = pressOffset
        val target = if (pressed) maximumDip else 0f
        pressAnimator?.cancel()
        if (start == target) return
        val animator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = if (pressed) 55L else 95L
            interpolator = PRESS_EASE
            addUpdateListener {
                if (!ValueAnimator.areAnimatorsEnabled()) {
                    clearOffset()
                } else {
                    pressOffset = start + (target - start) * it.animatedFraction
                    invalidate()
                }
            }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    if (pressAnimator === animation) pressAnimator = null
                }
            })
        }
        pressAnimator = animator
        animator.start()
    }

    override fun draw(canvas: Canvas) {
        val checkpoint = canvas.save()
        canvas.translate(0f, pressOffset)
        super.draw(canvas)
        canvas.restoreToCount(checkpoint)
    }

    internal fun cancelPressMotion() {
        suppressPressMotion = true
        isPressed = false
        suppressPressMotion = false
        clearOffset()
    }

    private fun clearOffset() {
        if (pressAnimator == null && pressOffset == 0f) return
        val animator = pressAnimator
        pressAnimator = null
        animator?.cancel()
        pressOffset = 0f
        invalidate()
    }

    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView, visibility)
        if (motionReady && visibility != VISIBLE) cancelPressMotion()
    }

    override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
        super.onWindowFocusChanged(hasWindowFocus)
        if (motionReady && !hasWindowFocus) cancelPressMotion()
    }

    override fun onDetachedFromWindow() {
        cancelPressMotion()
        super.onDetachedFromWindow()
    }

    private companion object {
        val PRESS_EASE = DecelerateInterpolator()
    }
}
