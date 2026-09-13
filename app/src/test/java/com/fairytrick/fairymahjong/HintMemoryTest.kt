package com.fairytrick.fairymahjong

import org.junit.Assert.*
import org.junit.Test

/** Also run this class with a 64 MiB heap to exercise the search's retained-memory bound. */
class HintMemoryTest {
    @Test fun difficultLargeRetryStaysBoundedWithoutPoisoningLaterHints() {
        val shape = BoardShape("hint-memory-envelope", buildList {
            for (layer in 0..3) for (y in 0..7) for (x in 0..7) {
                add(TilePosition(x * 2, y * 2, layer))
            }
        })
        // This valid, solvable custom deal exhausted a 64 MiB heap when every searched
        // loss was retained. It uses only identities supported by the artwork catalog.
        val generated = BoardGenerator.generate(2L, MahjongGame.FACE_COUNT, shape)
        val snapshot = GameSnapshot(generated.faces, shape = shape)
        val witness = MahjongGame(snapshot)
        generated.solution.forEach { assertNotEquals(PickResult.IGNORED, witness.pickTile(it)) }
        assertTrue(witness.isComplete)

        val engine = HintEngine()
        when (val result = engine.findHint(snapshot, maxNodes = 800_000)) {
            HintResult.SearchLimit -> Unit
            is HintResult.Found -> {
                val game = MahjongGame(snapshot)
                result.hint.solution.forEach {
                    assertNotEquals(PickResult.IGNORED, game.pickTile(it))
                }
                assertTrue(game.isComplete)
            }
            else -> fail("A solvable deal must receive a verified hint or a search limit: $result")
        }

        // An exhausted search must neither retain its large working set nor mark an
        // unknown state as lost. A subsequent ordinary deal still gets a certified hint.
        val ordinary = MahjongGame.newGame(seed = 29L)
        val result = engine.findHint(ordinary.snapshot())
        assertTrue(result is HintResult.Found)
        (result as HintResult.Found).hint.solution.forEach {
            assertNotEquals(PickResult.IGNORED, ordinary.pickTile(it))
        }
        assertTrue(ordinary.isComplete)
    }
}
