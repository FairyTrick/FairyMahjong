package com.fairytrick.fairymahjong

import android.util.JsonReader
import android.util.JsonToken
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.StringReader
import org.json.JSONArray
import org.json.JSONObject

internal data class DecodedSave(
    val game: SavedGame,
    val migratedLegacySave: Boolean = false,
    val requiresRewrite: Boolean = false,
)

/** Save geometry itself, so changing a style or generator never changes an existing deal. */
internal object GameSaveCodec {
    // A maximum-size board, full pick history and metadata fit comfortably below this.
    const val MAX_SAVE_BYTES = 64 * 1024
    private const val MAX_JSON_DEPTH = 8

    fun readBytes(input: InputStream): ByteArray {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(4096)
        while (true) {
            val remaining = MAX_SAVE_BYTES - output.size()
            val count = input.read(buffer, 0, minOf(buffer.size, remaining + 1))
            if (count < 0) return output.toByteArray()
            require(count <= remaining) { "Saved game is too large" }
            output.write(buffer, 0, count)
        }
    }

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
        validateStructure(text)
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
                val faces = json.getJSONArray("faces")
                val picks = json.getJSONArray("picks")
                require(faces.length() == shape.positions.size) { "Invalid saved face count" }
                require(picks.length() <= shape.positions.size) { "Invalid saved pick count" }
                val board = GameSnapshot(
                    faces = faces.intList(strict = version >= 4),
                    picks = picks.intList(strict = version >= 4),
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

    /** Bound nesting before JSONObject's recursive parser sees even an unknown field. */
    private fun validateStructure(text: String) {
        require(text.length <= MAX_SAVE_BYTES) { "Saved game is too large" }
        JsonReader(StringReader(text)).use { reader ->
            // Older Android JSON accepts comments and unquoted values; retain that tolerance.
            reader.isLenient = true
            require(reader.peek() == JsonToken.BEGIN_OBJECT) { "Saved game must be an object" }
            var depth = 0
            do {
                when (reader.peek()) {
                    JsonToken.BEGIN_OBJECT -> {
                        require(depth < MAX_JSON_DEPTH) { "Saved game is nested too deeply" }
                        reader.beginObject()
                        depth++
                    }
                    JsonToken.BEGIN_ARRAY -> {
                        require(depth < MAX_JSON_DEPTH) { "Saved game is nested too deeply" }
                        reader.beginArray()
                        depth++
                    }
                    JsonToken.END_OBJECT -> { reader.endObject(); depth-- }
                    JsonToken.END_ARRAY -> { reader.endArray(); depth-- }
                    JsonToken.NAME -> reader.nextName()
                    JsonToken.STRING, JsonToken.NUMBER -> reader.nextString()
                    JsonToken.BOOLEAN -> reader.nextBoolean()
                    JsonToken.NULL -> reader.nextNull()
                    JsonToken.END_DOCUMENT -> error("Incomplete saved game")
                }
            } while (depth > 0)
            require(reader.peek() == JsonToken.END_DOCUMENT) { "Unexpected data after saved game" }
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
        val facesJson = json.getJSONArray("faces")
        val pairsJson = json.getJSONArray("matches")
        require(facesJson.length() == 12 && pairsJson.length() <= 6) { "Invalid legacy board size" }
        val faces = facesJson.intList(strict = false)
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
