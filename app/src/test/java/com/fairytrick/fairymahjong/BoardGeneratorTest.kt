package com.fairytrick.fairymahjong

import java.io.File
import kotlin.math.abs
import kotlin.random.Random
import kotlin.system.measureNanoTime
import org.junit.Assert.*
import org.junit.Test

class BoardGeneratorTest {
    /** Direct rule calculation deliberately does not consult the generator's blocker arrays. */
    private fun free(index: Int, positions: List<TilePosition>, onBoard: BooleanArray): Boolean {
        if (!onBoard[index]) return false
        val tile = positions[index]
        val others = positions.indices.filter { it != index && onBoard[it] }.map { positions[it] }
        if (others.any {
            it.layer > tile.layer && abs(it.x2 - tile.x2) < 2 && abs(it.y2 - tile.y2) < 2
        }) return false
        val left = others.any { it.layer == tile.layer && it.x2 == tile.x2 - 2 && abs(it.y2 - tile.y2) < 2 }
        val right = others.any { it.layer == tile.layer && it.x2 == tile.x2 + 2 && abs(it.y2 - tile.y2) < 2 }
        return !left || !right
    }

    private fun verifyWitness(deal: GeneratedBoard, faceCount: Int, cap: Int): Int {
        val positions = deal.shape.positions
        assertEquals(positions.size, deal.faces.size)
        assertEquals(positions.indices.toSet(), deal.solution.toSet())
        assertEquals(positions.size, deal.solution.size)
        val counts = deal.faces.groupingBy { it }.eachCount()
        assertEquals((0 until faceCount).toSet(), counts.keys)
        assertTrue(counts.values.all { it > 0 && it % 2 == 0 })
        assertTrue(counts.values.max() - counts.values.min() <= 2)
        val onBoard = BooleanArray(positions.size) { true }
        val hand = mutableSetOf<Int>()
        var peak = 0
        deal.solution.forEach { index ->
            assertTrue("Witness picked blocked tile $index on ${deal.shape.id}", free(index, positions, onBoard))
            onBoard[index] = false
            if (!hand.add(deal.faces[index])) hand.remove(deal.faces[index])
            assertTrue("Witness exceeded buffer cap $cap", hand.size <= cap)
            peak = maxOf(peak, hand.size)
        }
        assertTrue(hand.isEmpty())
        assertFalse(onBoard.any { it })
        return peak
    }

    private fun verifyShape(shape: BoardShape) {
        val positions = shape.positions
        assertTrue(positions.size in 2..256 && positions.size % 2 == 0)
        assertTrue(positions.maxOf { it.x2 } - positions.minOf { it.x2 } <= 30)
        assertTrue(positions.maxOf { it.y2 } - positions.minOf { it.y2 } <= 30)
        assertTrue(positions.all { it.layer in 0..3 })
        val centerX2 = positions.minOf { it.x2 } + positions.maxOf { it.x2 }
        val centerY2 = positions.minOf { it.y2 } + positions.maxOf { it.y2 }
        val occupied = positions.toSet()
        positions.forEach { tile ->
            assertTrue(TilePosition(centerX2 - tile.x2, tile.y2, tile.layer) in occupied)
            assertTrue(TilePosition(tile.x2, centerY2 - tile.y2, tile.layer) in occupied)
            if (tile.layer > 0) {
                // Sample the center of each unit square in its footprint.
                for (dx in listOf(-0.5, 0.5)) for (dy in listOf(-0.5, 0.5)) {
                    assertTrue(positions.any {
                        it.layer == tile.layer - 1 &&
                            abs(it.x2 - tile.x2 - dx) < 1 && abs(it.y2 - tile.y2 - dy) < 1
                    })
                }
            }
        }
        positions.groupBy { it.layer }.values.forEach { layer ->
            val visited = mutableSetOf(layer.first())
            val queue = ArrayDeque<TilePosition>().apply { add(layer.first()) }
            while (queue.isNotEmpty()) {
                val tile = queue.removeFirst()
                layer.filter { other ->
                    (abs(tile.x2 - other.x2) == 2 && abs(tile.y2 - other.y2) < 2) ||
                        (abs(tile.y2 - other.y2) == 2 && abs(tile.x2 - other.x2) < 2)
                }.forEach { if (visited.add(it)) queue.add(it) }
            }
            assertEquals("Layer is disconnected on ${shape.id}", layer.size, visited.size)
        }
    }

