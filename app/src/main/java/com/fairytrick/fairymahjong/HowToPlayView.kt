package com.fairytrick.fairymahjong

import android.content.Context
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import kotlin.math.min
import kotlin.math.roundToInt

/** Rules arranged for the player's chosen orientation. */
internal class HowToPlayView(
    context: Context, onClose: () -> Unit,
) : FrameLayout(context) {
    private val ink = Color.rgb(35, 63, 52)
    private val secondaryInk = Color.rgb(67, 82, 67)
    private val landscape = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE

    init {
        id = R.id.instructions_screen
        background = MeadowBackground(context)
        setPadding(dp(12), dp(12), dp(12), dp(12))
        clipToPadding = false
        if (Build.VERSION.SDK_INT >= 28) accessibilityPaneTitle = context.getString(R.string.instructions_title)

        val panels = LinearLayout(context).apply {
            orientation = if (landscape) LinearLayout.HORIZONTAL else LinearLayout.VERTICAL
        }
        addView(panels, LayoutParams(-1, -1))
        panels.addView(panel(R.id.instructions_pick_panel, R.string.instructions_pick_heading, closeCorner = !landscape) {
            addContent(copy(R.string.instructions_pick_rule), 10)
            addContent(BoardRulesDiagram(context, compact = landscape), 8)
            addContent(controlRule(R.drawable.meadow_hint_v1, R.string.instructions_hint_rule), 12)
            addContent(controlRule(R.drawable.meadow_regenerate_v2, R.string.instructions_restart_rule), 8)
            addContent(controlRule(R.drawable.meadow_rotate_v1, R.string.instructions_rotate_rule), 8)
        }, panelParams(first = true))
        panels.addView(panel(R.id.instructions_pairs_panel, R.string.instructions_pairs_heading, closeCorner = landscape) {
            addContent(copy(R.string.instructions_hand_rule), 10)
            addContent(HandPairDiagram(context, compact = landscape), 12)
            addContent(copy(R.string.instructions_last_slot, 14f, secondaryInk), 6)
            addContent(identityRule(), 14)
            addContent(copy(R.string.instructions_end_rule).apply {
                val verticalInset = dp(if (landscape) 8 else 10)
                setPadding(dp(12), verticalInset, dp(12), verticalInset)
                background = stone(Color.rgb(220, 226, 207), Color.rgb(213, 221, 199), 10)
            }, 14)
        }, panelParams(first = false))
        // A fixed overlay shares the panel corner instead of taking a separate header row.
        addView(meadowControl(context, R.string.instructions_back_to_game, R.drawable.meadow_close_v1, onClose).apply {
            id = R.id.instructions_back_button
            elevation = dp(6).toFloat()
        }, LayoutParams(dp(52), dp(52), Gravity.TOP or Gravity.END).apply {
            topMargin = -dp(8)
            marginEnd = -dp(8)
        })
    }

    private fun panelParams(first: Boolean) = if (landscape) {
        LinearLayout.LayoutParams(0, -1, 1f).apply { if (first) marginEnd = dp(10) }
    } else {
        LinearLayout.LayoutParams(-1, 0, 1f).apply { if (first) bottomMargin = dp(10) }
    }

    private fun panel(idValue: Int, title: Int, closeCorner: Boolean, content: LinearLayout.() -> Unit) = LinearLayout(context).apply {
        id = idValue
        orientation = LinearLayout.VERTICAL
        clipToOutline = true
        background = stone(Color.rgb(241, 240, 220), Color.rgb(218, 224, 210), 18)
        elevation = dp(3).toFloat()
        setPadding(dp(16), dp(if (landscape) 10 else 15), dp(16), dp(if (landscape) 10 else 16))
        addView(copy(title, 20f).apply {
            setTypeface(typeface, Typeface.BOLD)
            heading(this)
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(36)
        }, LinearLayout.LayoutParams(-1, -2).apply {
            if (closeCorner) marginEnd = dp(36)
        })
        // Keep the heading and close corner clear even when enlarged text needs scrolling.
        addView(ScrollView(context).apply {
            isFillViewport = true
            scrollBarStyle = SCROLLBARS_INSIDE_INSET
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                content()
            }, LayoutParams(-1, -2))
        }, LinearLayout.LayoutParams(-1, 0, 1f))
    }

    private fun controlRule(art: Int, caption: Int) = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        addView(ImageView(context).apply {
            // Share the HUD's sampled pixels instead of decoding a full-size PNG for the guide.
            setImageDrawable(MeadowSpriteDrawable(context, art))
            scaleType = ImageView.ScaleType.FIT_CENTER
            importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
        }, LinearLayout.LayoutParams(dp(if (landscape) 34 else 38), dp(if (landscape) 34 else 38)).apply { marginEnd = dp(10) })
        addView(copy(caption, 14f, secondaryInk), LinearLayout.LayoutParams(0, -2, 1f))
    }

    private fun identityRule() = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        val pictures = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_YES
            contentDescription = context.getString(R.string.instructions_identity_diagram)
            addView(instructionTile(context, 6), LinearLayout.LayoutParams(dp(32), dp(39)))
            addView(TextView(context).apply {
                text = "≠"
                textSize = 20f
                gravity = Gravity.CENTER
                setTextColor(secondaryInk)
                importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
            }, LinearLayout.LayoutParams(dp(22), -2))
            addView(instructionTile(context, 7), LinearLayout.LayoutParams(dp(32), dp(39)))
        }
        addView(pictures, LinearLayout.LayoutParams(-2, -2).apply { marginEnd = dp(10) })
        addView(copy(R.string.instructions_exact_image, 14f, secondaryInk), LinearLayout.LayoutParams(0, -2, 1f))
    }

    private fun LinearLayout.addContent(view: View, topMarginDp: Int) {
        // Keep the complete guide visible in a typical landscape window without shrinking text.
        val spacing = if (landscape) (topMarginDp * .55f).roundToInt() else topMarginDp
        addView(view, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(spacing) })
    }

    private fun copy(string: Int, size: Float = 15f, color: Int = ink) = TextView(context).apply {
        setText(string)
        textSize = size
        setTextColor(color)
        includeFontPadding = false
        setLineSpacing(dp(2).toFloat(), 1f)
    }

    private fun heading(view: TextView) {
        if (Build.VERSION.SDK_INT >= 28) view.isAccessibilityHeading = true
    }

    private fun stone(top: Int, bottom: Int, radius: Int) = GradientDrawable(
        GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(top, bottom),
    ).apply {
        cornerRadius = dp(radius).toFloat()
        setStroke(dp(1), Color.rgb(156, 173, 145))
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).roundToInt()
}

