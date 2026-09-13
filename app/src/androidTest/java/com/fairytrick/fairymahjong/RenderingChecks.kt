package com.fairytrick.fairymahjong

import android.content.Context
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Outline
import android.graphics.Rect
import android.view.ContextThemeWrapper
import android.view.View
import android.widget.ImageView
import java.lang.reflect.Modifier

/** Uses the device's real decoder, Canvas, resource system and View constructors. */
internal object RenderingChecks {
    fun run(checks: NativeChecks) {
        checks.test("Every bundled tile face decodes and draws on a software Canvas") {
            checks.onMain {
                val context = themed(checks.instrumentation.targetContext)
                val target = Bitmap.createBitmap(128, 160, Bitmap.Config.ARGB_8888)
                try {
                    val canvas = Canvas(target)
                    check(!canvas.isHardwareAccelerated)
                    for (face in TileCatalog.faces) {
                        val tile = ImageView(context).apply {
                            background = TileArtwork.background(context)
                            TileArtwork.bind(this, face.id)
                        }
                        measureAndLayout(tile, target.width, target.height)
                        TileArtwork.applyIconPadding(tile, tile.width, tile.height)
                        target.eraseColor(Color.TRANSPARENT)
                        tile.draw(canvas)
                        check(Color.alpha(target.getPixel(64, 80)) != 0) {
                            "Tile ${face.id} did not draw"
                        }
                        // Exercise native drawable-state propagation as well as a normal frame.
                        tile.isPressed = true
                        tile.draw(canvas)
                        tile.isEnabled = false
                        tile.draw(canvas)
                    }
                } finally {
                    target.recycle()
                }
            }
        }

        checks.test("Limestone outlines accept small, normal and offset bounds") {
            checks.onMain {
                val drawable = HairlineTileDrawable(themed(checks.instrumentation.targetContext))
                val outline = Outline()
                for (bounds in listOf(Rect(), Rect(0, 0, 1, 1), Rect(0, 0, 32, 40),
                    Rect(15, 20, 143, 180), Rect(0, 0, 400, 550))) {
                    drawable.bounds = bounds
                    drawable.getOutline(outline)
                    check(outline.isEmpty == bounds.isEmpty) { "Unexpected outline for $bounds" }
                }
            }
        }

        checks.test("View callbacks tolerate pre-initialization state and canceled effects") {
            checks.onMain {
                val context = ContextThemeWrapper(checks.instrumentation.targetContext,
                    R.style.ReliabilityHiddenViewTheme)
                // ViewGroup/ImageButton read styled attributes; View(context) alone does not.
                check(View(context, null).visibility == View.GONE) { "Hidden-view fixture was not applied" }
                val board = GardenBoardView(context)
                val motion = MeadowMotionView(context)
                val tile = GentleTileButton(context)
                val hud = MeadowHudView(context)
                check(listOf(board, tile, hud).all { it.visibility == View.GONE }) {
                    "The actual attribute-aware game views did not receive themed visibility"
                }
                // Framework versions differ in when they dispatch virtual callbacks. Exercise
                // the actual game overrides with JVM-default subclass fields while leaving
                // the native View superclass initialized. No framework internals are accessed.
                for (view in listOf(board, motion, tile)) {
                    withDefaultSubclassFields(view) {
                        val callback = view.javaClass.getDeclaredMethod("onVisibilityChanged",
                            View::class.java, Int::class.javaPrimitiveType).apply { isAccessible = true }
                        callback.invoke(view, view, View.GONE)
                    }
                }
                board.cancelMotion()
                motion.cancel()
                tile.cancelPressMotion()
                val haptics = GameHaptics { true }
                for (event in GameHapticEvent.entries) haptics.play(tile, event)
                haptics.cancel()
                for (view in listOf(board, motion, tile, hud)) {
                    view.visibility = View.VISIBLE
                    view.visibility = View.INVISIBLE
                    view.visibility = View.GONE
                }
            }
        }

        for (orientation in listOf(Configuration.ORIENTATION_PORTRAIT, Configuration.ORIENTATION_LANDSCAPE)) {
            for (fontScale in listOf(1f, 2f)) {
                checks.test("Guide and hand render at orientation=$orientation fontScale=$fontScale") {
                    checks.onMain {
                        val context = themed(checks.instrumentation.targetContext, orientation, fontScale)
                        val density = context.resources.displayMetrics.density
                        val width = ((if (orientation == Configuration.ORIENTATION_PORTRAIT) 320 else 640) * density).toInt()
                        val height = ((if (orientation == Configuration.ORIENTATION_PORTRAIT) 640 else 320) * density).toInt()
                        val guide = HowToPlayView(context) {}
                        drawView(guide, width, height)
                        val close = guide.findViewById<View>(R.id.instructions_back_button)
                        check(close.width > 0 && close.height > 0 && close.visibility == View.VISIBLE) { "Guide cannot be closed" }
                        val closeBounds = Rect()
                        close.getDrawingRect(closeBounds)
                        guide.offsetDescendantRectToMyCoords(close, closeBounds)
                        check(closeBounds.left >= 0 && closeBounds.top >= 0 &&
                            closeBounds.right <= width && closeBounds.bottom <= height) {
                            "Guide close button falls outside the window: $closeBounds"
                        }
                        val game = fixtureGame()
                        check(game.pickTile(0) == PickResult.PICKED)
                        val hud = MeadowHudView(context)
                        for (requested in BoardOrientation.entries) {
                            hud.render(game, null, false, requested)
                            check(hud.rotateButton.isEnabled) {
                                "Ignored orientation request disables all subsequent toggles"
                            }
                        }
                        drawView(hud,
                            if (orientation == Configuration.ORIENTATION_PORTRAIT) width else (60 * density).toInt(),
                            if (orientation == Configuration.ORIENTATION_PORTRAIT) (104 * density).toInt() else height)
                        val held = hud.handSlots().first()
                        check(held.width > 0 && held.height > 0 && held.getChildAt(0).visibility == View.VISIBLE)
                    }
                }
            }
        }

        checks.test("Board and viewport measure at zero, small and large window sizes") {
            checks.onMain {
                val context = themed(checks.instrumentation.targetContext)
                val board = GardenBoardView(context)
                val viewport = GardenBoardViewport(context).apply { addView(board) }
                board.render(fixtureGame())
                for ((width, height) in listOf(0 to 0, 1 to 1, 120 to 80, 320 to 480, 1600 to 2560)) {
                    measureAndLayout(viewport, width, height)
                    check(board.measuredWidth > 0 && board.measuredHeight > 0)
                    check((0 until board.childCount).all {
                        val tile = board.getChildAt(it)
                        tile.measuredWidth > 0 && tile.measuredHeight > 0
                    }) { "Window $width x $height produced empty tile geometry" }
                }
                drawView(viewport, 320, 480)
            }
        }
    }