    @Test fun curatedStylesHaveSupportedSymmetricGeometryAndPreserveTheGarden() {
        assertEquals(listOf(68, 68, 64, 68, 68, 72), BoardStyles.curated.map { it.positions.size })
        assertEquals(BoardStyles.curated.size, BoardStyles.curated.map { it.id }.toSet().size)
        BoardStyles.curated.forEach { verifyShape(it) }
        assertFalse(BoardStyles.garden in BoardStyles.curated)
        assertEquals("garden-v1", BoardStyles.garden.id)
        assertEquals(50, BoardStyles.garden.positions.size)
        assertEquals(GardenLayout.positions, BoardStyles.garden.positions)
        assertSame(BoardStyles.garden.geometry, BoardStyles.garden.geometry)
    }

    @Test fun newBoardsBoundPortraitHeightAndAspectRatioWhileKeepingNegativeSpace() {
        val footprints = BoardStyles.curated.map { shape ->
            assertTrue("${shape.id} exceeds seven columns",
                shape.positions.maxOf { it.x2 } - shape.positions.minOf { it.x2 } <= 12)
            val base = shape.positions.filter { it.layer == 0 }
            val width = (base.maxOf { it.x2 } - base.minOf { it.x2 }) / 2 + 1
            val height = (base.maxOf { it.y2 } - base.minOf { it.y2 }) / 2 + 1
            assertTrue("${shape.id} should use six to seven columns", width in 6..7)
            assertTrue("${shape.id} should stay within eight to ten rows", height in 8..10)
            assertTrue("${shape.id} should remain taller than it is wide", height > width)
            assertTrue("${shape.id} is too tall for its width", height * 5 <= width * 8)
            assertTrue("${shape.id} should keep a substantial deal", shape.positions.size in 64..80)
            assertTrue("${shape.id} should use two or three supported layers", shape.positions.maxOf { it.layer } in 1..2)
            assertTrue("${shape.id} has a solid rectangular footprint", base.size < width * height)
            base.map { it.x2 to it.y2 }.toSet()
        }
        assertEquals("Each new style needs its own base silhouette", BoardStyles.curated.size, footprints.toSet().size)
        val splitRowStyles = BoardStyles.curated.count { shape ->
            shape.positions.filter { it.layer == 0 }.groupBy { it.y2 }.values.any { row ->
                row.maxOf { it.x2 } - row.minOf { it.x2 } > (row.size - 1) * 2
            }
        }
        assertTrue("Most styles should have real openings, beyond centered row profiles", splitRowStyles >= 5)
        val wreath = BoardStyles.curated.single { it.id == "wreath-v4" }
        assertTrue("The wreath opening must remain clear on every layer",
            wreath.positions.none { it.x2 in 4..8 && it.y2 in 6..12 })
        val lantern = BoardStyles.curated.single { it.id == "lantern-v4" }
        assertTrue("The lantern window must remain clear through its new third layer",
            lantern.positions.none { it.x2 in 4..6 && it.y2 in 6..8 })
    }

    @Test fun thousandsOfDealsHaveBalancedCountsAndIndependentlyVerifiedWinningRoutes() {
        for (shape in BoardStyles.curated) {
            val faceCounts = setOf(1, 2, 3, 6, 12, shape.positions.size / 2)
            for (seed in 0L until 50L) for (faceCount in faceCounts) for (cap in 1..3) {
                val deal = BoardGenerator.generate(seed, faceCount, shape, cap)
                verifyWitness(deal, faceCount, cap)
                if (seed == 0L && faceCount == 12 && cap == 3) {
                    System.getenv("FAIRY_EXPORT_SHAPES")?.let { directory ->
                        val output = File(directory).apply { mkdirs() }
                        val positions = shape.positions.joinToString(",") { "[${it.x2},${it.y2},${it.layer}]" }
                        val faces = deal.faces.joinToString(",") { (6 + 4 * it).toString() }
                        File(output, "${shape.id}.json").writeText(
                            """{"version":4,"layout":"${shape.id}","positions":[$positions],"faces":[$faces],"picks":[],"haptics":true}""",
                        )
                    }
                }
            }
        }
    }

    @Test fun constructorCanUseTheEntireEightByEightByFourEnvelope() {
        val shape = BoardShape("maximum", buildList {
            for (layer in 0..3) for (row in 0..7) for (column in 0..7) {
                add(TilePosition(2 * column, 2 * row, layer))
            }
        })
        assertEquals(256, shape.positions.size)
        for (seed in listOf(Long.MIN_VALUE, -1L, 0L, Long.MAX_VALUE)) {
            for (faceCount in listOf(1, 12, 24, 128)) for (cap in 1..3) {
                verifyWitness(BoardGenerator.generate(seed, faceCount, shape, cap), faceCount, cap)
            }
        }
    }

