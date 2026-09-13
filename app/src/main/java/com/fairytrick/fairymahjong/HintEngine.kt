package com.fairytrick.fairymahjong

import java.math.BigInteger
import java.util.Collections

/**
 * An ordered sequence to the next automatic match, backed by a complete winning continuation.
 * [matchingTiles] contains the two physical tile indices which match at the end of [picks].
 * One may already be held; either may still be covered. Other picks are temporary buffer tiles.
 * Only [nextPick] should be presented as the tile to tap now.
 */
data class GameHint(
    val picks: List<Int>,
    val matchingTiles: List<Int>,
    val solution: List<Int>,
) {
    val nextPick: Int get() = picks.first()
}

sealed interface HintResult {
    data class Found(val hint: GameHint) : HintResult
    data object Unwinnable : HintResult
    data object Complete : HintResult
    data object SearchLimit : HintResult
    data object Cancelled : HintResult
}

/**
 * Exact, buffer-aware hints for the current position, including picks the player made off the
 * generated solution. Run on a worker thread; each instance belongs to one worker at a time.
 * A bounded search can return [HintResult.SearchLimit], which does not mean the board is lost.
 * Found is returned only after replaying an entire winning suffix with the live geometry rules.
 *
 * Board identities need even initial multiplicities. Faces are arbitrary nonnegative integers;
 * the engine supports the full 256-position shape limit independently of the artwork catalog.
 */
class HintEngine {
    private var cachedPositions: List<TilePosition>? = null
    private var cachedFaces: List<Int>? = null
    private var cachedSolution: List<Int>? = null

    fun findHint(
        snapshot: GameSnapshot,
        maxNodes: Int = 200_000,
        isCancelled: () -> Boolean = { false },
    ): HintResult {
        require(maxNodes >= 0) { "The search budget must not be negative" }
        if (isCancelled()) return HintResult.Cancelled
        try {
            val position = Position(snapshot, isCancelled)
            if (position.remaining.signum() == 0) return HintResult.Complete
            if (position.heldCount == MahjongGame.HAND_CAPACITY) return HintResult.Unwinnable

            // A shape ID is not a geometry cache key: saves and custom shapes may reuse it.
            if (cachedPositions == snapshot.shape.positions && cachedFaces == position.faces) {
                val suffix = cachedSolution?.filter { position.onBoard[it] }
                if (suffix != null) {
                    val hint = position.verifiedHint(suffix)
                    if (hint != null) return HintResult.Found(hint)
                }
            } else {
                cachedPositions = null
                cachedFaces = null
                cachedSolution = null
            }

            val solution = Search(position, maxNodes).solve() ?: return HintResult.Unwinnable
            val hint = checkNotNull(position.verifiedHint(solution)) {
                "Hint search produced an invalid winning continuation"
            }
            cachedPositions = snapshot.shape.positions
            cachedFaces = position.faces
            cachedSolution = hint.solution
            return HintResult.Found(hint)
        } catch (stop: SearchStopped) {
            return stop.result
        }
    }

    private class SearchStopped(val result: HintResult) : RuntimeException(null, null, false, false)

    private class Position(snapshot: GameSnapshot, private val cancelled: () -> Boolean) {
        val faces = immutableCopy(snapshot.faces)
        val geometry = snapshot.shape.geometry
        val onBoard = BooleanArray(faces.size) { true }
        val faceOf: IntArray
        val held: BooleanArray
        val heldTile: IntArray
        var heldCount = 0
        var remaining = BigInteger.ONE.shiftLeft(faces.size).subtract(BigInteger.ONE)

        init {
            require(faces.size == snapshot.shape.positions.size) { "Unexpected board size" }
            require(faces.all { it >= 0 }) { "Invalid tile face" }
            val counts = faces.groupingBy { it }.eachCount()
            require(counts.values.all { it % 2 == 0 }) { "Every face must have an even tile count" }
            val denseFaces = counts.keys.withIndex().associate { it.value to it.index }
            faceOf = IntArray(faces.size) { denseFaces.getValue(faces[it]) }
            held = BooleanArray(denseFaces.size)
            heldTile = IntArray(denseFaces.size) { -1 }
            snapshot.picks.toList().forEach { tile ->
                checkCancellation()
                require(heldCount < MahjongGame.HAND_CAPACITY && geometry.isFree(tile, onBoard)) {
                    "Invalid pick history"
                }
                val face = faceOf[tile]
                onBoard[tile] = false
                remaining = remaining.clearBit(tile)
                held[face] = !held[face]
                heldCount += if (held[face]) 1 else -1
                heldTile[face] = if (held[face]) tile else -1
            }
            checkCancellation()
        }

        fun checkCancellation() {
            if (cancelled()) throw SearchStopped(HintResult.Cancelled)
        }

