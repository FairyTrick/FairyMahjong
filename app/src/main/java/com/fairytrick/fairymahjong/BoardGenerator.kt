package com.fairytrick.fairymahjong

import java.util.Collections
import kotlin.math.abs
import kotlin.random.Random

/** A reusable shape, with integer coordinates measured in half-tile units. */
class BoardShape(
    val id: String,
    positions: List<TilePosition>,
    requireSymmetry: Boolean = true,
) {
    val positions: List<TilePosition> = Collections.unmodifiableList(ArrayList(positions))

    /** Curated styles retain their precomputed blockers between deals. */
    val geometry: BoardGeometry by lazy { BoardGeometry(this.positions) }

    init {
        require(ID_PATTERN.matches(id)) { "Shape IDs need 1–80 letters, digits, dots, underscores or hyphens, starting with a letter or digit" }
        require(this.positions.size in 2..256 && this.positions.size % 2 == 0) {
            "A board needs an even number of tiles between 2 and 256"
        }
        require(this.positions.toSet().size == this.positions.size) { "Duplicate tile position" }
        require(this.positions.all { it.layer in 0..3 }) { "Boards support up to four layers" }
        val minX = this.positions.minOf { it.x2 }.toLong()
        val maxX = this.positions.maxOf { it.x2 }.toLong()
        val minY = this.positions.minOf { it.y2 }.toLong()
        val maxY = this.positions.maxOf { it.y2 }.toLong()
        require(maxX - minX <= 30 && maxY - minY <= 30) { "Board exceeds sixteen columns or sixteen rows" }
        this.positions.forEachIndexed { index, tile ->
            require(this.positions.take(index).none { other ->
                other.layer == tile.layer &&
                    abs(other.x2.toLong() - tile.x2) < 2 && abs(other.y2.toLong() - tile.y2) < 2
            }) { "Tiles overlap within one layer" }
            if (tile.layer > 0) {
                // A tile covers four unit squares. Integer half-tile coordinates make this
                // an exact union-coverage test, including layers offset by half a tile.
                for (dx in -1..0) for (dy in -1..0) {
                    val cellX = tile.x2.toLong() + dx
                    val cellY = tile.y2.toLong() + dy
                    require(this.positions.any { below ->
                        below.layer == tile.layer - 1 &&
                            cellX - below.x2 in -1L..0L && cellY - below.y2 in -1L..0L
                    }) { "Every upper tile must be fully supported by the layer below" }
                }
            }
        }
        if (requireSymmetry) {
            val occupied = this.positions.toSet()
            require(this.positions.all { tile ->
                TilePosition((minX + maxX - tile.x2).toInt(), tile.y2, tile.layer) in occupied &&
                    TilePosition(tile.x2, (minY + maxY - tile.y2).toInt(), tile.layer) in occupied
            }) { "Board must be symmetric across both axes on every layer" }
        }
    }

    override fun equals(other: Any?): Boolean =
        this === other || (other is BoardShape && id == other.id && positions == other.positions)

    override fun hashCode(): Int = 31 * id.hashCode() + positions.hashCode()

    override fun toString(): String = "BoardShape(id=$id, tiles=${positions.size})"

    private companion object {
        val ID_PATTERN = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
    }
}

/** Blockers depend only on geometry, never on tile identities or the hand. */
class BoardGeometry(positions: List<TilePosition>) {
    private val positions = positions.toList()
    private val above: Array<IntArray>
    private val left: Array<IntArray>
    private val right: Array<IntArray>
    private val coveredBy: Array<IntArray>
    private val blockedOnLeftBy: Array<IntArray>
    private val blockedOnRightBy: Array<IntArray>