/** Real game artwork; examples cannot be selected or mistaken for game controls by TalkBack. */
private fun instructionTile(context: Context, face: Int, highlight: Boolean = false) = object : ImageView(context) {
    override fun setFrame(left: Int, top: Int, right: Int, bottom: Int): Boolean {
        // ImageView sizes its drawable inside setFrame. Set the face inset first,
        // so the picture cannot cover the limestone's right and bottom walls.
        TileArtwork.applyIconPadding(this, right - left, bottom - top)
        return super.setFrame(left, top, right, bottom)
    }
}.apply {
    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
    background = TileArtwork.background(context)
    TileArtwork.bind(this, face)
    if (highlight) foreground = GradientDrawable().apply {
        setColor(Color.TRANSPARENT)
        cornerRadius = 4 * resources.displayMetrics.density
        setStroke((2 * resources.displayMetrics.density).roundToInt(), Color.rgb(58, 139, 98))
    }
}

private abstract class InstructionDiagram(context: Context, description: Int) : ViewGroup(context) {
    init {
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_YES
        contentDescription = context.getString(description)
    }

    protected fun caption(textResource: Int) = TextView(context).apply {
        setText(textResource)
        textSize = 12f
        setTextColor(Color.rgb(67, 82, 67))
        includeFontPadding = false
        gravity = Gravity.CENTER
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
    }.also { addView(it) }

    protected fun measureAt(view: View, width: Int, height: Int) = view.measure(exact(width), exact(height))
    protected fun measureCaption(view: View, width: Int) = view.measure(exact(width), MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED))
    protected fun place(view: View, x: Int, y: Int) = view.layout(x, y, x + view.measuredWidth, y + view.measuredHeight)
    protected fun exact(value: Int) = MeasureSpec.makeMeasureSpec(value.coerceAtLeast(1), MeasureSpec.EXACTLY)
    protected fun dp(value: Int) = (value * resources.displayMetrics.density).roundToInt()
    override fun generateDefaultLayoutParams() = LayoutParams(-2, -2)
}

