package com.fairytrick.fairymahjong

import org.json.JSONArray
import org.json.JSONObject

internal data class DecodedSave(
    val game: SavedGame,
    val migratedLegacySave: Boolean = false,
    val requiresRewrite: Boolean = false,
)

/** Save geometry itself, so changing a style or generator never changes an existing deal. */
internal object GameSaveCodec {
    fun encode(game: SavedGame): String {
        val positions = JSONArray()
        game.board.shape.positions.forEach { position ->
            positions.put(JSONArray(listOf(position.x2, position.y2, position.layer)))
        }
        return JSONObject()
            .put("version", 5)
            .put("layout", game.board.shape.id)
            .put("positions", positions)
            .put("faces", JSONArray(game.board.faces))
            .put("picks", JSONArray(game.board.picks))
            .put("haptics", game.hapticsEnabled)
            .put("orientation", game.orientation.savedValue)
            .toString()
    }

    fun decode(text: String): DecodedSave {
        val json = JSONObject(text)
        return when (val version = json.getInt("version")) {
            1 -> migrateLegacySave(json)
            2, 3, 4, 5 -> {
                val layoutId = json.getString("layout")
                val shape = if (version < 4) {
                    require(layoutId == GardenLayout.ID) { "Unsupported saved layout" }
                    BoardStyles.garden
                } else {
                    // Custom and procedural IDs need not exist in the current style catalog.
                    require(layoutId.matches(Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,79}"))) {
                        "Invalid saved layout ID"
                    }
                    val coordinates = json.getJSONArray("positions")
                    require(coordinates.length() in 2..256) { "Invalid saved board size" }
                    BoardShape(layoutId, List(coordinates.length()) { index ->
                        val position = coordinates.getJSONArray(index)
                        require(position.length() == 3) { "Invalid saved tile position" }
                        TilePosition(position.strictInt(0), position.strictInt(1), position.strictInt(2))
                    })
                }
                val board = GameSnapshot(
                    faces = json.getJSONArray("faces").intList(strict = version >= 4),
                    picks = json.getJSONArray("picks").intList(strict = version >= 4),
                    shape = shape,
                )
                val orientation = if (version >= 5) {
                    val value = json.get("orientation")
                    require(value is String) { "Saved orientation must be a string" }
                    BoardOrientation.fromSavedValue(value)
                } else {
                    BoardOrientation.PORTRAIT
                }
                // Replay validates every pick against the saved geometry and hand capacity.
                DecodedSave(
                    game = SavedGame(
                        MahjongGame(board).snapshot(), json.getBoolean("haptics"), orientation,
                    ),
                    requiresRewrite = version < 5,
                )
            }
            else -> error("Unsupported save version")
        }
    }

    private fun JSONArray.intList(strict: Boolean): List<Int> =
        List(length()) { if (strict) strictInt(it) else getInt(it) }

    private fun JSONArray.strictInt(index: Int): Int {
        val value = get(index)
        require(value is Int) { "Saved tile values must be integers" }
        return value
    }

    private fun migrateLegacySave(json: JSONObject): DecodedSave {
        // Validate the original twelve-tile format before classifying it as a migration.
        // Invalid legacy data still follows the unreadable-save recovery path.
        val faces = json.getJSONArray("faces").intList(strict = false)
        val pairsJson = json.getJSONArray("matches")
        require(faces.size == 12 && (0 until 6).all { face -> faces.count { it == face } == 2 }) {
            "Invalid legacy board"
        }
        val removed = mutableSetOf<Int>()
        repeat(pairsJson.length()) { index ->
            val pair = pairsJson.getJSONArray(index)
            require(pair.length() == 2) { "Invalid legacy pair" }
            val first = pair.getInt(0)
            val second = pair.getInt(1)
            require(first in faces.indices && second in faces.indices && first != second) {
                "Invalid legacy tile"
            }
            require(faces[first] == faces[second] && removed.add(first) && removed.add(second)) {
                "Invalid legacy match"
            }
        }
        val selected = json.getInt("selected")
        require(selected == -1 || (selected in faces.indices && selected !in removed)) {
            "Invalid legacy selection"
        }
        return DecodedSave(
            game = SavedGame(MahjongGame.newGame().snapshot(), json.getBoolean("haptics")),
            migratedLegacySave = true,
            requiresRewrite = true,
        )
    }
}