    init {
        val size = this.positions.size
        val aboveLists = Array(size) { mutableListOf<Int>() }
        val leftLists = Array(size) { mutableListOf<Int>() }
        val rightLists = Array(size) { mutableListOf<Int>() }
        val coveredLists = Array(size) { mutableListOf<Int>() }
        val leftDependents = Array(size) { mutableListOf<Int>() }
        val rightDependents = Array(size) { mutableListOf<Int>() }
        this.positions.forEachIndexed { index, tile ->
            this.positions.forEachIndexed { otherIndex, other ->
                if (otherIndex != index) {
                    val dx = other.x2.toLong() - tile.x2
                    val dy = other.y2.toLong() - tile.y2
                    if (other.layer > tile.layer && abs(dx) < 2 && abs(dy) < 2) {
                        aboveLists[index].add(otherIndex)
                        coveredLists[otherIndex].add(index)
                    }
                    if (other.layer == tile.layer && abs(dy) < 2) {
                        if (dx == -2L) {
                            leftLists[index].add(otherIndex)
                            leftDependents[otherIndex].add(index)
                        }
                        if (dx == 2L) {
                            rightLists[index].add(otherIndex)
                            rightDependents[otherIndex].add(index)
                        }
                    }
                }
            }
        }
        above = Array(size) { aboveLists[it].toIntArray() }
        left = Array(size) { leftLists[it].toIntArray() }
        right = Array(size) { rightLists[it].toIntArray() }
        coveredBy = Array(size) { coveredLists[it].toIntArray() }
        blockedOnLeftBy = Array(size) { leftDependents[it].toIntArray() }
        blockedOnRightBy = Array(size) { rightDependents[it].toIntArray() }
    }

    fun isFree(index: Int, onBoard: BooleanArray): Boolean {
        require(onBoard.size == positions.size) { "Occupancy does not match the shape" }
        if (index !in positions.indices || !onBoard[index]) return false
        return above[index].none { onBoard[it] } &&
            (left[index].none { onBoard[it] } || right[index].none { onBoard[it] })
    }

    /** Each blocker edge is visited once; no search or retries are necessary. */
    fun removalOrder(random: Random): List<Int> {
        val size = positions.size
        val aboveCount = IntArray(size) { above[it].size }
        val leftCount = IntArray(size) { left[it].size }
        val rightCount = IntArray(size) { right[it].size }
        val onBoard = BooleanArray(size) { true }
        val queued = BooleanArray(size)
        val available = ArrayList<Int>(size)
        val order = ArrayList<Int>(size)

        fun offer(index: Int) {
            if (onBoard[index] && !queued[index] && aboveCount[index] == 0 &&
                (leftCount[index] == 0 || rightCount[index] == 0)
            ) {
                queued[index] = true
                available.add(index)
            }
        }

        positions.indices.forEach { offer(it) }
        repeat(size) {
            // The highest remaining layer always has a leftmost, uncovered tile.
            check(available.isNotEmpty()) { "Shape has no geometrically legal removal" }
            val slot = random.nextInt(available.size)
            val index = available[slot]
            available[slot] = available.last()
            available.removeAt(available.lastIndex)
            onBoard[index] = false
            order.add(index)
            coveredBy[index].forEach { dependent ->
                aboveCount[dependent] -= 1
                offer(dependent)
            }
            blockedOnLeftBy[index].forEach { dependent ->
                leftCount[dependent] -= 1
                offer(dependent)
            }
            blockedOnRightBy[index].forEach { dependent ->
                rightCount[dependent] -= 1
                offer(dependent)
            }
        }
        return order
    }
}

object BoardStyles {
    val garden: BoardShape = BoardShape("garden-v1", buildList {
        listOf(3, 4, 5, 6, 5, 4, 3).forEachIndexed { row, length ->
            repeat(length) { column -> add(TilePosition(6 - length + 2 * column, 2 * row, 0)) }
        }
        listOf(2, 3, 4, 4, 3, 2).forEachIndexed { row, length ->
            repeat(length) { column -> add(TilePosition(6 - length + 2 * column, 1 + 2 * row, 1)) }
        }
        add(TilePosition(5, 5, 2))
        add(TilePosition(5, 7, 2))
    })

