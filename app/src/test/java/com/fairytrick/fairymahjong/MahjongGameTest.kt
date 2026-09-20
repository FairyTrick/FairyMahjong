package com.fairytrick.fairymahjong

import org.junit.Assert.*
import org.junit.Test

class MahjongGameTest {
    /** Assign a requested opening to a geometrically legal order, preserving face parity. */
    private fun opening(vararg openingFaces: Int): Pair<MahjongGame, List<Int>> {
        val order = MahjongGame.generateDeal(41, GardenLayout.shape).solution
        val facesInOrder = openingFaces.toMutableList()
        openingFaces.distinct().forEach { face ->
            if (facesInOrder.count { it == face } % 2 != 0) facesInOrder.add(face)
        }
        while (facesInOrder.size < GardenLayout.positions.size) facesInOrder.add(0)
        val faces = MutableList(GardenLayout.positions.size) { 0 }
        order.forEachIndexed { position, index -> faces[index] = facesInOrder[position] }
        return MahjongGame(GameSnapshot(faces)) to order
    }

    private fun position(x2: Int, y2: Int, layer: Int): Int =
        GardenLayout.positions.indexOf(TilePosition(x2, y2, layer)).also { assertTrue(it >= 0) }

    private fun occupied(vararg indices: Int): BooleanArray =
        BooleanArray(GardenLayout.positions.size).apply { indices.forEach { this[it] = true } }

    @Test fun layoutHasFiftyTilesInThreeCenteredLayers() {
        val positions = GardenLayout.positions
        assertEquals(50, positions.size)
        assertEquals(positions.size, positions.toSet().size)
        assertEquals(mapOf(0 to 30, 1 to 18, 2 to 2), positions.groupingBy { it.layer }.eachCount())
        assertEquals(listOf(TilePosition(5, 5, 2), TilePosition(5, 7, 2)), positions.takeLast(2))
        assertEquals(0, positions.minOf { it.x2 })
        assertEquals(10, positions.maxOf { it.x2 })
        assertEquals(0, positions.minOf { it.y2 })
        assertEquals(12, positions.maxOf { it.y2 })
    }

    @Test fun pickingABCALeavesBCAndRemovesTheMatchingPair() {
        val (game, order) = opening(0, 1, 2, 0)
        order.take(3).forEach { assertEquals(PickResult.PICKED, game.pickTile(it)) }
        assertEquals(order.take(3), game.hand)
        assertEquals(PickResult.MATCHED, game.pickTile(order[3]))
        assertEquals(listOf(order[1], order[2]), game.hand)
        assertEquals(1, game.matchedPairCount)
        assertEquals(2, game.clearedTileCount)
        assertEquals(48, game.remainingTileCount)
        assertEquals(46, game.boardTileCount)
        assertFalse(game.isHandFull)
        assertFalse(game.isGameOver)
        assertFalse(game.isOnBoard(order[0]))
        assertFalse(game.isOnBoard(order[3]))
        assertEquals(PickResult.IGNORED, game.pickTile(order[3]))
    }

    @Test fun fourthPickCanMatchAnyExistingHandTileBeforeCapacityIsChecked() {
        for (matchingFace in 0..2) {
            val (game, order) = opening(0, 1, 2, matchingFace)
            order.take(3).forEach { game.pickTile(it) }
            assertEquals(PickResult.MATCHED, game.pickTile(order[3]))
            assertEquals(2, game.hand.size)
            assertFalse(game.isHandFull)
            assertTrue(game.isPickable(order[4]))
        }
    }

    @Test fun fullHandBlocksEveryPickUntilRestart() {
        val (game, order) = opening(0, 1, 2, 3, 0)
        order.take(4).forEach { assertEquals(PickResult.PICKED, game.pickTile(it)) }
        assertTrue(game.isHandFull)
        assertTrue(game.isGameOver)
        assertEquals(order.take(4), game.hand)
        assertEquals(0, game.clearedTileCount)
        assertEquals(50, game.remainingTileCount)
        val fullSnapshot = game.snapshot()
        assertFalse(game.isPickable(order[4]))
        assertEquals(PickResult.IGNORED, game.pickTile(order[4]))
        assertEquals(fullSnapshot, game.snapshot())
        assertTrue(game.restart())
        assertTrue(game.hand.isEmpty())
        assertEquals(50, game.boardTileCount)
        assertTrue(game.isOnBoard(order[3]))
        assertFalse(game.isHandFull)
        assertFalse(game.isGameOver)
        assertFalse(game.restart())
    }

