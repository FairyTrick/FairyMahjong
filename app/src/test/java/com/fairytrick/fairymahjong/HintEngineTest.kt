package com.fairytrick.fairymahjong

import kotlin.math.abs
import kotlin.random.Random
import kotlin.system.measureNanoTime
import org.junit.Assert.*
import org.junit.Test

class HintEngineTest {
    private fun row(size: Int = 8, id: String = "hint-row") =
        BoardShape(id, List(size) { TilePosition(it * 2, 0, 0) })

    /** These rules deliberately do not call BoardGeometry or MahjongGame. */
    private fun free(index: Int, positions: List<TilePosition>, remaining: Set<Int>): Boolean {
        if (index !in remaining) return false
        val tile = positions[index]
        val others = remaining.filter { it != index }.map { positions[it] }
        val covered = others.any {
            it.layer > tile.layer && abs(it.x2 - tile.x2) < 2 && abs(it.y2 - tile.y2) < 2
        }
        val left = others.any { it.layer == tile.layer && it.x2 == tile.x2 - 2 && abs(it.y2 - tile.y2) < 2 }
        val right = others.any { it.layer == tile.layer && it.x2 == tile.x2 + 2 && abs(it.y2 - tile.y2) < 2 }
        return !covered && (!left || !right)
    }

    private class State(snapshot: GameSnapshot) {
        val faces = snapshot.faces
        val positions = snapshot.shape.positions
        val remaining = faces.indices.toMutableSet()
        val hand = mutableListOf<Int>()

        init { snapshot.picks.forEach { pick(it) } }

        fun pick(index: Int): Int? {
            assertTrue("Pick $index with a full hand", hand.size < 4)
            assertTrue("Pick $index was blocked or already removed", freeDirect(index))
            remaining.remove(index)
            val partner = hand.firstOrNull { faces[it] == faces[index] }
            if (partner == null) hand.add(index) else hand.remove(partner)
            return partner
        }

        private fun freeDirect(index: Int): Boolean {
            if (index !in remaining) return false
            val tile = positions[index]
            var covered = false
            var left = false
            var right = false
            for (otherIndex in remaining) {
                if (otherIndex == index) continue
                val other = positions[otherIndex]
                covered = covered || (other.layer > tile.layer && abs(other.x2 - tile.x2) < 2 && abs(other.y2 - tile.y2) < 2)
                if (other.layer == tile.layer && abs(other.y2 - tile.y2) < 2) {
                    left = left || other.x2 == tile.x2 - 2
                    right = right || other.x2 == tile.x2 + 2
                }
            }
            return !covered && (!left || !right)
        }
    }

    /** Exhaustive small-board oracle keyed by picked positions; hand parity follows from them. */
    private inner class Oracle(val snapshot: GameSnapshot) {
        private val all = (1 shl snapshot.faces.size) - 1
        private val known = HashMap<Int, Boolean>()

        fun mask(picks: List<Int>) = picks.fold(0) { mask, index -> mask or (1 shl index) }

        fun handSize(mask: Int): Int {
            val odd = mutableSetOf<Int>()
            snapshot.faces.indices.filter { mask and (1 shl it) != 0 }.forEach {
                if (!odd.add(snapshot.faces[it])) odd.remove(snapshot.faces[it])
            }
            return odd.size
        }

        fun legal(mask: Int): List<Int> {
            if (handSize(mask) == 4) return emptyList()
            val remaining = snapshot.faces.indices.filter { mask and (1 shl it) == 0 }.toSet()
            return remaining.filter { free(it, snapshot.shape.positions, remaining) }
        }

        fun wins(mask: Int): Boolean = known.getOrPut(mask) {
            handSize(mask) < 4 && (mask == all || legal(mask).any { wins(mask or (1 shl it)) })
        }

        fun reachableHistories(): List<List<Int>> {
            val histories = linkedMapOf(0 to emptyList<Int>())
            val queue = ArrayDeque<Int>().apply { add(0) }
            while (queue.isNotEmpty()) {
                val mask = queue.removeFirst()
                for (index in legal(mask)) {
                    val next = mask or (1 shl index)
                    if (next !in histories) {
                        histories[next] = histories.getValue(mask) + index
                        queue.add(next)
                    }
                }
            }
            return histories.values.toList()
        }
    }

