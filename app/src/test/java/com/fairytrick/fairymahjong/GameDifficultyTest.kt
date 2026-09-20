package com.fairytrick.fairymahjong

import org.junit.Assert.*
import org.junit.Test
import java.security.MessageDigest
import kotlin.math.abs

class GameDifficultyTest {
    @Test fun existingCallersUseNormalAndTheButtonCyclesThroughAllThreeModes() {
        for (orientation in BoardOrientation.entries) for (seed in 0L until 30L) {
            assertEquals(
                MahjongGame.generateDeal(seed, orientation = orientation),
                MahjongGame.generateDeal(seed, orientation = orientation, difficulty = GameDifficulty.NORMAL),
            )
            assertEquals(
                MahjongGame.newGame(seed, orientation = orientation).snapshot(),
                MahjongGame.generateDeal(seed, orientation = orientation).snapshot,
            )
        }
        assertEquals(GameDifficulty.HARD, GameDifficulty.NORMAL.next())
        assertEquals(GameDifficulty.EASY, GameDifficulty.HARD.next())
        assertEquals(GameDifficulty.NORMAL, GameDifficulty.EASY.next())
    }

    @Test fun easyKeepsTheOriginalDealsAndNormalKeepsThePreviousHardDeals() {
        // Frozen deals from before the rebalance, including the chosen shape and winning route.
        val fixtures = listOf(
            Triple(BoardOrientation.PORTRAIT, 0L, listOf(
                "94eb8f833416adaef14e43f35c9c49ca0938851b21f582d15b4823409b3d252e",
                "933e0b077acc53ac3aefc7a6c1311009cab3e96b9067ff5b8d90cc24dc0efa82")),
            Triple(BoardOrientation.PORTRAIT, 42L, listOf(
                "d440b5eed3c2f3ff4f355ca9294a111357bd2c785c9f8f28a6e7ae9071537f31",
                "e29f81cfc0d3cfaf374e1f60dc28f2ea544addea08f6935c23dc3339ed9078d5")),
            Triple(BoardOrientation.LANDSCAPE, 0L, listOf(
                "e1cea3bab4f9708f002a1717b88349de811c365334b6fc90dffaf9aa1e7a2de0",
                "9e015f07d4e0a1f4b72443eca990873f6be6cd56f76b671237903a9120208d04")),
            Triple(BoardOrientation.LANDSCAPE, 42L, listOf(
                "c733d7a6fcdfdd9ac8aeb141aebe838bf44528428fe88738084a11efd4b4593c",
                "cd24ad020e273828897297703b06473be9d7ce310fac8499e60c6a5b5990fc79")),
        )
        for ((orientation, seed, expected) in fixtures) {
            for ((index, difficulty) in listOf(GameDifficulty.EASY, GameDifficulty.NORMAL).withIndex()) {
                val deal = MahjongGame.generateDeal(seed, orientation = orientation, difficulty = difficulty)
                val input = "${deal.snapshot.shape.id}:${deal.snapshot.faces}:${deal.solution}"
                val hash = MessageDigest.getInstance("SHA-256").digest(input.toByteArray(Charsets.UTF_8))
                    .joinToString("") { "%02x".format(it) }
                assertEquals("$orientation $seed $difficulty", expected[index], hash)
            }
        }
    }