    @Test fun landscapeStylesHaveDistinctSupportedShallowSilhouettesWithOpenings() {
        assertEquals(6, BoardStyles.landscape.size)
        assertEquals(6, BoardStyles.landscape.map { it.id }.toSet().size)
        assertTrue(BoardStyles.landscape.none { it.id in BoardStyles.curated.map(BoardShape::id) })
        val footprints = BoardStyles.landscape.map { shape ->
            verifyShape(shape)
            val base = shape.positions.filter { it.layer == 0 }
            val width = (base.maxOf { it.x2 } - base.minOf { it.x2 }) / 2 + 1
            val height = (base.maxOf { it.y2 } - base.minOf { it.y2 }) / 2 + 1
            assertTrue("${shape.id} exceeds twelve tile columns",
                shape.positions.maxOf { it.x2 } - shape.positions.minOf { it.x2 } <= 22)
            assertTrue("${shape.id} should remain wider than it is tall", width > height)
            assertEquals("${shape.id} should fit four upright rows", 4, height)
            assertTrue("${shape.id} needs space around its silhouette", base.size < width * height)
            assertTrue("${shape.id} should support a substantial landscape deal", shape.positions.size in 64..76)
            assertTrue("${shape.id} should use two or three supported layers", shape.positions.maxOf { it.layer } in 1..2)
            base.map { it.x2 to it.y2 }.toSet()
        }
        assertEquals("Landscape shapes should have distinct silhouettes", 6, footprints.toSet().size)
        val splitRowStyles = BoardStyles.landscape.count { shape ->
            shape.positions.filter { it.layer == 0 }.groupBy { it.y2 }.values.any { row ->
                row.maxOf { it.x2 } - row.minOf { it.x2 } > (row.size - 1) * 2
            }
        }
        assertTrue("Most landscape styles should include openings", splitRowStyles >= 5)
        val halo = BoardStyles.landscape.single { it.id == "meadow-halo-landscape-v2" }
        val haloBase = halo.positions.filter { it.layer == 0 }
        val minX = haloBase.minOf { it.x2 }
        val maxX = haloBase.maxOf { it.x2 }
        val innerRows = haloBase.minOf { it.y2 } + 2..haloBase.maxOf { it.y2 } - 2
        val opening = (minX..maxX step 2).filter { x -> haloBase.none { it.x2 == x && it.y2 in innerRows } }
        assertTrue("The halo needs a central opening at least four columns wide", opening.size >= 4)
        assertTrue("The halo opening should be continuous", opening.zipWithNext().all { (left, right) -> right - left == 2 })
        assertTrue("The halo opening should straddle its center", opening.first() < (minX + maxX) / 2 &&
            opening.last() > (minX + maxX) / 2)
        assertTrue("The halo opening must remain clear through every layer",
            halo.positions.none { it.x2 in opening && it.y2 in innerRows })
        // Portrait masks were not transposed into landscape; their tall identity stays intact.
        val portraitTransposes = BoardStyles.curated.map { shape ->
            shape.positions.filter { it.layer == 0 }.map { it.y2 to it.x2 }.toSet()
        }
        assertTrue(footprints.none { it in portraitTransposes })
    }

    @Test fun landscapeDealsHaveBalancedCountsAndIndependentlyVerifiedRepeatableWinningRoutes() {
        for (shape in BoardStyles.landscape) {
            val distinctDeals = mutableSetOf<List<Int>>()
            val faceCounts = setOf(1, 2, 3, 6, 12, shape.positions.size / 2)
            for (seed in 0L until 50L) for (faceCount in faceCounts) for (cap in 1..3) {
                val deal = BoardGenerator.generate(seed, faceCount, shape, cap)
                verifyWitness(deal, faceCount, cap)
                if (faceCount == 12 && cap == 3) {
                    assertEquals(deal, BoardGenerator.generate(seed, faceCount, shape, cap))
                    distinctDeals.add(deal.faces)
                    if (seed == 0L) {
                        System.getenv("FAIRY_EXPORT_LANDSCAPE_SHAPES")?.let { directory ->
                            val output = File(directory).apply { mkdirs() }
                            val positions = shape.positions.joinToString(",") { "[${it.x2},${it.y2},${it.layer}]" }
                            val faces = deal.faces.joinToString(",") { (6 + 4 * it).toString() }
                            File(output, "${shape.id}.json").writeText(
                                """{"version":4,"layout":"${shape.id}","positions":[$positions],"faces":[$faces],"picks":[],"haptics":true}""",
                            )
                        }
                    }
                }
            }
            assertTrue("Landscape deals should vary faces for ${shape.id}", distinctDeals.size > 45)
        }
    }

