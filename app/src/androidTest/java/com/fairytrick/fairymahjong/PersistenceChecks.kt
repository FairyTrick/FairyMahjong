package com.fairytrick.fairymahjong

import java.io.File
import java.io.ByteArrayInputStream
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicReference
import org.json.JSONArray
import org.json.JSONObject

/** Real Android JSON, AtomicFile, worker callbacks and solver stack, using only private fixtures. */
internal object PersistenceChecks {
    fun run(checks: NativeChecks) {
        val root = File(checks.instrumentation.targetContext.cacheDir, "persistence-checks-${System.nanoTime()}")
        check(root.mkdirs())
        var fixtureIndex = 0
        fun directory(): File = File(root, "case-${fixtureIndex++}").apply { check(mkdir()) }
        fun saveFile(directory: File) = File(directory, "practice-board.json")
        fun load(store: GameStore): LoadedGame {
            val result = AtomicReference<LoadedGame>()
            checks.onMain { store.load { result.set(it) } }
            checks.await("saved board callback") { result.get() != null }
            return checkNotNull(result.get())
        }
        val square = BoardShape("retired-custom-save", listOf(
            TilePosition(0, 0, 0), TilePosition(2, 0, 0),
            TilePosition(0, 2, 0), TilePosition(2, 2, 0),
        ))
        val original = SavedGame(
            GameSnapshot(listOf(6, 7, 6, 7), listOf(0, 1, 2), square),
            hapticsEnabled = false,
            orientation = BoardOrientation.LANDSCAPE,
            difficulty = GameDifficulty.HARD,
        )
        fun rejectsWithoutVmError(block: () -> Unit) {
            val failure = runCatching(block).exceptionOrNull()
            check(failure is Exception) { "Expected ordinary input rejection, got $failure" }
        }

        try {
            checks.test("save codec preserves custom geometry, matched pair, held tile and orientation") {
                val decoded = GameSaveCodec.decode(GameSaveCodec.encode(original))
                check(decoded.game == original)
                check(!decoded.migratedLegacySave && !decoded.requiresRewrite)
                val restored = MahjongGame(decoded.game.board)
                check(restored.hand == listOf(1) && restored.matchedPairCount == 1)
            }

            checks.test("all difficulty settings survive save and reload in either orientation") {
                for (difficulty in GameDifficulty.values()) {
                    for (orientation in BoardOrientation.values()) {
                        val source = original.copy(difficulty = difficulty, orientation = orientation)
                        val directory = directory()
                        saveFile(directory).writeText(GameSaveCodec.encode(source))
                        val restored = load(checks.onMain { GameStore(directory) })
                        check(restored.game == source)
                        check(!restored.recoveredUnreadableSave && !restored.migratedLegacySave)
                    }
                }
            }

            checks.test("version five saves without difficulty retain their board and default to normal") {
                val directory = directory()
                val older = JSONObject(GameSaveCodec.encode(original)).apply { remove("difficulty") }
                check(older.getInt("version") == 5)
                saveFile(directory).writeText(older.toString())
                val expected = original.copy(difficulty = GameDifficulty.NORMAL)
                val restored = load(checks.onMain { GameStore(directory) })
                check(restored.game == expected && restored.saveAvailable)
                check(!restored.recoveredUnreadableSave && !restored.migratedLegacySave)
                val rewritten = JSONObject(saveFile(directory).readText())
                check(rewritten.getString("difficulty") == "NORMAL")
                check(GameSaveCodec.decode(rewritten.toString()).let { it.game == expected && !it.requiresRewrite })
            }

            checks.test("invalid optional difficulty falls back without losing saved progress") {
                for (value in listOf(JSONObject.NULL, "UNKNOWN", 1, true, JSONArray(), JSONObject())) {
                    val encoded = JSONObject(GameSaveCodec.encode(original)).put("difficulty", value)
                    val decoded = GameSaveCodec.decode(encoded.toString())
                    check(decoded.game == original.copy(difficulty = GameDifficulty.NORMAL))
                    check(decoded.requiresRewrite && !decoded.migratedLegacySave)
                }
            }

            checks.test("legacy garden save versions preserve accepted picks through current rewrite") {
                val deal = MahjongGame.generateDeal(502L, BoardStyles.garden)
                for (version in 2..4) {
                    val source = SavedGame(deal.snapshot.copy(picks = deal.solution.take(5)), false)
                    val json = JSONObject(GameSaveCodec.encode(source)).put("version", version)
                    json.remove("orientation")
                    json.remove("difficulty")
                    if (version < 4) json.remove("positions")
                    val decoded = GameSaveCodec.decode(json.toString())
                    check(decoded.game == source)
                    check(decoded.requiresRewrite && !decoded.migratedLegacySave)
                }
                val legacy = JSONObject()
                    .put("version", 1)
                    .put("faces", JSONArray((0..5).flatMap { listOf(it, it) }))
                    .put("matches", JSONArray().put(JSONArray(listOf(0, 1))))
                    .put("selected", 2)
                    .put("haptics", false)
                val migrated = GameSaveCodec.decode(legacy.toString())
                check(migrated.migratedLegacySave && migrated.requiresRewrite)
                check(!migrated.game.hapticsEnabled && migrated.game.board.picks.isEmpty())
                check(MahjongGame(migrated.game.board).boardTileCount > 12)
            }

            checks.test("completed and full-hand saves replay without reopening finished games") {
                val complete = original.copy(board = original.board.copy(picks = listOf(0, 1, 2, 3)))
                check(MahjongGame(GameSaveCodec.decode(GameSaveCodec.encode(complete)).game.board).isComplete)
                val full = original.copy(board = GameSnapshot(
                    listOf(0, 1, 2, 3, 0, 1, 2, 3), listOf(0, 1, 2, 3),
                    BoardShape("full-hand", List(8) { TilePosition((it % 4) * 2, (it / 4) * 2, 0) }),
                ))
                val restored = MahjongGame(GameSaveCodec.decode(GameSaveCodec.encode(full)).game.board)
                check(restored.hand.size == 4 && restored.isGameOver)
                check(restored.positions.indices.none(restored::isPickable))
            }

            checks.test("malformed current saves reject invalid geometry, identities and pick histories") {
                fun rejects(change: (JSONObject) -> Unit) {
                    val json = JSONObject(GameSaveCodec.encode(original)).also(change)
                    check(runCatching { GameSaveCodec.decode(json.toString()) }.isFailure)
                }
                rejects { it.put("version", 1000) }
                rejects { it.put("orientation", "sensor") }
                rejects { it.put("orientation", 1) }
                rejects { it.put("layout", "../another-file") }
                rejects { it.put("faces", JSONArray(listOf(-1, 7, -1, 7))) }
                rejects { it.put("faces", JSONArray(listOf(TileCatalog.FACE_COUNT, 7, TileCatalog.FACE_COUNT, 7))) }
                rejects { it.put("faces", JSONArray(listOf(6.5, 7, 6.5, 7))) }
                rejects { it.put("faces", JSONArray(listOf(6, 7, 6, 6))) }
                rejects { it.put("picks", JSONArray(listOf(0, 0))) }
                rejects { it.put("picks", JSONArray(listOf(-1))) }
                rejects { it.put("picks", JSONArray(listOf(4))) }
                rejects { it.getJSONArray("positions").getJSONArray(0).put(0, .5) }
                rejects { it.getJSONArray("positions").put(1, it.getJSONArray("positions").getJSONArray(0)) }
                rejects { it.getJSONArray("positions").getJSONArray(0).put(2, 4) }
                check(runCatching { GameSaveCodec.decode("{\"version\":5,\"faces\":[") }.isFailure)
            }

            checks.test("save parser bounds unknown nesting and rejects oversized input before recursion") {
                val encoded = GameSaveCodec.encode(original)
                val nested = encoded.dropLast(1) + ",\"extra\":" +
                    "[".repeat(8192) + "0" + "]".repeat(8192) + "}"
                check(nested.length < GameSaveCodec.MAX_SAVE_BYTES)
                rejectsWithoutVmError { GameSaveCodec.decode(nested) }
                rejectsWithoutVmError { GameSaveCodec.decode(encoded + " ".repeat(GameSaveCodec.MAX_SAVE_BYTES)) }
                rejectsWithoutVmError { GameSaveCodec.decode(encoded + "{}") }
                // Brackets and escaped quotes within strings are data, not nesting.
                val strings = JSONObject(encoded).put("extra", "[\\\"".repeat(2048)).toString()
                check(GameSaveCodec.decode(strings).game == original)
                for (key in listOf("faces", "picks")) {
                    val tooMany = JSONObject(encoded).put(key, JSONArray(List(257) { 0 }))
                    rejectsWithoutVmError { GameSaveCodec.decode(tooMany.toString()) }
                }
            }

            checks.test("bounded save read consumes at most one byte beyond the limit") {
                val source = ByteArrayInputStream(ByteArray(GameSaveCodec.MAX_SAVE_BYTES + 4096) { 32 })
                rejectsWithoutVmError { GameSaveCodec.readBytes(source) }
                check(source.available() == 4095)
                val encoded = GameSaveCodec.encode(original)
                val atLimit = encoded.padEnd(GameSaveCodec.MAX_SAVE_BYTES).toByteArray(Charsets.UTF_8)
                check(atLimit.size == GameSaveCodec.MAX_SAVE_BYTES)
                val accepted = GameSaveCodec.readBytes(ByteArrayInputStream(atLimit))
                check(accepted.contentEquals(atLimit))
                check(GameSaveCodec.decode(accepted.toString(Charsets.UTF_8)).game == original)
            }

            checks.test("maximum save geometry and full history remain below the input limit") {
                val shape = BoardShape("m".repeat(80), buildList {
                    for (layer in 0..3) for (y in 0..7) for (x in 0..7) {
                        add(TilePosition(Int.MAX_VALUE - 14 + x * 2, Int.MIN_VALUE + y * 2, layer))
                    }
                })
                val remaining = BooleanArray(256) { true }
                val order = List(256) {
                    remaining.indices.first { shape.geometry.isFree(it, remaining) }.also { remaining[it] = false }
                }
                val maximum = SavedGame(GameSnapshot(List(256) { 0 }, order, shape), false, BoardOrientation.LANDSCAPE)
                val encoded = GameSaveCodec.encode(maximum)
                check(encoded.toByteArray(Charsets.UTF_8).size < GameSaveCodec.MAX_SAVE_BYTES)
                check(GameSaveCodec.decode(encoded).game == maximum)
            }

            checks.test("application writer orders rapid saves and reloads on the main looper") {
                val directory = directory()
                val store = checks.onMain { GameStore(directory) }
                val events = CopyOnWriteArrayList<String>()
                val last = original.copy(board = original.board.copy(picks = listOf(0, 1, 2, 3)))
                val loaded = AtomicReference<LoadedGame>()
                val callbackOnMain = CopyOnWriteArrayList<Boolean>()
                checks.onMain {
                    listOf(original, last).forEachIndexed { index, game ->
                        store.save(game) { succeeded ->
                            callbackOnMain.add(android.os.Looper.myLooper() == android.os.Looper.getMainLooper())
                            events.add("save-$index-$succeeded")
                        }
                    }
                    store.load {
                        callbackOnMain.add(android.os.Looper.myLooper() == android.os.Looper.getMainLooper())
                        loaded.set(it)
                        events.add("load")
                    }
                }
                checks.await("ordered save and load callbacks") { events.size == 3 }
                check(events == listOf("save-0-true", "save-1-true", "load"))
                check(callbackOnMain.all { it })
                check(loaded.get().game == last)
                check(GameSaveCodec.decode(saveFile(directory).readText()).game == last)
                check(load(checks.onMain { GameStore(directory) }).game == last)
            }

            checks.test("queued save owns a snapshot rather than caller-owned mutable lists") {
                val directory = directory()
                val faces = original.board.faces.toMutableList()
                val picks = original.board.picks.toMutableList()
                val store = checks.onMain { GameStore(directory) }
                val saved = AtomicReference<Boolean>()
                checks.onMain {
                    store.save(original.copy(board = original.board.copy(faces = faces, picks = picks))) { saved.set(it) }
                    faces[0] = -1
                    picks.clear()
                }
                checks.await("immutable save") { saved.get() != null }
                check(saved.get())
                check(load(store).game == original)
            }

            checks.test("AtomicFile backup preserves committed progress after interrupted replacement") {
                for (basePresent in listOf(true, false)) {
                    val directory = directory()
                    if (basePresent) saveFile(directory).writeText("interrupted write")
                    File(directory, "practice-board.json.bak").writeText(GameSaveCodec.encode(original))
                    val restored = load(checks.onMain { GameStore(directory) })
                    check(restored.game == original && !restored.recoveredUnreadableSave && restored.saveAvailable)
                    check(GameSaveCodec.decode(saveFile(directory).readText()).game == original)
                }
            }

            checks.test("uncommitted AtomicFile new data cannot replace the last committed board") {
                val directory = directory()
                saveFile(directory).writeText(GameSaveCodec.encode(original))
                File(directory, "practice-board.json.new").writeText("partial newer write")
                val restored = load(checks.onMain { GameStore(directory) })
                check(restored.game == original && !restored.recoveredUnreadableSave)
            }

            checks.test("unreadable save recovery produces a valid durable game") {
                val directory = directory()
                saveFile(directory).writeText("{\"version\":5,\"positions\":[")
                val loaded = load(checks.onMain { GameStore(directory) })
                check(loaded.recoveredUnreadableSave && loaded.saveAvailable)
                check(loaded.game.board.picks.isEmpty())
                check(MahjongGame(loaded.game.board).boardTileCount > 12)
                val restarted = load(checks.onMain { GameStore(directory) })
                check(restarted.game == loaded.game && !restarted.recoveredUnreadableSave)
            }

            checks.test("oversized and deeply nested base or backup saves recover without killing the writer") {
                val nested = "{\"extra\":" + "[".repeat(8192) + "0" + "]".repeat(8192) + "}"
                for (backup in listOf(false, true)) {
                    for (malformed in listOf(nested, " ".repeat(GameSaveCodec.MAX_SAVE_BYTES + 1))) {
                        val directory = directory()
                        val input = if (backup) File(directory, "practice-board.json.bak") else saveFile(directory)
                        input.writeText(malformed)
                        val store = checks.onMain { GameStore(directory) }
                        val loaded = load(store)
                        check(loaded.recoveredUnreadableSave && loaded.saveAvailable)
                        check(MahjongGame(loaded.game.board).boardTileCount > 12)
                        check(load(checks.onMain { GameStore(directory) }).game == loaded.game)
                        val saved = AtomicReference<Boolean>()
                        checks.onMain { store.save(original) { saved.set(it) } }
                        checks.await("save after oversized input recovery") { saved.get() != null }
                        check(saved.get())
                        check(load(checks.onMain { GameStore(directory) }).game == original)
                    }
                }
            }

            checks.test("failed storage retains live progress and reports failure instead of crashing") {
                val directory = File(directory(), "blocked-directory").apply { writeText("not a directory") }
                val store = checks.onMain { GameStore(directory) }
                val saved = AtomicReference<Boolean>()
                checks.onMain { store.save(original) { saved.set(it) } }
                checks.await("failed save callback") { saved.get() != null }
                check(!saved.get())
                val loaded = load(store)
                check(loaded.game == original && !loaded.saveAvailable)
            }

            checks.test("failed AtomicFile replacement cannot report a successful save") {
                val directory = directory()
                val blockedDestination = saveFile(directory).apply { check(mkdir()) }
                File(blockedDestination, "keep").writeText("prevents directory replacement")
                val store = checks.onMain { GameStore(directory) }
                val saved = AtomicReference<Boolean>()
                checks.onMain { store.save(original) { saved.set(it) } }
                checks.await("failed commit callback") { saved.get() != null }
                check(!saved.get()) { "A write without a committed save reported success" }
                check(load(store).let { it.game == original && !it.saveAvailable })
                val restarted = load(checks.onMain { GameStore(directory) })
                check(!restarted.saveAvailable && restarted.game != original)
            }

            checks.test("Android solver stack supports the maximum saved geometry and cancellation retry") {
                val shape = BoardShape("maximum-native-hint", buildList {
                    for (layer in 0..3) for (y in 0..7) for (x in 0..7) add(TilePosition(x * 2, y * 2, layer))
                })
                val remaining = BooleanArray(shape.positions.size) { true }
                val order = List(remaining.size) {
                    remaining.indices.first { shape.geometry.isFree(it, remaining) }.also { remaining[it] = false }
                }
                val faces = MutableList(order.size) { 0 }
                order.forEachIndexed { offset, tile -> faces[tile] = offset / 2 }
                val snapshot = GameSnapshot(faces, shape = shape)
                val engine = HintEngine()
                check(engine.findHint(snapshot, maxNodes = 0) == HintResult.SearchLimit)
                var checksSeen = 0
                check(engine.findHint(snapshot) { ++checksSeen > 20 } == HintResult.Cancelled)
                val found = engine.findHint(snapshot) as? HintResult.Found
                    ?: error("No winning continuation after cancellation")
                check(found.hint.solution.size == 256 && found.hint.solution.toSet().size == 256)
                // The engine supports identities beyond the artwork catalog; replay with relabeled pairs.
                val replay = MahjongGame(snapshot.copy(faces = faces.map { it % TileCatalog.FACE_COUNT }))
                found.hint.solution.forEach { check(replay.pickTile(it) != PickResult.IGNORED) }
                check(replay.isComplete)
            }
        } finally {
            check(root.deleteRecursively())
        }
    }
}