    private fun verify(snapshot: GameSnapshot, hint: GameHint, oracle: Oracle? = null) {
        assertTrue("A hint needs a next pick", hint.picks.isNotEmpty())
        assertEquals(hint.picks.first(), hint.nextPick)
        assertEquals(hint.picks, hint.solution.take(hint.picks.size))
        assertEquals(2, hint.matchingTiles.size)
        assertEquals(2, hint.matchingTiles.toSet().size)
        assertEquals(snapshot.faces[hint.matchingTiles[0]], snapshot.faces[hint.matchingTiles[1]])

        val state = State(snapshot)
        val initialRemaining = state.remaining.toSet()
        var picked = oracle?.mask(snapshot.picks) ?: 0
        var firstMatchAt = -1
        hint.solution.forEachIndexed { step, index ->
            val partner = state.pick(index)
            assertTrue("Certified solution filled the hand", state.hand.size < 4)
            if (oracle != null) {
                picked = picked or (1 shl index)
                assertTrue("Hint prefix ${hint.solution.take(step + 1)} leads to loss", oracle.wins(picked))
            }
            if (partner != null && firstMatchAt == -1) {
                firstMatchAt = step
                assertEquals(setOf(partner, index), hint.matchingTiles.toSet())
            }
        }
        assertEquals("Displayed plan must end at its first match", firstMatchAt + 1, hint.picks.size)
        assertEquals(initialRemaining, hint.solution.toSet())
        assertEquals(initialRemaining.size, hint.solution.size)
        assertTrue(state.remaining.isEmpty())
        assertTrue(state.hand.isEmpty())
    }

    private fun found(snapshot: GameSnapshot, engine: HintEngine = HintEngine()): GameHint {
        val result = engine.findHint(snapshot)
        assertTrue("Expected a certified hint, received $result", result is HintResult.Found)
        return (result as HintResult.Found).hint.also {
            verify(snapshot, it, if (snapshot.faces.size <= 12) Oracle(snapshot) else null)
        }
    }

    @Test fun immediateHandMatchAndTwoBoardTilesAreIdentifiedByPhysicalIndex() {
        val shape = BoardShape("hint-square", listOf(
            TilePosition(0, 0, 0), TilePosition(2, 0, 0),
            TilePosition(0, 2, 0), TilePosition(2, 2, 0),
        ))
        val held = GameSnapshot(listOf(0, 1, 0, 1), listOf(0), shape)
        val handHint = found(held)
        assertEquals(listOf(2), handHint.picks)
        assertEquals(setOf(0, 2), handHint.matchingTiles.toSet())

        val pair = found(held.copy(picks = emptyList()))
        assertEquals(2, pair.picks.size)
        assertEquals(pair.picks.toSet(), pair.matchingTiles.toSet())
    }

    @Test fun bufferedUnblockingPlansRemainSafeThroughEveryIntermediatePick() {
        val oneBlocker = GameSnapshot(listOf(0, 1, 0, 1), listOf(0), row(4))
        assertEquals(2, found(oneBlocker).picks.size)

        // With A held, neither exposed end matches it; every winning first match needs 3 picks.
        val twoBlockers = GameSnapshot(listOf(0, 1, 2, 0, 1, 3, 2, 3), listOf(0), row())
        assertEquals(3, found(twoBlockers).picks.size)

        // Empty hand must temporarily hold 3 distinct identities before its first match.
        val threeHeld = GameSnapshot(listOf(0, 1, 2, 0, 3, 1, 2, 3), shape = row())
        assertEquals(4, found(threeHeld).picks.size)

        val towers = BoardShape("hint-towers", List(8) {
            TilePosition((it % 2) * 2, 0, it / 2)
        })
        val layered = GameSnapshot(listOf(0, 0, 1, 2, 3, 3, 2, 1), shape = towers)
        val layeredHint = found(layered)
        assertEquals(4, layeredHint.picks.size)
        assertTrue("The match should require uncovering a lower layer", layeredHint.picks.any { it < 6 })
    }

    @Test fun temptingImmediateMatchIsRejectedWhenItDestroysAllWinningContinuations() {
        val shape = BoardShape("hint-trap", List(8) { TilePosition(it * 2, 2, 0) } + listOf(
            TilePosition(7, 0, 0), TilePosition(7, 4, 0),
        ))
        val snapshot = GameSnapshot(listOf(0, 1, 2, 3, 0, 1, 2, 3, 0, 0), listOf(8), shape)
        val oracle = Oracle(snapshot)
        assertTrue(oracle.wins(1 shl 8))
        assertFalse(oracle.wins((1 shl 8) or (1 shl 9)))
        val hint = found(snapshot)
        assertNotEquals("Matching the isolated A would strand ABCDABCD", 9, hint.nextPick)
    }

    @Test fun losingBoardFullHandAndCompletedGameAreDistinguished() {
        val impossible = GameSnapshot(listOf(0, 1, 2, 3, 0, 1, 2, 3), shape = row())
        val engine = HintEngine()
        assertEquals(HintResult.Unwinnable, engine.findHint(impossible))
        assertEquals(HintResult.Unwinnable, engine.findHint(impossible.copy(picks = listOf(0, 1, 2))))
        assertEquals(HintResult.Unwinnable, engine.findHint(impossible.copy(picks = listOf(0, 1, 2, 3))))
        val complete = GameSnapshot(listOf(0, 0), listOf(0, 1), row(2))
        assertEquals(HintResult.Complete, engine.findHint(complete))
    }