    /** Portrait masks cap both row count and aspect ratio so narrow styles cannot grow too tall. */
    private val patterns = listOf(
        Pattern("butterfly-v4", listOf(
            listOf(".#...#.", "###.###", "###.###", ".#####.", "..###..", "..###..", ".#####.", "###.###", "###.###", ".#...#."),
            listOf(".......", ".#...#.", ".##.##.", "..###..", "...#...", "...#...", "..###..", ".##.##.", ".#...#.", "......."),
            listOf(".......", ".......", ".......", "...#...", "...#...", "...#...", "...#...", ".......", ".......", "......."),
        )),
        Pattern("wreath-v4", listOf(
            listOf("..###..", ".#####.", "###.###", "##...##", "##...##", "##...##", "##...##", "###.###", ".#####.", "..###.."),
            listOf("...#...", "..###..", ".##.##.", ".#...#.", ".#...#.", ".#...#.", ".#...#.", ".##.##.", "..###..", "...#..."),
        )),
        Pattern("hourglass-v4", listOf(
            listOf(".####.", "######", ".####.", "..##..", "..##..", ".####.", "######", ".####."),
            listOf("..##..", ".####.", "..##..", "..##..", "..##..", "..##..", ".####.", "..##.."),
            listOf("......", "..##..", "..##..", "..##..", "..##..", "..##..", "..##..", "......"),
        )),
        Pattern("clover-v4", listOf(
            listOf(".#...#.", "###.###", "###.###", ".##.##.", ".#####.", ".#####.", ".##.##.", "###.###", "###.###", ".#...#."),
            listOf(".......", ".#...#.", ".#...#.", ".#...#.", ".#####.", ".#####.", ".#...#.", ".#...#.", ".#...#.", "......."),
        )),
        Pattern("lantern-v4", listOf(
            listOf("..##..", ".####.", "######", "##..##", "##..##", "######", ".####.", "..##.."),
            listOf("..##..", "..##..", ".####.", ".#..#.", ".#..#.", ".####.", "..##..", "..##.."),
            listOf("......", "..##..", ".####.", ".#..#.", ".#..#.", ".####.", "..##..", "......"),
        )),
        Pattern("twin-crescents-v4", listOf(
            listOf("..#.#..", ".##.##.", "###.###", "###.###", ".#####.", ".#####.", "###.###", "###.###", ".##.##.", "..#.#.."),
            listOf(".......", "..#.#..", ".##.##.", ".##.##.", "..###..", "..###..", ".##.##.", ".##.##.", "..#.#..", "......."),
        )),
    )

    /** New deals stay within seven columns and ten rows; saved boards retain their geometry. */
    val curated: List<BoardShape> = patterns.map { pattern ->
        require(pattern.width <= 7 && pattern.height <= 10 && pattern.height * 5 <= pattern.width * 8)
        fromCells(pattern.id, pattern.width, pattern.layers.map(::cells))
    }