        /** Replay independently of search state, including held physical indices for highlights. */
        fun verifiedHint(solution: List<Int>): GameHint? {
            checkCancellation()
            if (solution.size != remaining.bitCount()) return null
            val board = onBoard.copyOf()
            val hand = heldTile.copyOf()
            var handSize = heldCount
            var firstMatchLength = 0
            var match: List<Int>? = null
            solution.forEachIndexed { offset, tile ->
                checkCancellation()
                if (handSize >= MahjongGame.HAND_CAPACITY || !geometry.isFree(tile, board)) return null
                board[tile] = false
                val face = faceOf[tile]
                val partner = hand[face]
                if (partner >= 0) {
                    hand[face] = -1
                    handSize -= 1
                    if (match == null) {
                        match = listOf(partner, tile)
                        firstMatchLength = offset + 1
                    }
                } else {
                    hand[face] = tile
                    handSize += 1
                }
                if (handSize >= MahjongGame.HAND_CAPACITY) return null
            }
            if (handSize != 0 || board.any { it } || match == null) return null
            return GameHint(
                immutableCopy(solution.take(firstMatchLength)),
                immutableCopy(checkNotNull(match)),
                immutableCopy(solution),
            )
        }
    }

    private class Search(private val position: Position, private val maxNodes: Int) {
        private val onBoard = position.onBoard.copyOf()
        private val held = position.held.copyOf()
        private val path = ArrayList<Int>(position.remaining.bitCount())
        private val losing = HashSet<BigInteger>()
        private var nodes = 0
        private var winningPath: List<Int>? = null

        fun solve(): List<Int>? {
            // Before the first pair, every pick grows the hand. Thus all winning first-match
            // prefixes have length at most 4 - current occupancy. Try shorter explanations first.
            for (length in 1..MahjongGame.HAND_CAPACITY - position.heldCount) {
                if (firstMatch(position.remaining, position.heldCount, length)) return winningPath
            }
            return null
        }

        private fun visit() {
            position.checkCancellation()
            if (nodes >= maxNodes) throw SearchStopped(HintResult.SearchLimit)
            nodes += 1
        }

        private fun firstMatch(mask: BigInteger, handSize: Int, picksLeft: Int): Boolean {
            visit()
            if (mask in losing) return false
            for (tile in candidates(handSize)) {
                position.checkCancellation()
                val matching = held[position.faceOf[tile]]
                if (matching != (picksLeft == 1)) continue
                val nextSize = handSize + if (matching) -1 else 1
                if (nextSize >= MahjongGame.HAND_CAPACITY) continue
                val result = withPick(tile) {
                    val child = mask.clearBit(tile)
                    if (matching) winning(child, nextSize)
                    else firstMatch(child, nextSize, picksLeft - 1)
                }
                if (result) return true
            }
            // A depth-restricted prefix failure does not prove the position loses.
            return false
        }

        private fun winning(mask: BigInteger, handSize: Int): Boolean {
            position.checkCancellation()
            if (mask.signum() == 0) {
                check(handSize == 0)
                winningPath = path.toList()
                return true
            }
            if (mask in losing) return false
            visit()
            for (tile in candidates(handSize)) {
                position.checkCancellation()
                val nextSize = handSize + if (held[position.faceOf[tile]]) -1 else 1
                if (nextSize >= MahjongGame.HAND_CAPACITY) continue
                if (withPick(tile) { winning(mask.clearBit(tile), nextSize) }) return true
            }
            // Cancellation / a spent budget throws before reaching here. Unknowns are never
            // memoized as losses. Even face counts make held parity a function of mask alone.
            losing.add(mask)
            return false
        }

        private inline fun withPick(tile: Int, action: () -> Boolean): Boolean {
            val face = position.faceOf[tile]
            onBoard[tile] = false
            held[face] = !held[face]
            path.add(tile)
            try {
                return action()
            } finally {
                path.removeAt(path.lastIndex)
                held[face] = !held[face]
                onBoard[tile] = true
            }
        }

        private fun candidates(handSize: Int): List<Int> {
            val available = ArrayList<Int>()
            val faceCounts = IntArray(held.size)
            onBoard.indices.forEach { tile ->
                position.checkCancellation()
                if (position.geometry.isFree(tile, onBoard) &&
                    (handSize < MahjongGame.HAND_CAPACITY - 1 || held[position.faceOf[tile]])
                ) {
                    available.add(tile)
                    faceCounts[position.faceOf[tile]] += 1
                }
            }
            // Good ordering improves speed, but no heuristic is accepted as a proof of safety.
            available.sortWith(compareBy<Int> {
                val face = position.faceOf[it]
                when {
                    held[face] -> 0
                    faceCounts[face] >= 2 -> 1
                    else -> 2
                }
            }.thenBy { it })
            return available
        }
    }

    private companion object {
        fun <T> immutableCopy(values: List<T>): List<T> =
            Collections.unmodifiableList(ArrayList(values))
    }
}
