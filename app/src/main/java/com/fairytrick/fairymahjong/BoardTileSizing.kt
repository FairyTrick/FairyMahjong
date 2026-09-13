package com.fairytrick.fairymahjong

import kotlin.math.ceil
import kotlin.math.min

/** Shared footprint math for fitting a board and reserving readable scroll content. */
internal class BoardTileSizing(positions: List<TilePosition>, layerStepX: Int, layerStepY: Int) {
    private val columns = 1f + span(positions.map { it.x2 }) * TileArtwork.COLUMN_PITCH_FRACTION
    private val rows = 1f + span(positions.map { it.y2 }) * TileArtwork.ROW_PITCH_FRACTION
    private val layers = positions.maxOfOrNull { it.layer } ?: 0
    private val layerMarginX = layers * layerStepX
    private val layerMarginY = layers * layerStepY

    fun widthFromAvailable(availableWidth: Int): Float =
        (availableWidth - layerMarginX).coerceAtLeast(1) / columns

    fun heightForTileWidth(tileWidth: Float): Int =
        ceil(tileWidth * TILE_ASPECT * rows + layerMarginY).toInt()

    fun fittedTileWidth(availableWidth: Int, availableHeight: Int): Int =
        min(widthFromAvailable(availableWidth),
            (availableHeight - layerMarginY).coerceAtLeast(1) / (TILE_ASPECT * rows))
            .toInt().coerceAtLeast(1)

    fun readableHeight(availableWidth: Int, minimumTileWidth: Int): Int =
        heightForTileWidth(min(widthFromAvailable(availableWidth), minimumTileWidth.toFloat()))

    private fun span(values: List<Int>): Float =
        if (values.isEmpty()) 0f else (values.max().toLong() - values.min()).toFloat() / 2f

    companion object {
        const val TILE_ASPECT = 1.22f
        const val MIN_PORTRAIT_TILE_WIDTH_DP = 48
    }
}