    // Landscape tiles remain upright. Twelve columns and four rows leave room for large
    // faces between the side controls and hand. Extra layers retain substantial deals.
    // These masks are drawn independently; rotating a portrait mask gives too many rows.
    private val landscapePatterns = listOf(
        Pattern("meadow-halo-landscape-v2", listOf(
            listOf("..########..", "####....####", "####....####", "..########.."),
            listOf("..########..", "...#....#...", "...#....#...", "..########.."),
            listOf("...######...", "...#....#...", "...#....#...", "...######..."),
        )),
        Pattern("dragonfly-wings-landscape-v2", listOf(
            listOf(".###....###.", "############", "############", ".###....###."),
            listOf("..##....##..", "...######...", "...######...", "..##....##.."),
            listOf("...#....#...", "...######...", "...######...", "...#....#..."),
        )),
        Pattern("silk-bow-landscape-v2", listOf(
            listOf("###..##..###", "..########..", "..########..", "###..##..###"),
            listOf(".##..##..##.", "..########..", "..########..", ".##..##..##."),
            listOf(".....##.....", ".....##.....", ".....##.....", ".....##....."),
        )),
        Pattern("seedpod-landscape-v2", listOf(
            listOf("..########..", "############", "############", "..########.."),
            listOf("....####....", ".##########.", ".##########.", "....####...."),
        )),
        Pattern("clover-chain-landscape-v2", listOf(
            listOf(".####..####.", "###.####.###", "###.####.###", ".####..####."),
            listOf("..###..###..", "..#.####.#..", "..#.####.#..", "..###..###.."),
            listOf("...##..##...", "....####....", "....####....", "...##..##..."),
        )),
        Pattern("dewdrop-bridge-landscape-v2", listOf(
            listOf(".##########.", "###..##..###", "###..##..###", ".##########."),
            listOf("..########..", "..#..##..#..", "..#..##..#..", "..########.."),
            listOf("...######...", ".....##.....", ".....##.....", "...######..."),
        )),
    )

    /** New landscape deals cap width at twelve columns; saved boards keep their geometry. */
    val landscape: List<BoardShape> = landscapePatterns.map { pattern ->
        require(pattern.width <= 12 && pattern.height == 4)
        fromCells(pattern.id, pattern.width, pattern.layers.map(::cells))
    }

    /** Bounded orbit removal varies the masks while retaining support and connected layers. */
    fun procedural(seed: Long, maxLayers: Int = 3): BoardShape {
        require(maxLayers in 1..4) { "Choose between one and four layers" }
        val random = Random(seed)
        val pattern = patterns.random(random)
        val base = erode(cells(pattern.layers.first()), pattern, random, random.nextInt(0, 4), 24,
            minHeight = pattern.width + 1, maxAspectRatio = 1.6)
        val layerCount = random.nextInt(1, maxLayers + 1)
        val layers = mutableListOf(base)
        repeat(layerCount - 1) {
            layers.add(erode(layers.last(), pattern, random, random.nextInt(2, 8), if (it == 0) 10 else 4))
        }
        return fromCells("procedural-v4-$seed-$maxLayers", pattern.width, layers)
    }

    private data class Pattern(val id: String, val layers: List<List<String>>) {
        val width = layers.first().first().length
        val height = layers.first().size

        init {
            require(width in 2..16 && height in 2..16 && height % 2 == 0)
            require(layers.all { rows -> rows.size == height && rows.all { it.length == width } })
        }
    }

    private fun cells(rows: List<String>): Set<Int> {
        val width = rows.first().length
        require(rows.all { it.length == width && it.all { mark -> mark == '.' || mark == '#' } })
        return buildSet {
            rows.forEachIndexed { row, columns ->
                columns.forEachIndexed { column, mark -> if (mark == '#') add(row * width + column) }
            }
        }
    }

    private fun fromCells(id: String, width: Int, layers: List<Set<Int>>): BoardShape =
        BoardShape(id, buildList {
            layers.forEachIndexed { layer, occupied ->
                occupied.sorted().forEach { cell ->
                    add(TilePosition(2 * (cell % width), 2 * (cell / width), layer))
                }
            }
        })

