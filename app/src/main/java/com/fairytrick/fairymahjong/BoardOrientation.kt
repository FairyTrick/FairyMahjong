package com.fairytrick.fairymahjong

/** Player-selected board mode; device sensors never choose or change this value. */
enum class BoardOrientation(val savedValue: String) {
    PORTRAIT("portrait"),
    LANDSCAPE("landscape");

    fun toggled(): BoardOrientation = when (this) {
        PORTRAIT -> LANDSCAPE
        LANDSCAPE -> PORTRAIT
    }

    companion object {
        /** Do not silently reinterpret a malformed save as a different board mode. */
        fun fromSavedValue(value: String): BoardOrientation =
            entries.firstOrNull { it.savedValue == value }
                ?: throw IllegalArgumentException("Invalid saved orientation")
    }
}
