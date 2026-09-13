package com.fairytrick.fairymahjong

import org.junit.Assert.*
import org.junit.Test

class BoardOrientationTest {
    @Test fun modesToggleInBothDirectionsAndHaveStrictStableSaveValues() {
        assertEquals(BoardOrientation.LANDSCAPE, BoardOrientation.PORTRAIT.toggled())
        assertEquals(BoardOrientation.PORTRAIT, BoardOrientation.LANDSCAPE.toggled())
        assertEquals("portrait", BoardOrientation.PORTRAIT.savedValue)
        assertEquals("landscape", BoardOrientation.LANDSCAPE.savedValue)
        for (orientation in BoardOrientation.entries) {
            assertEquals(orientation, orientation.toggled().toggled())
            assertEquals(orientation, BoardOrientation.fromSavedValue(orientation.savedValue))
        }
        for (invalid in listOf("", "auto", "sensor", "PORTRAIT", "Landscape", " landscape ")) {
            assertThrows(IllegalArgumentException::class.java) {
                BoardOrientation.fromSavedValue(invalid)
            }
        }
    }

    @Test fun orientationSelectsItsOwnPoolAndEveryNewDealHasAWinningRoute() {
        val pools = mapOf(
            BoardOrientation.PORTRAIT to BoardStyles.curated,
            BoardOrientation.LANDSCAPE to BoardStyles.landscape,
        )
        assertTrue(pools.values.flatten().groupingBy { it.id }.eachCount().values.all { it == 1 })
        for ((orientation, shapes) in pools) {
            val shapeIds = shapes.map { it.id }.toSet()
            val seen = mutableSetOf<String>()
            for (seed in 0L..99L) {
                val deal = MahjongGame.generateDeal(seed, orientation = orientation)
                val game = MahjongGame.newGame(seed, orientation = orientation)
                assertEquals(deal.snapshot, game.snapshot())
                assertTrue("$orientation seed $seed", game.shape.id in shapeIds)
                assertTrue("Normal $orientation deals must never use tiny test boards: seed $seed",
                    game.tileCount >= 64)
                if (orientation == BoardOrientation.LANDSCAPE) {
                    assertTrue("New landscape deal exceeds twelve tile columns: seed $seed",
                        game.positions.maxOf { it.x2 } - game.positions.minOf { it.x2 } <= 22)
                }
                assertTrue(game.snapshot().picks.isEmpty())
                assertTrue(game.hand.isEmpty())
                seen.add(game.shape.id)
                assertEquals(game.positions.indices.toSet(), deal.solution.toSet())
                assertEquals(game.tileCount, deal.solution.size)
                deal.solution.forEach { tile ->
                    assertTrue("$orientation seed $seed: tile $tile", game.isPickable(tile))
                    assertNotEquals(PickResult.IGNORED, game.pickTile(tile))
                    assertFalse(game.isHandFull)
                }
                assertTrue("$orientation seed $seed", game.isComplete)
            }
            assertEquals(shapeIds, seen)
        }
    }

    @Test fun explicitShapeOverridesOrientationWithoutChangingTheDeterministicDeal() {
        for (shape in listOf(BoardStyles.garden, BoardStyles.landscape.first())) {
            for (seed in 0L..9L) {
                val portrait = MahjongGame.generateDeal(seed, shape, BoardOrientation.PORTRAIT)
                val landscape = MahjongGame.generateDeal(seed, shape, BoardOrientation.LANDSCAPE)
                assertEquals(portrait, landscape)
                assertEquals(shape, landscape.snapshot.shape)
                assertEquals(landscape.snapshot, MahjongGame.newGame(seed, shape, BoardOrientation.LANDSCAPE).snapshot())
            }
        }
    }

    @Test fun savedOrientationStaysWithTheBoardAndOldCallersDefaultToPortrait() {
        val board = MahjongGame.newGame(5).snapshot()
        assertEquals(BoardOrientation.PORTRAIT, SavedGame(board).orientation)
        val saved = SavedGame(board, false, BoardOrientation.LANDSCAPE)
        assertEquals(BoardOrientation.LANDSCAPE, saved.copy(board = board.copy(picks = emptyList())).orientation)
        assertFalse(saved.hapticsEnabled)
    }

    @Test fun retiredSixteenColumnLandscapeSnapshotsRemainPlayableAndRestartExactly() {
        val oldLayers = listOf(
            listOf("..############..", "####........####", "####........####", "..############.."),
            listOf("...##########...", "...#........#...", "...#........#...", "...##########..."),
        )
        val oldShape = BoardShape("meadow-halo-landscape-v1", buildList {
            oldLayers.forEachIndexed { layer, rows ->
                rows.forEachIndexed { row, cells ->
                    cells.forEachIndexed { column, mark ->
                        if (mark == '#') add(TilePosition(column * 2, row * 2, layer))
                    }
                }
            }
        })
        assertTrue(BoardStyles.landscape.none { it.id == oldShape.id })
        assertEquals(30, oldShape.positions.maxOf { it.x2 } - oldShape.positions.minOf { it.x2 })
        val deal = MahjongGame.generateDeal(67L, oldShape, BoardOrientation.LANDSCAPE)
        val progressed = MahjongGame(deal.snapshot)
        deal.solution.take(7).forEach { assertNotEquals(PickResult.IGNORED, progressed.pickTile(it)) }
        val saved = SavedGame(progressed.snapshot(), false, BoardOrientation.LANDSCAPE)
        val restored = MahjongGame(saved.board)
        assertEquals(BoardOrientation.LANDSCAPE, saved.orientation)
        assertFalse(saved.hapticsEnabled)
        assertEquals(saved.board, restored.snapshot())
        assertEquals(progressed.hand, restored.hand)
        assertEquals(oldShape, restored.shape)
        deal.solution.drop(7).forEach {
            assertTrue(restored.isPickable(it))
            assertNotEquals(PickResult.IGNORED, restored.pickTile(it))
            assertFalse(restored.isHandFull)
        }
        assertTrue(restored.isComplete)
        val restarted = MahjongGame(saved.board)
        assertTrue(restarted.restart())
        assertEquals(deal.snapshot, restarted.snapshot())
    }
}