private class BoardRulesDiagram(context: Context, private val compact: Boolean) : InstructionDiagram(context, R.string.instructions_board_diagram) {
    private val row = listOf(6, 10, 18).mapIndexed { index, face ->
        instructionTile(context, face, highlight = index != 1).also { addView(it) }
    }
    private val lower = instructionTile(context, 22).also { addView(it) }
    private val upper = instructionTile(context, 26).also { addView(it) }
    private val endsCaption = caption(R.string.instructions_open_ends)
    private val coverCaption = caption(R.string.instructions_covered)
    private var tileWidth = 0
    private var tileHeight = 0
    private var leftWidth = 0

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        leftWidth = (width * .64f).roundToInt()
        tileWidth = min(dp(if (compact) 44 else 48), (leftWidth / 3.35f).roundToInt())
        tileHeight = (tileWidth * 1.22f).roundToInt()
        (row + lower + upper).forEach { measureAt(it, tileWidth, tileHeight) }
        measureCaption(endsCaption, leftWidth)
        measureCaption(coverCaption, width - leftWidth)
        val naturalHeight = tileHeight + dp(if (compact) 17 else 22) + maxOf(endsCaption.measuredHeight, coverCaption.measuredHeight)
        setMeasuredDimension(width, resolveSize(naturalHeight, heightMeasureSpec))
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        val pitch = (tileWidth * TileArtwork.COLUMN_PITCH_FRACTION).roundToInt()
        val rowStart = (leftWidth - tileWidth - 2 * pitch) / 2
        row.forEachIndexed { index, tile -> place(tile, rowStart + index * pitch, dp(6)) }
        val stackStart = leftWidth + (width - leftWidth - tileWidth - dp(10)) / 2
        place(lower, stackStart, dp(14))
        place(upper, stackStart + dp(10), 0)
        val captionTop = tileHeight + dp(if (compact) 17 else 22)
        place(endsCaption, 0, captionTop)
        place(coverCaption, leftWidth, captionTop)
    }
}

private class HandPairDiagram(context: Context, private val compact: Boolean) : InstructionDiagram(context, R.string.instructions_hand_diagram) {
    private val before = listOf(6, 10, 18, 6).mapIndexed { index, face ->
        instructionTile(context, face, highlight = index == 0 || index == 3).also { addView(it) }
    }
    private val after = listOf(10, 18).map { face -> instructionTile(context, face).also { addView(it) } }
    private val arrow = TextView(context).apply {
        text = "→"
        textSize = 24f
        gravity = Gravity.CENTER
        setTextColor(Color.rgb(62, 114, 85))
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
    }.also { addView(it) }
    private val beforeCaption = caption(R.string.instructions_hand_before)
    private val afterCaption = caption(R.string.instructions_hand_after)
    private var tileWidth = 0
    private var tileHeight = 0
    private var beforeWidth = 0
    private var afterWidth = 0
    private var diagramWidth = 0

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        tileWidth = min(dp(if (compact) 40 else 44), (width - dp(40)) / 6).coerceAtLeast(1)
        tileHeight = (tileWidth * 1.22f).roundToInt()
        beforeWidth = tileWidth * 4 + dp(12)
        afterWidth = tileWidth * 2 + dp(4)
        diagramWidth = beforeWidth + dp(24) + afterWidth
        (before + after).forEach { measureAt(it, tileWidth, tileHeight) }
        measureAt(arrow, dp(24), tileHeight)
        measureCaption(beforeCaption, beforeWidth)
        measureCaption(afterCaption, afterWidth)
        setMeasuredDimension(width, resolveSize(tileHeight + dp(if (compact) 5 else 7) + maxOf(beforeCaption.measuredHeight, afterCaption.measuredHeight), heightMeasureSpec))
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        val start = (width - diagramWidth) / 2
        before.forEachIndexed { index, tile -> place(tile, start + index * (tileWidth + dp(4)), 0) }
        place(arrow, start + beforeWidth, 0)
        val afterStart = start + beforeWidth + dp(24)
        after.forEachIndexed { index, tile -> place(tile, afterStart + index * (tileWidth + dp(4)), 0) }
        val captionTop = tileHeight + dp(if (compact) 5 else 7)
        place(beforeCaption, start, captionTop)
        place(afterCaption, afterStart, captionTop)
    }
}
