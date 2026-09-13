package com.fairytrick.fairymahjong

/** A small opening-only adjustment; face frequencies and the winning route stay intact. */
internal object OpeningPairBalance {
    private const val MIN_BOARD_TILES = 64
    private const val OPENING_PICKS = 16
    private const val MIN_OPENING_PAIRS = 3

    fun adjust(board: GeneratedBoard): GeneratedBoard {
        if (board.faces.size < MIN_BOARD_TILES) return board

        val onBoard = BooleanArray(board.faces.size) { true }
        val free = BooleanArray(board.faces.size) { board.shape.geometry.isFree(it, onBoard) }
        val freeCounts = mutableMapOf<Int, Int>()
        board.faces.forEachIndexed { index, face ->
            if (free[index]) freeCounts[face] = (freeCounts[face] ?: 0) + 1
        }
        val openingPairs = freeCounts.values.sumOf { it / 2 }
        if (openingPairs < MIN_OPENING_PAIRS) return board

        val pending = mutableMapOf<Int, Int>()
        val pairs = mutableListOf<WitnessPair>()
        board.solution.take(OPENING_PICKS).forEach { index ->
            val face = board.faces[index]
            val first = pending.remove(face)
            if (first == null) pending[face] = index
            else pairs.add(WitnessPair(face, first, index))
        }

        // At most eight complete pairs, hence only 28 candidate exchanges. Excluding
        // held faces preserves even the hand's tile IDs and order at the cutoff.
        for (firstIndex in pairs.indices) {
            val first = pairs[firstIndex]
            if (first.face in pending) continue
            for (secondIndex in firstIndex + 1 until pairs.size) {
                val second = pairs[secondIndex]
                if (second.face == first.face || second.face in pending) continue

                val firstCount = freeCounts[first.face] ?: 0
                val secondCount = freeCounts[second.face] ?: 0
                val transferred = second.freeEndpoints(free) - first.freeEndpoints(free)
                val before = firstCount / 2 + secondCount / 2
                val after = (firstCount + transferred) / 2 + (secondCount - transferred) / 2
                if (after != before - 1) continue

                // Exactly one fewer opening pair, with at least two still available.
                // Relabeling whole witness pairs cannot increase unmatched occupancy:
                // equal labels on overlapping pair intervals can only cancel out.
                val faces = board.faces.toMutableList()
                faces[first.first] = second.face
                faces[first.second] = second.face
                faces[second.first] = first.face
                faces[second.second] = first.face
                return board.copy(faces = faces)
            }
        }
        return board
    }

    private data class WitnessPair(val face: Int, val first: Int, val second: Int) {
        fun freeEndpoints(free: BooleanArray): Int =
            (if (free[first]) 1 else 0) + (if (free[second]) 1 else 0)
    }
}
