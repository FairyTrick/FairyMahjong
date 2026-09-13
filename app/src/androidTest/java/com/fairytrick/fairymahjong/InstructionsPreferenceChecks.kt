package com.fairytrick.fairymahjong

import android.content.Context

internal object InstructionsPreferenceChecks {
    fun run(checks: NativeChecks) {
        checks.test("Mistyped onboarding preference cannot prevent opening the guide") {
            val context = checks.instrumentation.targetContext
            val name = "instructions-preference-check-${System.nanoTime()}"
            val preferences = context.getSharedPreferences(name, Context.MODE_PRIVATE)
            try {
                check(!hasSeenInstructions(preferences))
                val invalidValues = listOf<(android.content.SharedPreferences.Editor) -> Unit>(
                    { it.putString("seen", "true") },
                    { it.putInt("seen", 1) },
                    { it.putLong("seen", 1L) },
                    { it.putFloat("seen", 1f) },
                    { it.putStringSet("seen", setOf("true")) },
                )
                for (writeInvalid in invalidValues) {
                    val editor = preferences.edit().clear()
                    writeInvalid(editor)
                    check(editor.commit())
                    // SharedPreferences.getBoolean would throw for these valid XML values.
                    check(!hasSeenInstructions(preferences))
                }
                check(preferences.edit().putBoolean("seen", false).commit())
                check(!hasSeenInstructions(preferences))
                // Closing the guide uses the existing Boolean write to repair the optional value.
                check(preferences.edit().putBoolean("seen", true).commit())
                check(hasSeenInstructions(preferences))
                check(preferences.getBoolean("seen", false))
            } finally {
                check(context.deleteSharedPreferences(name))
            }
        }
    }
}
