package com.fairytrick.fairymahjong

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import kotlin.math.min
import kotlin.math.roundToInt

/** Positions native tile buttons; Android owns hit testing, click cancellation and accessibility. */
class GardenBoardView @JvmOverloads constructor(
    context: Context,
    private val onPick: (Int) -> Unit = {},
) : ViewGroup(context) {
    private var positions: List<TilePosition> = emptyList()
    private var tiles: List<GentleTileButton> = emptyList()
    private var minX2 = 0
    private var minY2 = 0
    private var tileWidth = 0
    private var tileHeight = 0
    private var originX = 0f
    private var originY = 0f
    private var tileStepX = 0f
    private var tileStepY = 0f
    private var hintMarkers: List<HintMarker> = emptyList()
    private val hintPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
    }
    private val hintBounds = RectF()
    private val futureHintDash = DashPathEffect(floatArrayOf(dp(4).toFloat(), dp(3).toFloat()), 0f)
    private val hintBreathPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = HINT_NEXT_COLOR
        strokeWidth = dp(8).toFloat()
    }
    private var hintBreathAnimator: ValueAnimator? = null
    private var hintBreathTile = -1
    private var hintBreath = 0f
    private var motionReady = true

    init {
        clipChildren = false
        clipToPadding = false
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
        val landscape = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        val verticalPadding = if (landscape) 4 else 14
        setPadding(dp(12), dp(verticalPadding), dp(12), dp(verticalPadding))
    }

    fun render(game: MahjongGame, hint: GameHint? = null) {
        if (positions != game.positions) createTiles(game.positions)
        val gameOver = game.isGameOver
        val activeHint = hint?.takeUnless { gameOver }
        val hintSteps = activeHint?.picks?.withIndex()?.associate { it.value to it.index + 1 }
            ?: emptyMap()
        val matchingTiles = activeHint?.matchingTiles?.toSet() ?: emptySet()
        hintMarkers = (matchingTiles + hintSteps.keys).filter { it in positions.indices && game.isOnBoard(it) }
            .map { HintMarker(it, hintSteps[it]) }
            // Draw the next pick last: it remains distinct from targets beneath it.
            .sortedBy { if (it.step == 1) 1 else 0 }
        if (hintBreathAnimator != null && hintMarkers.none { it.index == hintBreathTile && it.step == 1 }) {
            cancelHintBreath()
        }
        tiles.forEachIndexed { index, tile ->
            val present = game.isOnBoard(index)
            tile.visibility = if (present) VISIBLE else GONE
            tile.isEnabled = game.isPickable(index)
            tile.importantForAccessibility = if (present) IMPORTANT_FOR_ACCESSIBILITY_YES
                else IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
            val face = game.faceAt(index)
            if (tile.tag != face) {
                TileArtwork.bind(tile, face)
            }
            tile.imageAlpha = 255
            val availability = when {
                gameOver -> R.string.tile_game_over
                tile.isEnabled -> R.string.tile_available
                else -> R.string.tile_blocked
            }
            val description = context.getString(R.string.tile_description,
                TileCatalog.face(face).name, index + 1, game.tileCount, context.getString(availability))
            val hintDescription = when (val step = hintSteps[index]) {
                1 -> context.getString(R.string.hint_tile_next)
                null -> if (index in matchingTiles) context.getString(R.string.hint_tile_match) else null
                else -> context.getString(R.string.hint_tile_step, step)
            }
            tile.contentDescription = if (hintDescription == null) description
                else "$description. $hintDescription"
        }
        // Geometry always uses the original footprint, so removing tiles never shifts the board.
        requestLayout()
        invalidate()
    }

    override fun dispatchDraw(canvas: Canvas) {
        super.dispatchDraw(canvas)
        // Overlay only markers, never tile faces. A covered target stays covered and blocked;
        // its marker remains visible without raising the tile above its blocking neighbor.
        for (index in hintMarkers.indices) {
            val marker = hintMarkers[index]
            val tile = tiles.getOrNull(marker.index) ?: continue
            if (tile.visibility != VISIBLE || tile.width == 0 || tile.height == 0) continue
            drawHintMarker(canvas, tile, marker.step, marker.label)
        }
    }

    private fun drawHintMarker(canvas: Canvas, tile: View, step: Int?, label: String) {
        val isNext = step == 1
        val color = if (isNext) HINT_NEXT_COLOR else HINT_MATCH_COLOR
        // Different insets keep both outlines legible when a future target sits directly
        // underneath the next pick. Later steps use separate badge corners for the same reason.
        val faceWidth = tile.width * TileArtwork.FACE_WIDTH_FRACTION
        val faceHeight = tile.height * TileArtwork.FACE_HEIGHT_FRACTION
        val inset = min(dp(if (isNext) 2 else 5).toFloat(), min(faceWidth, faceHeight) / 6f)
        hintBounds.set(tile.left + inset, tile.top + inset,
            tile.left + faceWidth - inset, tile.top + faceHeight - inset)
        val cornerRadius = dp(5).toFloat()
        // A single faint halo supplements the unchanged, fully readable numbered border.
        if (isNext && hintBreath > 0f && tile === tiles.getOrNull(hintBreathTile)) {
            hintBreathPaint.alpha = (hintBreath * 52f).roundToInt()
            canvas.drawRoundRect(hintBounds, cornerRadius, cornerRadius, hintBreathPaint)
        }
        hintPaint.style = Paint.Style.STROKE
        hintPaint.pathEffect = if (isNext) null else futureHintDash
        hintPaint.strokeWidth = dp(if (isNext) 5 else 4).toFloat()
        hintPaint.color = HINT_OUTLINE_COLOR
        canvas.drawRoundRect(hintBounds, cornerRadius, cornerRadius, hintPaint)
        hintPaint.strokeWidth = dp(2).toFloat()
        hintPaint.color = color
        canvas.drawRoundRect(hintBounds, cornerRadius, cornerRadius, hintPaint)
        hintPaint.pathEffect = null

        val radius = min(dp(9).toFloat(), min(hintBounds.width(), hintBounds.height()) / 3f)
        val corner = when (step) {
            1 -> 0
            null -> 3
            else -> (step - 1) % 4
        }
        val centerX = if (corner == 0 || corner == 3) hintBounds.left + radius
            else hintBounds.right - radius
        val centerY = if (corner == 0 || corner == 1) hintBounds.top + radius
            else hintBounds.bottom - radius
        hintPaint.style = Paint.Style.FILL
        hintPaint.color = color
        canvas.drawCircle(centerX, centerY, radius, hintPaint)
        hintPaint.style = Paint.Style.STROKE
        hintPaint.strokeWidth = dp(1).toFloat()
        hintPaint.color = HINT_OUTLINE_COLOR
        canvas.drawCircle(centerX, centerY, radius, hintPaint)
        hintPaint.style = Paint.Style.FILL
        hintPaint.textSize = radius * 1.35f
        val baseline = centerY - (hintPaint.ascent() + hintPaint.descent()) / 2f
        canvas.drawText(label, centerX, baseline, hintPaint)
    }

    private fun createTiles(newPositions: List<TilePosition>) {
        cancelMotion()
        removeAllViews()
        positions = newPositions.toList()
        minX2 = positions.minOfOrNull { it.x2 } ?: 0
        minY2 = positions.minOfOrNull { it.y2 } ?: 0
        tiles = positions.mapIndexed { index, position ->
            GentleTileButton(context).apply {
                background = TileArtwork.background(context)
                scaleType = ImageView.ScaleType.FIT_CENTER
                stateListAnimator = null
                elevation = dp(2 + position.layer * 3).toFloat()
                isEnabled = false
                isFocusable = true
                setOnClickListener { onPick(index) }
            }
        }
        // Right/lower neighbors cover sidewalls. Android draws equal-Z children in insertion
        // order and dispatches touches in reverse, so visible and tappable order stay aligned.
        positions.indices.sortedWith(
            compareBy<Int> { positions[it].layer }
                .thenBy { positions[it].y2 }
                .thenBy { positions[it].x2 },
        ).forEach { addView(tiles[it]) }
    }

    internal fun tileView(index: Int): View? = tiles.getOrNull(index)

    fun cancelPressedTiles() = tiles.forEach { it.cancelPressMotion() }

    /** Called only when a requested hint succeeds, never from render or continuation picks. */
    fun playHintBreath() {
        cancelHintBreath()
        if (!isAttachedToWindow || !isShown || !hasWindowFocus() || !ValueAnimator.areAnimatorsEnabled()) return
        val next = hintMarkers.firstOrNull { it.step == 1 }?.index ?: return
        hintBreathTile = next
        val animator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 500L
            addUpdateListener {
                if (!ValueAnimator.areAnimatorsEnabled()) {
                    cancelHintBreath()
                } else {
                    val fraction = it.animatedFraction
                    hintBreath = 4f * fraction * (1f - fraction)
                    invalidate()
                }
            }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    if (hintBreathAnimator === animation) {
                        hintBreathAnimator = null
                        hintBreath = 0f
                        hintBreathTile = -1
                        invalidate()
                    }
                }
            })
        }
        hintBreathAnimator = animator
        animator.start()
    }

    fun cancelMotion() {
        cancelHintBreath()
        cancelPressedTiles()
    }

    private fun cancelHintBreath() {
        if (hintBreathAnimator == null && hintBreath == 0f) return
        val animator = hintBreathAnimator
        hintBreathAnimator = null
        animator?.cancel()
        hintBreathTile = -1
        hintBreath = 0f
        invalidate()
    }

    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView, visibility)
        // View construction can dispatch this before Kotlin's field initializers run.
        if (motionReady && visibility != VISIBLE) cancelMotion()
    }

    override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
        super.onWindowFocusChanged(hasWindowFocus)
        if (!hasWindowFocus) cancelMotion()
    }

    override fun onDetachedFromWindow() {
        cancelMotion()
        super.onDetachedFromWindow()
    }

    private fun sizing() = BoardTileSizing(positions, dp(1), dp(2))

    private fun availableWidth(width: Int): Int {
        val landscape = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        return min(width - paddingLeft - paddingRight, dp(if (landscape) 1100 else 440)).coerceAtLeast(1)
    }

    internal fun minimumReadableHeight(width: Int): Int =
        sizing().readableHeight(availableWidth(width), dp(BoardTileSizing.MIN_PORTRAIT_TILE_WIDTH_DP)) +
            paddingTop + paddingBottom

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        val availableWidth = availableWidth(width)
        val sizing = sizing()
        val desiredHeight = sizing.heightForTileWidth(sizing.widthFromAvailable(availableWidth)) +
            paddingTop + paddingBottom
        val height = resolveSize(desiredHeight, heightMeasureSpec)
        val availableHeight = (height - paddingTop - paddingBottom).coerceAtLeast(1)
        tileWidth = sizing.fittedTileWidth(availableWidth, availableHeight)
        tileHeight = (tileWidth * BoardTileSizing.TILE_ASPECT).toInt().coerceAtLeast(1)
        tileStepX = tileWidth * TileArtwork.COLUMN_PITCH_FRACTION
        tileStepY = tileHeight * TileArtwork.ROW_PITCH_FRACTION
        // Center the complete deal's actual visual bounds, never just the remaining tiles.
        val minX = positions.minOfOrNull { tileX(it) } ?: 0f
        val maxX = positions.maxOfOrNull { tileX(it) } ?: 0f
        val minY = positions.minOfOrNull { tileY(it) } ?: 0f
        val maxY = positions.maxOfOrNull { tileY(it) } ?: 0f
        originX = paddingLeft + (width - paddingLeft - paddingRight - (maxX - minX + tileWidth)) / 2f - minX
        originY = paddingTop + (height - paddingTop - paddingBottom - (maxY - minY + tileHeight)) / 2f - minY
        tiles.forEach { tile ->
            TileArtwork.applyIconPadding(tile, tileWidth, tileHeight)
            tile.measure(MeasureSpec.makeMeasureSpec(tileWidth, MeasureSpec.EXACTLY),
                MeasureSpec.makeMeasureSpec(tileHeight, MeasureSpec.EXACTLY))
        }
        setMeasuredDimension(width, height)
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        tiles.forEachIndexed { index, tile ->
            val position = positions[index]
            val x = (originX + tileX(position)).roundToInt()
            val y = (originY + tileY(position)).roundToInt()
            tile.layout(x, y, x + tileWidth, y + tileHeight)
        }
    }

    // Normalize before floating-point conversion so translated custom shapes retain exact spacing.
    private fun tileX(position: TilePosition) =
        (position.x2.toLong() - minX2).toFloat() * tileStepX / 2 - position.layer * dp(1)
    private fun tileY(position: TilePosition) =
        (position.y2.toLong() - minY2).toFloat() * tileStepY / 2 - position.layer * dp(2)

    override fun generateDefaultLayoutParams() = LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT)
    private fun dp(value: Int) = (value * resources.displayMetrics.density + 0.5f).toInt()

    private companion object {
        val HINT_NEXT_COLOR = Color.rgb(255, 198, 74)
        val HINT_MATCH_COLOR = Color.rgb(116, 220, 235)
        val HINT_OUTLINE_COLOR = Color.rgb(31, 58, 61)
    }

    private data class HintMarker(val index: Int, val step: Int?) {
        val label = step?.toString() ?: "="
    }
}
