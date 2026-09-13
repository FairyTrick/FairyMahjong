package com.fairytrick.fairymahjong

import android.os.Build
import android.view.HapticFeedbackConstants
import android.view.View

internal enum class GameHapticEvent {
    PICK, MATCH, COMPLETE, FULL_HAND, HINT, DEAL;

    companion object {
        /** A finishing pick has one outcome, so it cannot also buzz for a normal match. */
        fun forPick(result: PickResult, complete: Boolean, gameOver: Boolean): GameHapticEvent? = when {
            result == PickResult.IGNORED -> null
            complete -> COMPLETE
            gameOver -> FULL_HAND
            result == PickResult.MATCHED -> MATCH
            else -> PICK
        }
    }
}

/** Native touch feedback keeps the device's intensity, accessibility and system settings. */
internal class GameHaptics(private val isGameActive: () -> Boolean) {
    private var pendingView: View? = null
    private var pendingFinish: Runnable? = null

    fun play(view: View, event: GameHapticEvent) {
        cancel()
        if (!canPlay(view)) return
        val effect = when (event) {
            GameHapticEvent.PICK -> if (Build.VERSION.SDK_INT >= 34)
                HapticFeedbackConstants.SEGMENT_FREQUENT_TICK else HapticFeedbackConstants.CLOCK_TICK
            GameHapticEvent.MATCH -> confirmation()
            GameHapticEvent.COMPLETE -> HapticFeedbackConstants.CONTEXT_CLICK
            GameHapticEvent.FULL_HAND -> if (Build.VERSION.SDK_INT >= 30)
                HapticFeedbackConstants.REJECT else HapticFeedbackConstants.LONG_PRESS
            GameHapticEvent.HINT, GameHapticEvent.DEAL -> HapticFeedbackConstants.CLOCK_TICK
        }
        // No IGNORE_* flags: both the view and the user's touch-feedback setting are honored.
        if (!view.performHapticFeedback(effect) || event != GameHapticEvent.COMPLETE) return

        // A small two-part finish is reserved for clearing the board. No long vibration loop.
        val finish = Runnable {
            pendingView = null
            pendingFinish = null
            if (canPlay(view)) view.performHapticFeedback(confirmation())
        }
        pendingView = view
        pendingFinish = finish
        view.postDelayed(finish, 120L)
    }

    /** Called on pause, focus loss and replacement interactions. */
    fun cancel() {
        pendingFinish?.let { pendingView?.removeCallbacks(it) }
        pendingView = null
        pendingFinish = null
    }

    private fun canPlay(view: View): Boolean = isGameActive() && view.isAttachedToWindow &&
        view.isShown && view.hasWindowFocus() && view.isHapticFeedbackEnabled

    private fun confirmation(): Int = if (Build.VERSION.SDK_INT >= 30)
        HapticFeedbackConstants.CONFIRM else HapticFeedbackConstants.VIRTUAL_KEY
}
