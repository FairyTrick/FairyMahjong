package com.fairytrick.fairymahjong

/** Difficulty changes matching choices, while keeping the board and four-slot hand unchanged. */
enum class GameDifficulty(internal val faceCountAdjustment: Int) {
    EASY(0),
    NORMAL(3),
    HARD(6);

    fun next(): GameDifficulty = when (this) {
        EASY -> NORMAL
        NORMAL -> HARD
        HARD -> EASY
    }
}