    @Test fun everyDifficultyKeepsBoardSizeBalancedPairsAndAWinningRouteWithASpareHandSlot() {
        for (shape in BoardStyles.curated + BoardStyles.landscape) for (seed in 0L until 100L) {
            val easy = MahjongGame.generateDeal(seed, shape, difficulty = GameDifficulty.EASY)
            val easyFaceCount = easy.snapshot.faces.toSet().size
            for ((difficulty, difference) in listOf(
                GameDifficulty.EASY to 0, GameDifficulty.NORMAL to 3, GameDifficulty.HARD to 6,
            )) {
                val deal = MahjongGame.generateDeal(seed, shape, difficulty = difficulty)
                assertEquals(deal, MahjongGame.generateDeal(seed, shape, difficulty = difficulty))
                assertEquals(shape, deal.snapshot.shape)
                assertEquals(easy.solution, deal.solution)
                assertEquals(shape.positions.size, deal.snapshot.faces.size)
                val counts = deal.snapshot.faces.groupingBy { it }.eachCount()
                assertEquals(easyFaceCount + difference, counts.size)
                assertTrue(counts.keys.all { it in TileCatalog.LEGACY_FACE_COUNT until TileCatalog.FACE_COUNT })
                assertTrue(counts.values.all { it % 2 == 0 })
                assertTrue(counts.values.max() - counts.values.min() <= if (difficulty == GameDifficulty.HARD) 4 else 2)
                // Hard reserves two singleton pairs, but still leaves many repeated identities.
                assertTrue(counts.values.count { it >= 4 } * 3 >= counts.size)
                val game = MahjongGame(deal.snapshot)
                assertEquals(game.positions.indices.toSet(), deal.solution.toSet())
                deal.solution.forEach { tile ->
                    assertTrue("$difficulty ${shape.id} seed $seed: tile $tile", game.isPickable(tile))
                    assertNotEquals(PickResult.IGNORED, game.pickTile(tile))
                    assertTrue(game.hand.size < MahjongGame.HAND_CAPACITY)
                }
                assertTrue(game.isComplete)
            }
        }
    }

    @Test fun everyStandardHardBoardRequiresBufferUseAndUsuallyOffersItFromTheOpening() {
        var immediatelyPlayable = 0
        var total = 0
        for (shape in BoardStyles.curated + BoardStyles.landscape) for (seed in 0L until 100L) {
            val deal = MahjongGame.generateDeal(seed, shape, difficulty = GameDifficulty.HARD)
            val gate = crossedSingletonPairs(deal.snapshot)
            assertNotNull("${shape.id} seed $seed has no mandatory buffer step", gate)
            // Whichever singleton face is picked first, its mate is blocked by the other
            // face. Neither pair can disappear before two different faces share the hand.
            val (upperA, upperB, lowerA, lowerB) = gate!!
            val game = MahjongGame(deal.snapshot)
            if (game.isPickable(upperA) && game.isPickable(upperB)) {
                game.pickTile(upperA)
                game.pickTile(upperB)
                if (game.isPickable(lowerA) && game.isPickable(lowerB)) {
                    assertEquals(2, game.hand.size)
                    game.pickTile(lowerA)
                    game.pickTile(lowerB)
                    assertTrue(game.hand.isEmpty())
                    deal.solution.filter { it !in gate }.forEach { tile ->
                        assertNotEquals(PickResult.IGNORED, game.pickTile(tile))
                        assertTrue(game.hand.size < MahjongGame.HAND_CAPACITY)
                    }
                    assertTrue(game.isComplete)
                    immediatelyPlayable++
                }
            }
            total++
        }
        assertTrue("Most gates should be a usable opening choice, not buried at the end", immediatelyPlayable >= total * 3 / 4)
    }

    @Test fun changingDifficultyDoesNotChangeTheRandomlySelectedShapeInEitherOrientation() {
        for (orientation in BoardOrientation.entries) for (seed in 0L until 100L) {
            val shapes = GameDifficulty.entries.map { difficulty ->
                MahjongGame.generateDeal(seed, orientation = orientation, difficulty = difficulty).snapshot.shape
            }
            assertEquals(1, shapes.toSet().size)
        }
    }