    @Test fun independentOracleAgreesAcrossFourIdentityDealsAndAllTheirReachableStates() {
        val shapes = listOf(
            row(),
            BoardShape("hint-grid", List(8) { TilePosition((it % 4) * 2, (it / 4) * 2, 0) }),
            BoardShape("hint-layered", List(8) { TilePosition((it % 2) * 2, 0, it / 2) }),
        )
        val random = Random(717)
        var winning = 0
        var losing = 0
        for (shape in shapes) repeat(20) {
            val faces = listOf(0, 0, 1, 1, 2, 2, 3, 3).shuffled(random)
            val snapshot = GameSnapshot(faces, shape = shape)
            val oracle = Oracle(snapshot)
            val engine = HintEngine()
            for (history in oracle.reachableHistories()) {
                val current = snapshot.copy(picks = history)
                val expected = oracle.wins(oracle.mask(history))
                val result = engine.findHint(current, maxNodes = 200_000)
                when {
                    history.size == faces.size && expected -> assertEquals(HintResult.Complete, result)
                    expected -> {
                        winning += 1
                        assertTrue("Oracle has a win for $faces / $history; got $result", result is HintResult.Found)
                        verify(current, (result as HintResult.Found).hint, oracle)
                    }
                    else -> {
                        losing += 1
                        assertEquals("Oracle proves loss for $faces / $history", HintResult.Unwinnable, result)
                    }
                }
                assertEquals(faces, current.faces)
                assertEquals(history, current.picks)
            }
        }
        assertTrue(winning > 0)
        assertTrue(losing > 0)
        println("Hint oracle comparisons: $winning nonterminal winning states, $losing losing states")
    }

    @Test fun searchBudgetAndCancellationNeverBecomeCachedProofsOfLoss() {
        val snapshot = GameSnapshot(listOf(0, 1, 2, 0, 3, 1, 2, 3), shape = row())
        val engine = HintEngine()
        assertEquals(HintResult.SearchLimit, engine.findHint(snapshot, maxNodes = 0))
        assertEquals(HintResult.SearchLimit, engine.findHint(snapshot, maxNodes = 1))
        assertEquals(HintResult.Cancelled, engine.findHint(snapshot, isCancelled = { true }))
        var cancellationChecks = 0
        assertEquals(HintResult.Cancelled, engine.findHint(snapshot, isCancelled = {
            cancellationChecks += 1
            cancellationChecks >= 10
        }))
        assertEquals("Cancellation should interrupt search after setup", 10, cancellationChecks)
        found(snapshot, engine)
        // Changing a history must also invalidate or trim any retained solution correctly.
        found(snapshot.copy(picks = listOf(7)), engine)
        val changedFaces = snapshot.copy(faces = listOf(0, 1, 2, 3, 0, 1, 2, 3))
        assertEquals(HintResult.Unwinnable, engine.findHint(changedFaces))
        found(snapshot, engine)
        val sameIdDifferentShape = snapshot.copy(shape = BoardShape(snapshot.shape.id,
            List(8) { TilePosition((it % 4) * 2, (it / 4) * 2, 0) }))
        found(sameIdDifferentShape, engine)
        found(snapshot, engine)
    }

    @Test fun allFourBoardMaskWordsAndAll128IdentitiesAreSupported() {
        val shape = BoardShape("hint-maximum", buildList {
            for (layer in 0..3) for (y in 0..7) for (x in 0..7) add(TilePosition(x * 2, y * 2, layer))
        })
        val remaining = shape.positions.indices.toMutableSet()
        val order = buildList {
            while (remaining.isNotEmpty()) {
                val index = remaining.first { free(it, shape.positions, remaining) }
                add(index)
                remaining.remove(index)
            }
        }
        val faces = MutableList(256) { 0 }
        order.forEachIndexed { turn, index -> faces[index] = turn / 2 }
        val snapshot = GameSnapshot(faces, shape = shape)
        val hint = found(snapshot)
        assertEquals(256, hint.solution.size)
        assertEquals((0 until 128).toSet(), faces.toSet())
        found(snapshot.copy(picks = hint.solution.take(131)))
    }

    @Test fun generatedStylesAreSolvedFromCurrentProgressWithoutAGeneratorWitness() {
        var cases = 0
        val elapsed = measureNanoTime {
            for (shape in BoardStyles.curated) for (seed in 0L..2L) {
                val generated = BoardGenerator.generate(seed, 6, shape)
                val initial = GameSnapshot(generated.faces, shape = shape)
                val progress = State(initial)
                var tightPrefix = 0
                generated.solution.forEachIndexed { turn, index ->
                    progress.pick(index)
                    if (progress.hand.size == 3 && tightPrefix == 0) tightPrefix = turn + 1
                }
                for (prefix in setOf(0, tightPrefix, generated.solution.size / 2)) {
                    found(initial.copy(picks = generated.solution.take(prefix)))
                    cases += 1
                }
            }
        }
        println("Hint solver benchmark (JVM): $cases generated-board states in ${elapsed / 1_000_000.0} ms")
    }
}