    private fun erode(
        source: Set<Int>, pattern: Pattern, random: Random, maxRemovals: Int, minTiles: Int,
        minHeight: Int = 0, maxAspectRatio: Double = Double.POSITIVE_INFINITY,
    ): Set<Int> {
        var occupied = source
        var removed = 0
        val width = pattern.width
        val height = pattern.height
        // Even the largest production mask has only twenty-four reflection orbits.
        for (anchor in (0 until width * (height / 2)).filter { it % width < (width + 1) / 2 }.shuffled(random)) {
            if (removed == maxRemovals) break
            val x = anchor % width
            val y = anchor / width
            val reflectedX = width - 1 - x
            val reflectedY = height - 1 - y
            val orbit = setOf(y * width + x, y * width + reflectedX, reflectedY * width + x, reflectedY * width + reflectedX)
            if (orbit.none { it in occupied }) continue
            val candidate = occupied - orbit
            if (candidate.size >= minTiles &&
                candidate.maxOf { it / width } - candidate.minOf { it / width } + 1 >= minHeight &&
                (candidate.maxOf { it / width } - candidate.minOf { it / width } + 1).toDouble() /
                    (candidate.maxOf { it % width } - candidate.minOf { it % width } + 1) <= maxAspectRatio &&
                connected(candidate, width) && hasNegativeSpace(candidate, width)
            ) {
                occupied = candidate
                removed += 1
            }
        }
        return occupied
    }

    private fun connected(occupied: Set<Int>, width: Int): Boolean {
        val reached = mutableSetOf(occupied.first())
        val queue = ArrayDeque<Int>().apply { add(occupied.first()) }
        while (queue.isNotEmpty()) {
            val cell = queue.removeFirst()
            val neighbors = buildList {
                if (cell % width > 0) add(cell - 1)
                if (cell % width < width - 1) add(cell + 1)
                add(cell - width)
                add(cell + width)
            }
            neighbors.forEach { if (it in occupied && reached.add(it)) queue.add(it) }
        }
        return reached.size == occupied.size
    }

    private fun hasNegativeSpace(occupied: Set<Int>, stride: Int): Boolean {
        val width = occupied.maxOf { it % stride } - occupied.minOf { it % stride } + 1
        val height = occupied.maxOf { it / stride } - occupied.minOf { it / stride } + 1
        return occupied.size < width * height
    }
}

data class GeneratedBoard(val shape: BoardShape, val faces: List<Int>, val solution: List<Int>)

object BoardGenerator {
    /**
     * Constructs a winning witness, then places its faces on a legal geometric removal order.
     * A face held in the hand has odd pick parity. New faces can open only below maxUnmatched;
     * closing a face always remains possible because each face starts with an even tile count.
     * This guarantees a solution, not a specified number of safe alternative moves.
     * A bounded opening adjustment can remove one ready pair without raising witness occupancy
     * or changing the remaining board and hand after the first sixteen witness picks.
     */
    fun generate(
        seed: Long,
        faceCount: Int,
        shape: BoardShape? = null,
        maxUnmatched: Int = 3,
    ): GeneratedBoard {
        require(maxUnmatched in 1..3) { "The winning route may hold one to three unmatched tiles" }
        val random = Random(seed)
        val selectedShape = shape ?: BoardStyles.curated[random.nextInt(BoardStyles.curated.size)]
        val pairCount = selectedShape.positions.size / 2
        require(faceCount in 1..pairCount) { "Each active face needs at least one pair" }
        val solution = selectedShape.geometry.removalOrder(random)
        val remainderFaces = (0 until faceCount).shuffled(random).take(pairCount % faceCount).toSet()
        val remaining = IntArray(faceCount) { 2 * (pairCount / faceCount + if (it in remainderFaces) 1 else 0) }
        val held = BooleanArray(faceCount)
        var heldCount = 0
        val faces = IntArray(selectedShape.positions.size)
        val candidates = IntArray(faceCount)
        solution.forEach { index ->
            var candidateCount = 0
            for (face in 0 until faceCount) {
                if (remaining[face] > 0 && (held[face] || heldCount < maxUnmatched)) {
                    candidates[candidateCount++] = face
                }
            }
            check(candidateCount > 0) { "Even face counts always permit a completion" }
            val face = candidates[random.nextInt(candidateCount)]
            faces[index] = face
            remaining[face] -= 1
            held[face] = !held[face]
            heldCount += if (held[face]) 1 else -1
        }
        check(heldCount == 0)
        return OpeningPairBalance.adjust(GeneratedBoard(selectedShape, faces.toList(), solution))
    }
}