    @Test fun difficultyChangesMatchingChoicesGraduallyAcrossBothOrientations() {
        for (shapes in listOf(BoardStyles.curated, BoardStyles.landscape)) {
            val choices = GameDifficulty.entries.associateWith { difficulty ->
                var openings = 0L
                var allTurns = 0L
                var turns = 0
                for (shape in shapes) for (seed in 0L until 100L) {
                    val deal = MahjongGame.generateDeal(seed, shape, difficulty = difficulty)
                    val game = MahjongGame(deal.snapshot)
                    openings += matchingChoices(game)
                    deal.solution.forEach { tile ->
                        allTurns += matchingChoices(game)
                        turns++
                        game.pickTile(tile)
                    }
                }
                openings.toDouble() to allTurns.toDouble() / turns
            }
            val easy = choices.getValue(GameDifficulty.EASY)
            val normal = choices.getValue(GameDifficulty.NORMAL)
            val hard = choices.getValue(GameDifficulty.HARD)
            assertTrue(easy.first > normal.first)
            assertTrue(normal.first > hard.first)
            assertTrue("Easy should offer noticeably more matches", easy.second / normal.second in 1.05..1.6)
            assertTrue("Hard should be a modest step, not a steep reduction", hard.second / normal.second in 0.7..0.96)
        }
    }

    @Test fun hardBoardsAllowSeveralWinningFirstMovesBeyondTheGeneratedSolution() {
        for (shape in BoardStyles.curated + BoardStyles.landscape) for (seed in 0L until 10L) {
            val deal = MahjongGame.generateDeal(seed, shape, difficulty = GameDifficulty.HARD)
            val game = MahjongGame(deal.snapshot)
            val alternatives = game.positions.indices
                .filter { game.isPickable(it) && it != deal.solution.first() }.take(4)
            assertEquals(4, alternatives.size)
            for (firstPick in alternatives) {
                val candidate = MahjongGame(deal.snapshot)
                candidate.pickTile(firstPick)
                val hint = HintEngine().findHint(candidate.snapshot(), maxNodes = 20_000)
                assertTrue("${shape.id} seed $seed, alternative $firstPick: $hint", hint is HintResult.Found)
                (hint as HintResult.Found).hint.solution.forEach { tile ->
                    assertNotEquals(PickResult.IGNORED, candidate.pickTile(tile))
                }
                assertTrue(candidate.isComplete)
            }
        }
    }

    /** Inspect actual geometry and singleton identities, independently of the generator. */
    private fun crossedSingletonPairs(snapshot: GameSnapshot): List<Int>? {
        val singletonPairs = snapshot.faces.indices.groupBy { snapshot.faces[it] }.values.filter { it.size == 2 }
        fun covers(upper: Int, lower: Int): Boolean {
            val a = snapshot.shape.positions[upper]
            val b = snapshot.shape.positions[lower]
            return a.layer > b.layer && abs(a.x2.toLong() - b.x2) < 2 && abs(a.y2.toLong() - b.y2) < 2
        }
        for (a in singletonPairs) for (b in singletonPairs) {
            if (a === b) continue
            for (upperA in a) for (upperB in b) {
                val lowerA = a.first { it != upperA }
                val lowerB = b.first { it != upperB }
                if (covers(upperA, lowerB) && covers(upperB, lowerA)) {
                    return listOf(upperA, upperB, lowerA, lowerB)
                }
            }
        }
        return null
    }

    @Test fun smallCustomBoardsClampFaceCountsAndRemainWinnableInEveryDifficulty() {
        for (size in listOf(2, 4, 6, 8, 16, 32)) {
            val shape = BoardShape("small-difficulty-$size", List(size) {
                TilePosition((it % 8) * 2, (it / 8) * 2, 0)
            })
            for (difficulty in GameDifficulty.entries) for (seed in 0L until 10L) {
                val deal = MahjongGame.generateDeal(seed, shape, difficulty = difficulty)
                assertTrue(deal.snapshot.faces.toSet().size in 1..size / 2)
                val game = MahjongGame(deal.snapshot)
                deal.solution.forEach { tile ->
                    assertNotEquals(PickResult.IGNORED, game.pickTile(tile))
                }
                assertTrue(game.isComplete)
            }
        }
    }

    /** Counts matching a held tile or each distinct pair of currently exposed tiles. */
    private fun matchingChoices(game: MahjongGame): Int {
        val counts = game.positions.indices.filter(game::isPickable).groupingBy(game::faceAt).eachCount()
        val held = game.hand.map(game::faceAt).toSet()
        return counts.entries.sumOf { (face, count) ->
            if (face in held) count else count * (count - 1) / 2
        }
    }
}
