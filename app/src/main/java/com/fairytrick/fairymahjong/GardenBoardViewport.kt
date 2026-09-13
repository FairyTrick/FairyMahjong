package com.fairytrick.fairymahjong

import android.content.Context
import android.widget.ScrollView

/** Fits ordinary portrait deals; short windows scroll before shrinking artwork too far. */
internal class GardenBoardViewport(context: Context) : ScrollView(context) {
    init {
        id = R.id.board_viewport
        overScrollMode = OVER_SCROLL_IF_CONTENT_SCROLLS
        isVerticalScrollBarEnabled = true
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        super.onMeasure(widthMeasureSpec, heightMeasureSpec)
        val board = getChildAt(0) as? GardenBoardView ?: return
        val contentWidth = (measuredWidth - paddingLeft - paddingRight).coerceAtLeast(1)
        val viewportHeight = (measuredHeight - paddingTop - paddingBottom).coerceAtLeast(1)
        // ScrollView normally gives its child unlimited height. Remeasure against the
        // actual viewport so a board fits in one screen whenever the tile floor allows it.
        val contentHeight = maxOf(viewportHeight, board.minimumReadableHeight(contentWidth))
        board.measure(MeasureSpec.makeMeasureSpec(contentWidth, MeasureSpec.EXACTLY),
            MeasureSpec.makeMeasureSpec(contentHeight, MeasureSpec.EXACTLY))
    }
}
