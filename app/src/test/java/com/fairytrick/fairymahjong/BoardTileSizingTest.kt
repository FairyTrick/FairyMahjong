package com.fairytrick.fairymahjong

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.roundToInt

class BoardTileSizingTest {
    @Test fun shortPortraitWindowsReserveEnoughHeightForReadableTilesAtDifferentDensities() {
        for (density in listOf(1f, 1.5f, 2.625f, 3f)) {
            val minimum = (48 * density).roundToInt()
            for (shape in BoardStyles.curated) {
                val sizing = BoardTileSizing(shape.positions, density.roundToInt(), (2 * density).roundToInt())
                val width = (360 * density).roundToInt()
                val height = sizing.readableHeight(width, minimum)
                assertTrue("${shape.id} at $density density", sizing.fittedTileWidth(width, height) >= minimum)
                assertTrue("A short window must scroll instead of using its smaller fitted width",
                    sizing.fittedTileWidth(width, (240 * density).roundToInt()) < minimum)
            }
        }
    }

    @Test fun narrowWindowsStayWithinHorizontalSpaceInsteadOfForcingAnImpossibleMinimum() {
        val shape = BoardStyles.curated.first()
        val sizing = BoardTileSizing(shape.positions, 1, 2)
        val minimum = 48
        val width = 270
        val height = sizing.readableHeight(width, minimum)
        val fitted = sizing.fittedTileWidth(width, height)
        assertTrue(fitted < minimum)
        assertEquals(sizing.widthFromAvailable(width).toInt(), fitted)
        assertTrue(fitted >= 40)
    }

    @Test fun roomyWindowsCanUseTilesLargerThanTheMinimum() {
        val sizing = BoardTileSizing(BoardStyles.curated.first().positions, 1, 2)
        assertTrue(sizing.fittedTileWidth(380, 720) > 48)
        assertEquals(sizing.widthFromAvailable(380).toInt(), sizing.fittedTileWidth(380, 720))
    }

    @Test fun oldTallSavedGeometryCanScrollWithoutChangingItsLayoutOrTileProportions() {
        val positions = List(12) { row -> List(7) { column -> TilePosition(column * 2, row * 2, 0) } }.flatten()
        val sizing = BoardTileSizing(positions, 1, 2)
        val height = sizing.readableHeight(370, 48)
        assertTrue(height > 630)
        assertTrue(sizing.fittedTileWidth(370, height) >= 48)
        assertEquals(1.22f, BoardTileSizing.TILE_ASPECT, 0f)
        assertEquals(84, positions.size)
    }

    @Test fun translatedGeometryHasTheSameReadabilityAndFitBounds() {
        val positions = BoardStyles.curated.first().positions
        val moved = positions.map { it.copy(x2 = it.x2 - 2_000_000_000, y2 = it.y2 + 2_000_000_000) }
        val original = BoardTileSizing(positions, 3, 5)
        val translated = BoardTileSizing(moved, 3, 5)
        assertEquals(original.readableHeight(980, 126), translated.readableHeight(980, 126))
        assertEquals(original.fittedTileWidth(980, 1550), translated.fittedTileWidth(980, 1550))
    }
}