    @Test fun customBoardsCanUseTheFullSixteenColumnAndRowEnvelope() {
        val shape = BoardShape("maximum-width-and-height", buildList {
            for (row in 0 until 16) for (column in 0 until 16) {
                add(TilePosition(2 * column, 2 * row, 0))
            }
        })
        assertEquals(256, shape.positions.size)
        verifyShape(shape)
        for (seed in listOf(Long.MIN_VALUE, 0L, Long.MAX_VALUE)) {
            for (faceCount in listOf(1, 12, 128)) for (cap in 1..3) {
                verifyWitness(BoardGenerator.generate(seed, faceCount, shape, cap), faceCount, cap)
            }
        }
    }

    @Test fun customBoardsCanUseFifteenRowsAndTheFullSixteenRowEnvelope() {
        val fifteenRows = BoardShape("tall-custom", buildList {
            for (row in 0 until 15) for (column in 0 until 2) {
                add(TilePosition(2 * column, 2 * row, 0))
            }
        })
        val fullHeight = BoardShape("maximum-height", buildList {
            for (layer in 0..1) for (row in 0 until 16) for (column in 0 until 8) {
                add(TilePosition(2 * column, 2 * row, layer))
            }
        })
        assertEquals(30, fifteenRows.positions.size)
        assertEquals(256, fullHeight.positions.size)
        for (shape in listOf(fifteenRows, fullHeight)) {
            verifyShape(shape)
            for (seed in listOf(Long.MIN_VALUE, 0L, Long.MAX_VALUE)) for (cap in 1..3) {
                verifyWitness(BoardGenerator.generate(seed, 12, shape, cap), 12, cap)
            }
        }
    }

    @Test fun proceduralStylesAreDeterministicSupportedConnectedAndBounded() {
        val distinct = mutableSetOf<List<TilePosition>>()
        for (maxLayers in 1..4) for (seed in 0L until 125L) {
            val shape = BoardStyles.procedural(seed, maxLayers)
            verifyShape(shape)
            assertTrue("Procedural board exceeds seven columns",
                shape.positions.maxOf { it.x2 } - shape.positions.minOf { it.x2 } <= 12)
            val base = shape.positions.filter { it.layer == 0 }
            val width = (base.maxOf { it.x2 } - base.minOf { it.x2 }) / 2 + 1
            val height = (base.maxOf { it.y2 } - base.minOf { it.y2 }) / 2 + 1
            assertTrue("Procedural base became a solid rectangle", base.size < width * height)
            assertTrue("Procedural shape should favor a vertical silhouette", height > width)
            assertTrue("Procedural shape exceeds ten rows", height <= 10)
            assertTrue("Procedural shape is too tall for its width", height * 5 <= width * 8)
            assertTrue(shape.positions.maxOf { it.layer } < maxLayers)
            assertEquals(shape, BoardStyles.procedural(seed, maxLayers))
            distinct.add(shape.positions)
            val faceCount = minOf(12, shape.positions.size / 2)
            verifyWitness(BoardGenerator.generate(seed, faceCount, shape), faceCount, 3)
        }
        assertTrue("Procedural generator should vary its geometry", distinct.size > 100)
    }

    @Test fun dealSeedsAreRepeatableAndDifferentSeedsVaryFacesAndSelectedStyles() {
        val selectedStyles = mutableSetOf<String>()
        val faceAssignments = mutableSetOf<List<Int>>()
        for (seed in 0L until 100L) {
            val first = BoardGenerator.generate(seed, 12)
            assertEquals(first, BoardGenerator.generate(seed, 12))
            selectedStyles.add(first.shape.id)
            faceAssignments.add(BoardGenerator.generate(seed, 12, BoardStyles.garden).faces)
        }
        assertEquals(BoardStyles.curated.map { it.id }.toSet(), selectedStyles)
        assertTrue(faceAssignments.size > 90)
    }

    @Test fun bufferParameterAllowsInterleavedPairsWithoutForcingAdjacentMatches() {
        for (cap in 1..3) {
            val peaks = (0L until 20L).map { seed ->
                verifyWitness(BoardGenerator.generate(seed, 12, BoardStyles.garden, cap), 12, cap)
            }
            assertEquals(cap, peaks.max())
        }
    }

