package com.fairytrick.fairymahjong

import java.io.File
import kotlin.math.abs
import kotlin.random.Random
import org.junit.Assert.*
import org.junit.Test

class OpeningPairBalanceTest {
    // Frozen pre-tuning generator: a paired-seed control for this deliberately small adjustment.
    private fun original(seed: Long, count: Int, shape: BoardShape, cap: Int = 3): GeneratedBoard {
        val random = Random(seed)
        val pairCount = shape.positions.size / 2
        val solution = shape.geometry.removalOrder(random)
        val extras = (0 until count).shuffled(random).take(pairCount % count).toSet()
        val remaining = IntArray(count) { 2 * (pairCount / count + if (it in extras) 1 else 0) }
        val held = BooleanArray(count)
        var heldCount = 0
        val faces = IntArray(shape.positions.size)
        val candidates = IntArray(count)
        solution.forEach { index ->
            var size = 0
            for (face in 0 until count) {
                if (remaining[face] > 0 && (held[face] || heldCount < cap)) candidates[size++] = face
            }
            val face = candidates[random.nextInt(size)]
            faces[index] = face
            remaining[face]--
            held[face] = !held[face]
            heldCount += if (held[face]) 1 else -1
        }
        return GeneratedBoard(shape, faces.toList(), solution)
    }

    private fun free(index: Int, positions: List<TilePosition>, remaining: Set<Int>): Boolean {
        val tile = positions[index]
        val others = remaining.filter { it != index }.map { positions[it] }
        if (others.any { it.layer > tile.layer && abs(it.x2 - tile.x2) < 2 && abs(it.y2 - tile.y2) < 2 }) return false
        val left = others.any { it.layer == tile.layer && it.x2 == tile.x2 - 2 && abs(it.y2 - tile.y2) < 2 }
        val right = others.any { it.layer == tile.layer && it.x2 == tile.x2 + 2 && abs(it.y2 - tile.y2) < 2 }
        return !left || !right
    }

    private fun readyPairs(board: GeneratedBoard): Int {
        val remaining = board.faces.indices.toSet()
        return remaining.filter { free(it, board.shape.positions, remaining) }
            .groupingBy { board.faces[it] }.eachCount().values.sumOf { it / 2 }
    }

    @Test fun pairedDealsKeepTheirFrequenciesAndEndingWhileOnlyCrowdedOpeningsLoseOnePair() {
        var changed = 0
        var total = 0
        val output = System.getenv("FAIRY_EXPORT_OPENING_BALANCE")?.let { directory ->
            File(directory).apply { mkdirs() }.resolve("deals.jsonl").bufferedWriter()
        }
        try {
            for (shape in BoardStyles.curated + BoardStyles.landscape) for (seed in 0L until 250L) {
                val count = 12 + (seed % 5).toInt()
                val baseline = original(seed, count, shape)
                val tuned = BoardGenerator.generate(seed, count, shape)
                val oldPairs = readyPairs(baseline)
                val newPairs = readyPairs(tuned)
                assertEquals(baseline.shape, tuned.shape)
                assertEquals(baseline.solution, tuned.solution)
                assertEquals(baseline.faces.groupingBy { it }.eachCount(), tuned.faces.groupingBy { it }.eachCount())
                if (tuned.faces != baseline.faces) {
                    changed++
                    assertTrue(oldPairs >= 3)
                    assertEquals(oldPairs - 1, newPairs)
                    assertTrue(newPairs >= 2)
                    assertEquals(4, tuned.faces.indices.count { tuned.faces[it] != baseline.faces[it] })
                    assertTrue(tuned.solution.drop(16).all { baseline.faces[it] == tuned.faces[it] })
                } else {
                    assertEquals(oldPairs, newPairs)
                }
                val before = MahjongGame(GameSnapshot(baseline.faces, shape = shape))
                val after = MahjongGame(GameSnapshot(tuned.faces, shape = shape))
                baseline.solution.forEachIndexed { turn, tile ->
                    assertNotEquals(PickResult.IGNORED, before.pickTile(tile))
                    assertNotEquals(PickResult.IGNORED, after.pickTile(tile))
                    assertTrue("Tuning increased the planted route's buffer: ${shape.id}, $seed, $turn",
                        after.hand.size <= before.hand.size)
                    if (turn >= 15) {
                        assertEquals(before.hand, after.hand)
                        assertEquals(before.matchedPairCount, after.matchedPairCount)
                        assertEquals(before.remainingTileCount, after.remainingTileCount)
                    }
                }
                assertTrue(before.isComplete && after.isComplete)
                if (output != null) {
                    val positions = shape.positions.joinToString(",") { "[${it.x2},${it.y2},${it.layer}]" }
                    output.appendLine("""{"shape":"${shape.id}","seed":$seed,"faceCount":$count,"positions":[$positions],"solution":${baseline.solution},"baseline":${baseline.faces},"tuned":${tuned.faces}}""")
                }
                total++
            }
        } finally {
            output?.close()
        }
        assertTrue("Adjustment should reach some crowded openings", changed > 0)
        assertTrue("Keep plenty of ordinary openings untouched", changed < total / 2)
        println("Opening balance: $changed/$total deals changed, at most one fewer ready pair each")
    }

    @Test fun lowerBufferCapsAndSmallCustomBoardsKeepTheirPromises() {
        for (shape in BoardStyles.curated) for (cap in 1..2) for (seed in 0L until 25L) {
            val baseline = original(seed, 12, shape, cap)
            val tuned = BoardGenerator.generate(seed, 12, shape, cap)
            val game = MahjongGame(GameSnapshot(tuned.faces, shape = shape))
            tuned.solution.forEach { tile ->
                assertNotEquals(PickResult.IGNORED, game.pickTile(tile))
                assertTrue(game.hand.size <= cap)
            }
            assertTrue(game.isComplete)
            assertEquals(baseline.faces.groupingBy { it }.eachCount(), tuned.faces.groupingBy { it }.eachCount())
        }
        val small = BoardShape("small-control", List(8) { TilePosition(it * 2, 0, 0) })
        for (seed in 0L until 25L) assertEquals(original(seed, 4, small), BoardGenerator.generate(seed, 4, small))
    }
}
