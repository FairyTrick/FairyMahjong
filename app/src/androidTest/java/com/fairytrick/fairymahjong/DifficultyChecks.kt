package com.fairytrick.fairymahjong

import android.content.Context
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.os.Build
import android.view.ContextThemeWrapper
import android.view.View
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Button
import java.io.File
import kotlin.math.roundToInt

/** Exercise the real control at the smallest supported toolbar size and enlarged system text. */
internal object DifficultyChecks {
    fun run(checks: NativeChecks) {
        checks.test("difficulty control exposes every state and activates its callback once") {
            checks.onMain {
                val context = themed(checks.instrumentation.targetContext, Configuration.ORIENTATION_PORTRAIT, 1f)
                var clicks = 0
                val hud = MeadowHudView(context, onDifficulty = { clicks++ })
                val game = fixtureGame()
                hud.render(game, null, false, BoardOrientation.PORTRAIT)
                drawControl(hud.difficultyButton)
                check(face(hud).labelText == context.getString(R.string.difficulty_normal)) {
                    "A new difficulty control must default to Normal"
                }
                for (difficulty in GameDifficulty.entries) {
                    hud.render(game, null, false, BoardOrientation.PORTRAIT, difficulty)
                    drawControl(hud.difficultyButton)
                    val stateName = context.getString(labelResource(difficulty))
                    check(face(hud).labelText == stateName)
                    val description = hud.difficultyButton.contentDescription?.toString().orEmpty()
                    val expected = context.getString(R.string.difficulty_action, stateName,
                        context.getString(labelResource(difficulty.next())))
                    check(description == expected) {
                        "Difficulty accessibility description lost its action or state: $description"
                    }
                    check(hud.difficultyButton.tooltipText?.toString() == expected)
                    if (Build.VERSION.SDK_INT >= 30) check(hud.difficultyButton.stateDescription?.toString() == stateName)
                    checkButtonRole(hud.difficultyButton)
                    check(hud.difficultyButton.isEnabled && hud.difficultyButton.isClickable && hud.difficultyButton.isFocusable)
                    check(hud.actionBar.findViewById<View>(R.id.difficulty_button) === hud.difficultyButton)
                }
                check(clicks == 0) { "Rendering a difficulty state must not start another game" }
                check(hud.difficultyButton.performClick())
                check(clicks == 1) { "One difficulty tap must trigger exactly one change" }
            }
        }

        val toolbarSizes = listOf(
            Configuration.ORIENTATION_PORTRAIT to 320,
            Configuration.ORIENTATION_LANDSCAPE to 320,
            // A compact landscape screen loses vertical space to window insets and padding.
            Configuration.ORIENTATION_LANDSCAPE to 278,
        )
        for ((orientation, axisDp) in toolbarSizes) {
            for (fontScale in listOf(1f, 2f)) {
                checks.test("five toolbar controls fit a ${axisDp}dp axis at orientation=$orientation fontScale=$fontScale") {
                    checks.onMain {
                        val context = themed(checks.instrumentation.targetContext, orientation, fontScale)
                        val density = context.resources.displayMetrics.density
                        fun dp(value: Int) = (value * density).roundToInt()
                        val hud = MeadowHudView(context)
                        val bar = hud.actionBar
                        val game = fixtureGame()
                        val landscape = orientation == Configuration.ORIENTATION_LANDSCAPE
                        val boardOrientation = if (landscape) BoardOrientation.LANDSCAPE else BoardOrientation.PORTRAIT
                        for (difficulty in GameDifficulty.entries) {
                            hud.render(game, null, false, boardOrientation, difficulty)
                            val longAxis = View.MeasureSpec.makeMeasureSpec(dp(axisDp), View.MeasureSpec.EXACTLY)
                            val crossAxis = View.MeasureSpec.makeMeasureSpec(dp(128), View.MeasureSpec.AT_MOST)
                            bar.measure(if (landscape) crossAxis else longAxis, if (landscape) longAxis else crossAxis)
                            bar.layout(0, 0, bar.measuredWidth, bar.measuredHeight)
                            check(bar.childCount == 5) { "All five toolbar actions must be available" }
                            val bounds = (0 until bar.childCount).map { index ->
                                val child = bar.getChildAt(index)
                                check(child.visibility == View.VISIBLE && child.isEnabled)
                                check(child.width >= dp(48) && child.height >= dp(48)) {
                                    "Toolbar touch target became too small"
                                }
                                Rect(child.left, child.top, child.right, child.bottom).also { rectangle ->
                                    check(rectangle.left >= 0 && rectangle.top >= 0 &&
                                        rectangle.right <= bar.width && rectangle.bottom <= bar.height) {
                                        "Toolbar action falls outside the bar: $rectangle in ${bar.width} x ${bar.height}"
                                    }
                                }
                            }
                            bounds.forEachIndexed { index, first ->
                                bounds.drop(index + 1).forEach { second ->
                                    check(!Rect.intersects(first, second)) { "Toolbar actions overlap: $first and $second" }
                                }
                            }
                            val target = Bitmap.createBitmap(bar.width, bar.height, Bitmap.Config.ARGB_8888)
                            try {
                                bar.draw(Canvas(target))
                                val title = face(hud)
                                check(title.labelText == context.getString(labelResource(difficulty)))
                                check(title.labelTextSize.isFinite() && title.labelTextSize > 0f &&
                                    title.labelWidth.isFinite() && title.labelWidth > 0f &&
                                    title.labelPathLength.isFinite()) { "Difficulty title has invalid text geometry" }
                                check(title.labelWidth + 2f <= title.labelPathLength) {
                                    "Difficulty title exceeds its arc: ${title.labelText} needs ${title.labelWidth} px, has ${title.labelPathLength} px"
                                }
                            } finally {
                                target.recycle()
                            }
                        }
                    }
                }
            }
        }

        checks.test("export actual 52dp difficulty faces at rest and pressed for visual review") {
            checks.onMain {
                val context = themed(checks.instrumentation.targetContext, Configuration.ORIENTATION_PORTRAIT, 1f)
                val density = context.resources.displayMetrics.density
                fun dp(value: Int) = (value * density).roundToInt()
                val preview = Bitmap.createBitmap(dp(224), dp(176), Bitmap.Config.ARGB_8888)
                try {
                    val canvas = Canvas(preview)
                    canvas.drawColor(Color.rgb(29, 47, 32))
                    val caption = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                        color = Color.rgb(242, 243, 215)
                        textSize = 12f * density
                    }
                    for ((row, pressed) in listOf(false, true).withIndex()) {
                        val top = dp(12 + row * 80)
                        canvas.drawText(if (pressed) "Pressed" else "At rest", dp(16).toFloat(), top + dp(12).toFloat(), caption)
                        for ((column, difficulty) in GameDifficulty.entries.withIndex()) {
                            val control = DifficultyControl(context).apply {
                                render(difficulty)
                                isPressed = pressed
                            }
                            val size = dp(52)
                            control.measure(View.MeasureSpec.makeMeasureSpec(size, View.MeasureSpec.EXACTLY),
                                View.MeasureSpec.makeMeasureSpec(size, View.MeasureSpec.EXACTLY))
                            control.layout(0, 0, size, size)
                            val saved = canvas.save()
                            canvas.translate(dp(16 + column * 64).toFloat(), (top + dp(20)).toFloat())
                            control.draw(canvas)
                            canvas.restoreToCount(saved)
                        }
                    }
                    val output = File(context.filesDir, "difficulty-faces-preview.png")
                    output.outputStream().use { check(preview.compress(Bitmap.CompressFormat.PNG, 100, it)) }
                    check(output.length() > 0L) { "Difficulty preview was not written" }
                } finally {
                    preview.recycle()
                }

                val largeContext = themed(checks.instrumentation.targetContext, Configuration.ORIENTATION_PORTRAIT, 2f)
                val largeControls = listOf(false, true).map { pressed ->
                    GameDifficulty.entries.map { difficulty ->
                        DifficultyControl(largeContext).apply {
                            render(difficulty)
                            isPressed = pressed
                        }
                    }
                }
                val largeSize = (largeControls.first().first().background as DifficultyFaceDrawable).preferredSize
                val rowStep = largeSize + dp(32)
                val largePreview = Bitmap.createBitmap(dp(32) + largeSize * 3 + dp(24),
                    dp(24) + rowStep * 2, Bitmap.Config.ARGB_8888)
                try {
                    val canvas = Canvas(largePreview)
                    canvas.drawColor(Color.rgb(29, 47, 32))
                    val caption = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                        color = Color.rgb(242, 243, 215)
                        textSize = 12f * density
                    }
                    for ((row, controls) in largeControls.withIndex()) {
                        val top = dp(12) + row * rowStep
                        canvas.drawText(if (row == 0) "2x text: at rest" else "2x text: pressed",
                            dp(16).toFloat(), top + dp(12).toFloat(), caption)
                        for ((column, control) in controls.withIndex()) {
                            control.measure(View.MeasureSpec.makeMeasureSpec(largeSize, View.MeasureSpec.EXACTLY),
                                View.MeasureSpec.makeMeasureSpec(largeSize, View.MeasureSpec.EXACTLY))
                            control.layout(0, 0, largeSize, largeSize)
                            val saved = canvas.save()
                            canvas.translate((dp(16) + column * (largeSize + dp(12))).toFloat(),
                                (top + dp(20)).toFloat())
                            control.draw(canvas)
                            canvas.restoreToCount(saved)
                        }
                    }
                    val output = File(largeContext.filesDir, "difficulty-faces-large-text-preview.png")
                    output.outputStream().use { check(largePreview.compress(Bitmap.CompressFormat.PNG, 100, it)) }
                    check(output.length() > 0L) { "Large-text difficulty preview was not written" }
                } finally {
                    largePreview.recycle()
                }
            }
        }
    }

    private fun face(hud: MeadowHudView) = hud.difficultyButton.background as DifficultyFaceDrawable

    private fun drawControl(control: DifficultyControl) {
        val size = (52 * control.resources.displayMetrics.density).roundToInt()
        control.measure(View.MeasureSpec.makeMeasureSpec(size, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(size, View.MeasureSpec.EXACTLY))
        control.layout(0, 0, size, size)
        val target = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        try {
            control.draw(Canvas(target))
        } finally {
            target.recycle()
        }
    }

    @Suppress("DEPRECATION")
    private fun checkButtonRole(control: DifficultyControl) {
        val info = AccessibilityNodeInfo.obtain()
        try {
            control.onInitializeAccessibilityNodeInfo(info)
            check(info.className == Button::class.java.name) { "Difficulty action lost its accessible button role" }
        } finally {
            info.recycle()
        }
    }

    private fun labelResource(difficulty: GameDifficulty) = when (difficulty) {
        GameDifficulty.EASY -> R.string.difficulty_easy
        GameDifficulty.NORMAL -> R.string.difficulty_normal
        GameDifficulty.HARD -> R.string.difficulty_hard
    }

    private fun themed(base: Context, orientation: Int, fontScale: Float): Context {
        val configuration = Configuration(base.resources.configuration).apply {
            this.orientation = orientation
            this.fontScale = fontScale
        }
        return ContextThemeWrapper(base.createConfigurationContext(configuration), R.style.AppTheme)
    }

    private fun fixtureGame(): MahjongGame {
        val shape = BoardShape("difficulty-control-test", List(4) { TilePosition(it * 2, 0, 0) })
        return MahjongGame(GameSnapshot(listOf(6, 6, 7, 7), shape = shape))
    }
}