    @Test fun cachedBlockersAgreeWithDirectRulesForArbitraryIntermediateBoards() {
        val random = Random(590)
        (BoardStyles.curated + BoardStyles.landscape).forEach { shape ->
            repeat(100) {
                val onBoard = BooleanArray(shape.positions.size) { random.nextBoolean() }
                shape.positions.indices.forEach { index ->
                    assertEquals(free(index, shape.positions, onBoard), shape.geometry.isFree(index, onBoard))
                }
            }
            assertFalse(shape.geometry.isFree(-1, BooleanArray(shape.positions.size)))
            assertFalse(shape.geometry.isFree(shape.positions.size, BooleanArray(shape.positions.size)))
        }
    }

    @Test fun shapeCopiesInputAndHasStructuralEquality() {
        val positions = mutableListOf(TilePosition(0, 0, 0), TilePosition(2, 0, 0))
        val shape = BoardShape("test", positions)
        val equivalent = BoardShape("test", positions.toList())
        assertEquals(shape, equivalent)
        assertEquals(shape.hashCode(), equivalent.hashCode())
        positions.clear()
        assertEquals(2, shape.positions.size)
        assertThrows(UnsupportedOperationException::class.java) {
            (shape.positions as MutableList<TilePosition>).clear()
        }
    }

    @Test fun invalidShapesAndGeneratorParametersAreRejected() {
        val pair = listOf(TilePosition(0, 0, 0), TilePosition(2, 0, 0))
        assertThrows(IllegalArgumentException::class.java) { BoardShape("", pair) }
        assertThrows(IllegalArgumentException::class.java) { BoardShape("bad id", pair) }
        assertThrows(IllegalArgumentException::class.java) { BoardShape("x".repeat(81), pair) }
        assertThrows(IllegalArgumentException::class.java) { BoardShape("empty", emptyList()) }
        assertThrows(IllegalArgumentException::class.java) { BoardShape("odd", pair.take(1)) }
        assertThrows(IllegalArgumentException::class.java) { BoardShape("duplicate", List(2) { pair[0] }) }
        assertThrows(IllegalArgumentException::class.java) {
            BoardShape("overlap", listOf(TilePosition(0, 0, 0), TilePosition(1, 0, 0)))
        }
        assertThrows(IllegalArgumentException::class.java) {
            BoardShape("wide", listOf(TilePosition(0, 0, 0), TilePosition(32, 0, 0)))
        }
        assertThrows(IllegalArgumentException::class.java) {
            BoardShape("too-tall", listOf(TilePosition(0, 0, 0), TilePosition(0, 32, 0)))
        }
        assertThrows(IllegalArgumentException::class.java) { BoardShape("floating", pair.map { it.copy(layer = 1) }) }
        assertThrows(IllegalArgumentException::class.java) { BoardShape("high", pair.map { it.copy(layer = 4) }) }
        assertThrows(IllegalArgumentException::class.java) {
            BoardShape("partial-support", pair + pair.map { it.copy(y2 = 1, layer = 1) })
        }
        val asymmetric = listOf(TilePosition(0, 0, 0), TilePosition(2, 2, 0))
        assertThrows(IllegalArgumentException::class.java) { BoardShape("asymmetric", asymmetric) }
        assertEquals(2, BoardShape("custom", asymmetric, requireSymmetry = false).positions.size)
        for (faceCount in listOf(-1, 0, 26)) {
            assertThrows(IllegalArgumentException::class.java) { BoardGenerator.generate(0, faceCount, BoardStyles.garden) }
        }
        for (cap in listOf(0, 4)) {
            assertThrows(IllegalArgumentException::class.java) { BoardGenerator.generate(0, 12, BoardStyles.garden, cap) }
        }
        for (layers in listOf(0, 5)) {
            assertThrows(IllegalArgumentException::class.java) { BoardStyles.procedural(0, layers) }
        }
        assertThrows(IllegalArgumentException::class.java) { BoardStyles.garden.geometry.isFree(0, BooleanArray(1)) }
    }

    @Test fun benchmarkShapeConstructionAndWarmDealGeneration() {
        repeat(100) {
            BoardStyles.procedural(it.toLong())
            BoardGenerator.generate(it.toLong(), 12)
        }
        val shapeNanos = measureNanoTime { repeat(1_000) { BoardStyles.procedural(it.toLong()) } }
        val dealNanos = measureNanoTime { repeat(1_000) { BoardGenerator.generate(it.toLong(), 12) } }
        println("Board generation benchmark (JVM; 1,000 iterations): shapes=${shapeNanos / 1_000_000.0} ms, curated deals=${dealNanos / 1_000_000.0} ms")
        // Informational only: device load and JVM warmup must not turn this into a flaky test.
    }
}