    @Test fun legalTurnsContinueEvenWhenEveryNextPickWillFillTheHand() {
        val firstPicks = MahjongGame.generateDeal(41, GardenLayout.shape).solution.take(3)
        val onBoard = BooleanArray(50) { it !in firstPicks }
        val available = (0 until 50).filter { GardenLayout.isGeometricallyFree(it, onBoard) }
        val faces = MutableList(50) { 4 }
        firstPicks.forEachIndexed { face, index -> faces[index] = face }
        available.forEach { faces[it] = 3 }
        val hidden = (0 until 50).filter { it !in firstPicks && it !in available }.iterator()
        for (face in 0..3) {
            if (faces.count { it == face } % 2 != 0) faces[hidden.next()] = face
        }
        val game = MahjongGame(GameSnapshot(faces, firstPicks))
        assertEquals(3, game.hand.size)
        assertTrue(available.isNotEmpty())
        assertTrue(available.all { game.isPickable(it) && game.faceAt(it) == 3 })
        assertFalse(game.isGameOver)
        assertEquals(PickResult.PICKED, game.pickTile(available.first()))
        assertTrue(game.isGameOver)
    }

    @Test fun tileRequiresAnOpenLeftOrRightSide() {
        val left = position(2, 6, 0)
        val center = position(4, 6, 0)
        val right = position(6, 6, 0)
        val board = occupied(left, center, right)
        assertFalse(GardenLayout.isGeometricallyFree(center, board))
        assertTrue(GardenLayout.isGeometricallyFree(left, board))
        board[left] = false
        assertTrue(GardenLayout.isGeometricallyFree(center, board))
        board[center] = false
        assertFalse(GardenLayout.isGeometricallyFree(center, board))
    }

    @Test fun halfTileOverlapBlocksButTouchingUpperTileEdgeDoesNot() {
        val lower = position(3, 0, 0)
        val upper = position(4, 1, 1)
        assertFalse(GardenLayout.isGeometricallyFree(lower, occupied(lower, upper)))
        val touchingLower = position(2, 2, 0)
        assertTrue(GardenLayout.isGeometricallyFree(touchingLower, occupied(touchingLower, upper)))
        val baseUnderTop = position(5, 4, 0)
        val top = position(5, 5, 2)
        assertFalse(GardenLayout.isGeometricallyFree(baseUnderTop, occupied(baseUnderTop, top)))
    }

    @Test fun generatedDealsAreBalancedDeterministicAndHaveACompleteLegalSolution() {
        val stylesSeen = mutableSetOf<String>()
        val facesSeen = mutableSetOf<Int>()
        var usedBuffer = false
        for (seed in 0L..199L) {
            val deal = MahjongGame.generateDeal(seed)
            assertEquals(deal, MahjongGame.generateDeal(seed))
            stylesSeen.add(deal.snapshot.shape.id)
            facesSeen.addAll(deal.snapshot.faces)
            val counts = deal.snapshot.faces.groupingBy { it }.eachCount().values.sorted()
            val expectedRange = if (deal.snapshot.faces.size < 64) 13..17 else 15..19
            assertTrue("Active faces for seed $seed", counts.size in expectedRange)
            assertTrue(counts.all { it > 0 && it % 2 == 0 })
            assertTrue(counts.last() - counts.first() <= 2)
            val game = MahjongGame(deal.snapshot)
            deal.solution.forEachIndexed { move, index ->
                assertTrue("Seed $seed, move $move must be legal", game.isPickable(index))
                assertNotEquals(PickResult.IGNORED, game.pickTile(index))
                assertFalse(game.isHandFull)
                usedBuffer = usedBuffer || game.hand.size >= 2
            }
            assertTrue(game.isComplete)
            assertFalse(game.isGameOver)
            assertEquals(0, game.boardTileCount)
            assertEquals(0, game.remainingTileCount)
            assertEquals(game.tileCount, game.clearedTileCount)
            assertEquals(game.tileCount / 2, game.matchedPairCount)
            assertTrue(game.hand.isEmpty())
        }
        assertEquals(BoardStyles.curated.map { it.id }.toSet(), stylesSeen)
        assertEquals((TileCatalog.LEGACY_FACE_COUNT until TileCatalog.FACE_COUNT).toSet(), facesSeen)
        assertTrue(usedBuffer)
    }

