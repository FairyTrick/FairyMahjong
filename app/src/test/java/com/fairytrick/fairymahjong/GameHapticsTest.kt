package com.fairytrick.fairymahjong

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GameHapticsTest {
    @Test fun solvedBoardHasOneCompletionCueWithoutADuplicateLastMatch() {
        val deal = MahjongGame.generateDeal(514)
        val game = MahjongGame(deal.snapshot)
        val cues = deal.solution.map { pick ->
            val result = game.pickTile(pick)
            GameHapticEvent.forPick(result, game.isComplete, game.isGameOver)
        }
        assertTrue(game.isComplete)
        assertEquals(game.tileCount / 2, cues.count { it == GameHapticEvent.PICK })
        assertEquals(game.tileCount / 2 - 1, cues.count { it == GameHapticEvent.MATCH })
        assertEquals(1, cues.count { it == GameHapticEvent.COMPLETE })
        assertEquals(GameHapticEvent.COMPLETE, cues.last())
        assertNull(GameHapticEvent.forPick(game.pickTile(deal.solution.last()),
            game.isComplete, game.isGameOver))
    }

    @Test fun fillingTheHandGetsOneFailureCueAndSubsequentTapsStaySilent() {
        // Two rows of four isolated tiles fit the game's supported board footprint.
        val shape = BoardShape("haptics-test", List(8) {
            TilePosition((it % 4) * 4, (it / 4) * 4, 0)
        })
        val game = MahjongGame(GameSnapshot(listOf(0, 1, 2, 3, 0, 1, 2, 3), shape = shape))
        val cues = (0..3).map { pick ->
            val result = game.pickTile(pick)
            GameHapticEvent.forPick(result, game.isComplete, game.isGameOver)
        }
        assertTrue(game.isHandFull)
        assertEquals(listOf(GameHapticEvent.PICK, GameHapticEvent.PICK,
            GameHapticEvent.PICK, GameHapticEvent.FULL_HAND), cues)
        assertNull(GameHapticEvent.forPick(game.pickTile(4), game.isComplete, game.isGameOver))
    }
}
