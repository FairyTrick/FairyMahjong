package com.fairytrick.fairymahjong

import kotlin.random.Random

/** Coordinates use half-tile units, allowing centered, overlapping layers. */
data class TilePosition(val x2: Int, val y2: Int, val layer: Int)

/** Stable legacy shape for existing garden saves and callers requesting this style. */
object GardenLayout {
    const val ID = "garden-v1"
    val shape: BoardShape get() = BoardStyles.garden
    val positions: List<TilePosition> get() = shape.positions
    internal fun isGeometricallyFree(index: Int, onBoard: BooleanArray): Boolean =
        shape.geometry.isFree(index, onBoard)
}

/** Picks are replayable events, including tiles that have subsequently matched. */
data class GameSnapshot(
    val faces: List<Int>,
    val picks: List<Int> = emptyList(),
    val shape: BoardShape = BoardStyles.garden,
)

enum class PickResult { IGNORED, PICKED, MATCHED }

internal data class SolvableDeal(val snapshot: GameSnapshot, val solution: List<Int>)

/** Pure Kotlin rules; view lifetimes and animation callbacks never change the rules. */
class MahjongGame(snapshot: GameSnapshot) {
    val shape: BoardShape = snapshot.shape
    val positions: List<TilePosition> get() = shape.positions
    val tileCount: Int get() = positions.size
    private val faces = snapshot.faces.toList()
    private val picks = mutableListOf<Int>()
    private val onBoard = BooleanArray(tileCount) { true }
    private val heldTiles = mutableListOf<Int>()
    private var pairs = 0

    init {
        require(faces.size == tileCount) { "Unexpected board size" }
        require(faces.all { it in 0 until FACE_COUNT }) { "Invalid tile face" }
        require(faces.groupingBy { it }.eachCount().values.all { it % 2 == 0 }) {
            "Every face must have an even tile count"
        }
        snapshot.picks.toList().forEach { index ->
            require(pickTile(index) != PickResult.IGNORED) { "Invalid pick history" }
        }
    }

    val hand: List<Int> get() = heldTiles.toList()
    val boardTileCount: Int get() = tileCount - picks.size
    val matchedPairCount: Int get() = pairs
    val clearedTileCount: Int get() = matchedPairCount * 2
    val remainingTileCount: Int get() = boardTileCount + heldTiles.size
    val isComplete: Boolean get() = boardTileCount == 0 && heldTiles.isEmpty()
    val isHandFull: Boolean get() = heldTiles.size == HAND_CAPACITY
    val isGameOver: Boolean get() = !isComplete && faces.indices.none { isPickable(it) }

    fun faceAt(index: Int): Int = faces[index]

    fun isOnBoard(index: Int): Boolean = index in faces.indices && onBoard[index]

    fun isPickable(index: Int): Boolean =
        !isHandFull && shape.geometry.isFree(index, onBoard)

    fun pickTile(index: Int): PickResult {
        if (!isPickable(index)) return PickResult.IGNORED
        onBoard[index] = false
        picks.add(index)
        val matchingSlot = heldTiles.indexOfFirst { faces[it] == faces[index] }
        return if (matchingSlot >= 0) {
            // Match before checking capacity: A, B, C, A leaves B, C in the hand.
            heldTiles.removeAt(matchingSlot)
            pairs += 1
            PickResult.MATCHED
        } else {
            heldTiles.add(index)
            PickResult.PICKED
        }
    }

    /** Restart uses the same deal and clears all picks. */
    fun restart(): Boolean {
        if (picks.isEmpty()) return false
        resetBoard()
        return true
    }

    fun snapshot(): GameSnapshot = GameSnapshot(faces.toList(), picks.toList(), shape)

    private fun resetBoard() {
        picks.clear()
        onBoard.fill(true)
        heldTiles.clear()
        pairs = 0
    }

    companion object {
        const val FACE_COUNT = TileCatalog.FACE_COUNT
        const val HAND_CAPACITY = 4

        fun newGame(
            seed: Long = System.nanoTime(),
            shape: BoardShape? = null,
            orientation: BoardOrientation = BoardOrientation.PORTRAIT,
            difficulty: GameDifficulty = GameDifficulty.NORMAL,
        ): MahjongGame = MahjongGame(generateDeal(seed, shape, orientation, difficulty).snapshot)

        /** The returned witness uses the same geometry and four-slot rules as live play. */
        internal fun generateDeal(
            seed: Long,
            shape: BoardShape? = null,
            orientation: BoardOrientation = BoardOrientation.PORTRAIT,
            difficulty: GameDifficulty = GameDifficulty.NORMAL,
        ): SolvableDeal {
            val random = Random(seed)
            val styles = when (orientation) {
                BoardOrientation.PORTRAIT -> BoardStyles.curated
                BoardOrientation.LANDSCAPE -> BoardStyles.landscape
            }
            val selectedShape = shape ?: styles.random(random)
            val pairCount = selectedShape.positions.size / 2
            val desiredFaces = if (selectedShape.positions.size < 64) random.nextInt(10, 15)
                else random.nextInt(12, 17)
            // Each step adds three identities, leaving repeated pairs in every mode.
            val activeFaces = (desiredFaces + difficulty.faceCountAdjustment).coerceIn(1, pairCount)

            // Prefer different silhouettes and color families before another image of a fairy.
            // Every variant gets an equal chance within its family, including all belongings.
            val families = TileCatalog.faces.drop(TileCatalog.LEGACY_FACE_COUNT)
                .groupBy { it.familyId }.values
                .map { it.shuffled(random) }.shuffled(random)
            val selectedFaces = buildList {
                for (variant in 0 until families.first().size) {
                    families.forEach { family -> add(family[variant].id) }
                }
            }.take(activeFaces)

            val generated = BoardGenerator.generate(
                random.nextLong(), activeFaces, selectedShape,
                preferBufferPlay = difficulty == GameDifficulty.HARD,
            )
            // A one-to-one relabeling preserves pair counts and the winning route.
            return SolvableDeal(
                GameSnapshot(generated.faces.map { selectedFaces[it] }, shape = generated.shape),
                generated.solution,
            )
        }
    }
}