    @Test fun eachCuratedShapeVariesItsRosterAndCountsWhileRetainingAWinningRoute() {
        for (shape in BoardStyles.curated) {
            val expectedRange = if (shape.positions.size < 64) 13..17 else 15..19
            val faceCountsSeen = mutableSetOf<Int>()
            val rostersSeen = mutableSetOf<Set<Int>>()
            for (seed in 0L..99L) {
                val deal = MahjongGame.generateDeal(seed, shape)
                assertEquals(deal, MahjongGame.generateDeal(seed, shape))
                assertEquals(shape, deal.snapshot.shape)
                val counts = deal.snapshot.faces.groupingBy { it }.eachCount()
                assertTrue(counts.size in expectedRange)
                assertTrue(counts.keys.all { it in TileCatalog.LEGACY_FACE_COUNT until TileCatalog.FACE_COUNT })
                assertTrue(counts.values.all { it % 2 == 0 })
                assertTrue(counts.values.max() - counts.values.min() <= 2)
                faceCountsSeen.add(counts.size)
                rostersSeen.add(counts.keys)

                val familyCounts = counts.keys.groupingBy { TileCatalog.face(it).familyId }.eachCount()
                assertEquals(minOf(14, counts.size), familyCounts.size)
                assertTrue(familyCounts.values.max() - familyCounts.values.min() <= 1)

                val game = MahjongGame(deal.snapshot)
                assertEquals(game.positions.indices.toSet(), deal.solution.toSet())
                assertEquals(game.tileCount, deal.solution.size)
                deal.solution.forEach { index ->
                    assertTrue("${shape.id}, seed $seed: blocked tile $index", game.isPickable(index))
                    assertNotEquals(PickResult.IGNORED, game.pickTile(index))
                    assertFalse(game.isHandFull)
                }
                assertTrue(game.isComplete)
            }
            assertEquals(expectedRange.toSet(), faceCountsSeen)
            assertTrue("${shape.id} should vary the chosen artwork", rostersSeen.size >= 95)
        }
    }

    @Test fun restoringHistoryKeepsHandMatchesAndBoard() {
        val (game, order) = opening(0, 1, 2, 0)
        order.take(4).forEach { game.pickTile(it) }
        val restored = MahjongGame(game.snapshot())
        assertEquals(game.snapshot(), restored.snapshot())
        assertEquals(game.hand, restored.hand)
        assertEquals(game.matchedPairCount, restored.matchedPairCount)
        assertEquals(listOf(order[1], order[2]), restored.hand)
        assertEquals(46, restored.boardTileCount)
        order.take(4).forEach { assertFalse(restored.isOnBoard(it)) }
    }

    @Test fun restartKeepsDealAndClearsAllProgress() {
        val game = MahjongGame.newGame(918)
        val original = game.snapshot()
        val solution = MahjongGame.generateDeal(918).solution
        solution.take(3).forEach { game.pickTile(it) }
        assertTrue(game.restart())
        assertEquals(original, game.snapshot())
        assertEquals(game.tileCount, game.boardTileCount)
        assertEquals(0, game.matchedPairCount)
        assertEquals(0, game.clearedTileCount)
        assertEquals(game.tileCount, game.remainingTileCount)
        assertTrue(game.hand.isEmpty())
    }

    @Test fun invalidFacesAndImpossibleHistoriesAreRejected() {
        val deal = MahjongGame.generateDeal(4, GardenLayout.shape)
        val snapshot = deal.snapshot
        assertThrows(IllegalArgumentException::class.java) {
            MahjongGame(GameSnapshot(snapshot.faces.dropLast(1)))
        }
        assertThrows(IllegalArgumentException::class.java) {
            MahjongGame(snapshot.copy(faces = snapshot.faces.toMutableList().apply { this[0] = TileCatalog.FACE_COUNT }))
        }
        assertThrows(IllegalArgumentException::class.java) {
            MahjongGame(snapshot.copy(faces = snapshot.faces.toMutableList().apply { this[0] = -1 }))
        }
        assertThrows(IllegalArgumentException::class.java) {
            MahjongGame(snapshot.copy(picks = listOf(-1)))
        }
        assertThrows(IllegalArgumentException::class.java) {
            MahjongGame(snapshot.copy(picks = listOf(deal.solution[0], deal.solution[0])))
        }
        assertThrows(IllegalArgumentException::class.java) {
            MahjongGame(snapshot.copy(picks = listOf(position(5, 4, 0))))
        }
        val (fullHandGame, order) = opening(0, 1, 2, 3, 0)
        assertThrows(IllegalArgumentException::class.java) {
            MahjongGame(fullHandGame.snapshot().copy(picks = order.take(5)))
        }
    }

