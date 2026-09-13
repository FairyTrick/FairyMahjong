package com.fairytrick.fairymahjong

import android.app.Application
import android.os.Handler
import android.os.Looper
import android.util.AtomicFile
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.Executors

data class SavedGame(
    val board: GameSnapshot,
    // Retired preference, retained only to round-trip older saves. Runtime haptics are always enabled.
    val hapticsEnabled: Boolean = true,
    val orientation: BoardOrientation = BoardOrientation.PORTRAIT,
)

data class LoadedGame(
    val game: SavedGame,
    val recoveredUnreadableSave: Boolean = false,
    val saveAvailable: Boolean = true,
    val migratedLegacySave: Boolean = false,
)

class MahjongApplication : Application() {
    val gameStore: GameStore by lazy { GameStore(filesDir) }
}

/** One application-owned writer preserves ordering across Activity recreation. */
class GameStore(directory: File) {
    private val file = AtomicFile(File(directory, "practice-board.json"))
    private val worker = Executors.newSingleThreadExecutor { task ->
        Thread(task, "mahjong-save").apply { isDaemon = true }
    }
    private val main = Handler(Looper.getMainLooper())
    // Accessed only on the single worker; a failed disk write still retains live state.
    private var cached: SavedGame? = null
    private var lastWriteSucceeded = true

    fun load(callback: (LoadedGame) -> Unit) {
        worker.execute {
            var recovered = false
            var migrated = false
            var requiresRewrite = false
            var game = cached
            if (game == null) {
                if (file.baseFile.exists() || File(file.baseFile.path + ".bak").exists()) {
                    game = try {
                        val decoded = file.openRead().bufferedReader(Charsets.UTF_8).use {
                            GameSaveCodec.decode(it.readText())
                        }
                        migrated = decoded.migratedLegacySave
                        requiresRewrite = decoded.requiresRewrite
                        decoded.game
                    } catch (_: Exception) {
                        recovered = true
                        null
                    }
                }
                if (game == null) {
                    game = SavedGame(MahjongGame.newGame().snapshot())
                    lastWriteSucceeded = write(game)
                } else if (requiresRewrite) {
                    // Rewrite older formats without losing a compatible garden pick history.
                    lastWriteSucceeded = write(game)
                }
                cached = game
            }
            val result = LoadedGame(
                game = requireNotNull(game),
                recoveredUnreadableSave = recovered,
                saveAvailable = lastWriteSucceeded,
                migratedLegacySave = migrated,
            )
            main.post { callback(result) }
        }
    }

    fun save(game: SavedGame, callback: (Boolean) -> Unit) {
        val copy = game.copy(board = game.board.copy(
            faces = game.board.faces.toList(), picks = game.board.picks.toList(),
            shape = BoardShape(game.board.shape.id, game.board.shape.positions.toList()),
        ))
        worker.execute {
            cached = copy
            lastWriteSucceeded = write(copy)
            val succeeded = lastWriteSucceeded
            main.post { callback(succeeded) }
        }
    }

    private fun write(game: SavedGame): Boolean {
        var stream: FileOutputStream? = null
        return try {
            val bytes = GameSaveCodec.encode(game).toByteArray(Charsets.UTF_8)
            stream = file.startWrite()
            stream.write(bytes)
            file.finishWrite(stream)
            true
        } catch (_: Exception) {
            runCatching { file.failWrite(stream) }
            false
        }
    }
}
