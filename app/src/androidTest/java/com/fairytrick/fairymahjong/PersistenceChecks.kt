package com.fairytrick.fairymahjong

import java.io.File
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
        )

        try {
            checks.test("save codec preserves custom geometry, matched pair, held tile and orientation") {
                val decoded = GameSaveCodec.decode(GameSaveCodec.encode(original))
                check(decoded.game == original)
                check(!decoded.migratedLegacySave && !decoded.requiresRewrite)
                val restored = MahjongGame(decoded.game.board)
                check(restored.hand == listOf(1) && restored.matchedPairCount == 1)
            }

            checks.test("legacy garden save versions preserve accepted picks through current rewrite") {
                val deal = MahjongGame.generateDeal(502L, BoardStyles.garden)
                for (version in 2..4) {
                    val source = SavedGame(deal.snapshot.copy(picks = deal.solution.take(5)), false)
                    val json = JSONObject(GameSaveCodec.encode(source)).put("version", version)
                    json.remove("orientation")
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