    @Test fun inputAndOutputSnapshotsCannotChangeAnExistingGame() {
        val deal = MahjongGame.generateDeal(83, GardenLayout.shape)
        val inputFaces = deal.snapshot.faces.toMutableList()
        val inputPicks = mutableListOf(deal.solution[0])
        val game = MahjongGame(GameSnapshot(inputFaces, inputPicks))
        val snapshot = game.snapshot()
        inputFaces[deal.solution[0]] = -1
        inputPicks.clear()
        val expected = MahjongGame(deal.snapshot.copy(picks = deal.solution.take(2)))
        game.pickTile(deal.solution[1])
        assertEquals(listOf(deal.solution[0]), snapshot.picks)
        assertEquals(deal.snapshot.faces, snapshot.faces)
        assertEquals(expected.matchedPairCount, game.matchedPairCount)
        assertEquals(PickResult.IGNORED, game.pickTile(-1))
        assertEquals(PickResult.IGNORED, game.pickTile(50))
    }

    @Test fun customShapeAndPartialHandSurviveSnapshotAndRestart() {
        for (seed in 0L..19L) {
            val shape = BoardStyles.procedural(seed)
            val deal = MahjongGame.generateDeal(seed, shape)
            val game = MahjongGame(deal.snapshot)
            deal.solution.take(7).forEach { game.pickTile(it) }
            val restored = MahjongGame(game.snapshot())
            assertEquals(shape, restored.shape)
            assertEquals(game.snapshot(), restored.snapshot())
            assertEquals(game.hand, restored.hand)
            assertEquals(game.tileCount, restored.tileCount)
            deal.solution.drop(7).forEach { assertNotEquals(PickResult.IGNORED, restored.pickTile(it)) }
            assertTrue(restored.isComplete)
            assertTrue(restored.restart())
            assertEquals(deal.snapshot, restored.snapshot())
        }
    }

    @Test fun smallCustomBoardUsesOnlyAsManyFacesAsItHasPairs() {
        val shape = BoardShape("small", listOf(TilePosition(0, 0, 0), TilePosition(2, 0, 0)))
        val game = MahjongGame.newGame(1, shape)
        assertEquals(2, game.tileCount)
        assertEquals(game.faceAt(0), game.faceAt(1))
        assertTrue(game.faceAt(0) in TileCatalog.LEGACY_FACE_COUNT until TileCatalog.FACE_COUNT)
        assertEquals(PickResult.PICKED, game.pickTile(0))
        assertEquals(PickResult.MATCHED, game.pickTile(1))
        assertTrue(game.isComplete)
    }

    @Test fun smallCustomShapesCapArtworkCountsAndStillReplayToCompletion() {
        for (size in listOf(2, 4, 6, 8)) {
            val shape = BoardShape("small-$size", List(size) { TilePosition(it * 2, 0, 0) })
            for (seed in 0L..29L) {
                val deal = MahjongGame.generateDeal(seed, shape)
                val counts = deal.snapshot.faces.groupingBy { it }.eachCount()
                assertEquals(size / 2, counts.size)
                assertTrue(counts.values.all { it == 2 })
                val game = MahjongGame(deal.snapshot)
                deal.solution.forEach { assertNotEquals(PickResult.IGNORED, game.pickTile(it)) }
                assertTrue(game.isComplete)
            }
        }
    }

    @Test fun highestCatalogIdsRestoreAndMatchOnlyTheIdenticalImage() {
        val shape = BoardShape("catalog-pairs", listOf(
            TilePosition(0, 0, 0), TilePosition(2, 0, 0),
            TilePosition(0, 2, 0), TilePosition(2, 2, 0),
        ))
        val original = GameSnapshot(listOf(60, 61, 60, 61), shape = shape)
        val game = MahjongGame(original)
        assertEquals(TileCatalog.face(60).familyId, TileCatalog.face(61).familyId)
        assertEquals(PickResult.PICKED, game.pickTile(0))
        assertEquals(PickResult.PICKED, game.pickTile(1))
        assertEquals(listOf(0, 1), game.hand)
        val restored = MahjongGame(game.snapshot())
        assertEquals(game.snapshot(), restored.snapshot())
        assertEquals(PickResult.MATCHED, restored.pickTile(2))
        assertEquals(PickResult.MATCHED, restored.pickTile(3))
        assertTrue(restored.isComplete)
        assertTrue(restored.restart())
        assertEquals(original, restored.snapshot())
    }

    @Test fun legacyFacesStillReplayWithTheirOriginalIdentities() {
        val shape = BoardShape("legacy-pair", listOf(TilePosition(0, 0, 0), TilePosition(2, 0, 0)))
        for (face in 0 until TileCatalog.LEGACY_FACE_COUNT) {
            val game = MahjongGame(GameSnapshot(listOf(face, face), listOf(0), shape))
            assertEquals(face, game.faceAt(game.hand.single()))
            assertEquals(PickResult.MATCHED, game.pickTile(1))
            assertTrue(game.isComplete)
        }
    }
}