    private fun themed(base: Context, orientation: Int? = null, fontScale: Float = 1f): Context {
        val configuration = Configuration(base.resources.configuration).apply {
            if (orientation != null) this.orientation = orientation
            this.fontScale = fontScale
        }
        return ContextThemeWrapper(base.createConfigurationContext(configuration), R.style.AppTheme)
    }

    private fun fixtureGame(): MahjongGame {
        val shape = BoardShape("rendering-test", List(8) { TilePosition((it % 4) * 2, (it / 4) * 2, 0) })
        return MahjongGame(GameSnapshot(listOf(6, 7, 7, 6, 6, 7, 7, 6), shape = shape))
    }

    private fun withDefaultSubclassFields(view: View, body: () -> Unit) {
        val fields = view.javaClass.declaredFields.filterNot { Modifier.isStatic(it.modifiers) }
            .onEach { it.isAccessible = true }
        val originals = fields.map { it.get(view) }
        try {
            for (field in fields) {
                val default = when (field.type) {
                    Boolean::class.javaPrimitiveType -> false
                    Int::class.javaPrimitiveType -> 0
                    Long::class.javaPrimitiveType -> 0L
                    Float::class.javaPrimitiveType -> 0f
                    Double::class.javaPrimitiveType -> 0.0
                    Byte::class.javaPrimitiveType -> 0.toByte()
                    Short::class.javaPrimitiveType -> 0.toShort()
                    Char::class.javaPrimitiveType -> '\u0000'
                    else -> null
                }
                field.set(view, default)
            }
            body()
        } finally {
            fields.forEachIndexed { index, field -> field.set(view, originals[index]) }
        }
    }

    private fun measureAndLayout(view: View, width: Int, height: Int) {
        view.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(height, View.MeasureSpec.EXACTLY))
        view.layout(0, 0, width, height)
    }

    private fun drawView(view: View, width: Int, height: Int) {
        measureAndLayout(view, width, height)
        val pixels = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        try {
            view.draw(Canvas(pixels))
            check(Color.alpha(pixels.getPixel(width / 2, height / 2)) > 0) {
                "${view.javaClass.simpleName} did not paint its surface"
            }
        } finally {
            pixels.recycle()
        }
    }
}
