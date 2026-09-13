package com.fairytrick.fairymahjong

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import android.os.Looper
import android.os.SystemClock
import java.util.concurrent.atomic.AtomicReference

/** Framework-only checks; the shipped app has no test runner or test dependencies. */
class ReliabilityInstrumentation : Instrumentation() {
    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        start()
    }

    override fun onStart() {
        val checks = NativeChecks(this)
        try {
            PersistenceChecks.run(checks)
            RenderingChecks.run(checks)
        } catch (failure: Throwable) {
            checks.test("test suite setup") { throw failure }
        }
        finish(if (checks.failures == 0) Activity.RESULT_OK else Activity.RESULT_CANCELED,
            Bundle().apply {
                putString("reliability", if (checks.failures == 0) "passed" else "failed")
                putInt("checks", checks.count)
                putInt("failures", checks.failures)
                putString("stream", "\n${checks.count} native checks, ${checks.failures} failures\n")
            })
    }
}

class NativeChecks(val instrumentation: Instrumentation) {
    var count = 0
        private set
    var failures = 0
        private set

    fun test(name: String, body: () -> Unit) {
        count++
        val status = Bundle().apply {
            putString("class", "NativeReliability")
            putString("test", name)
            putInt("current", count)
        }
        instrumentation.sendStatus(1, status)
        try {
            body()
            status.putString("stream", "PASS: $name\n")
            instrumentation.sendStatus(0, status)
        } catch (failure: Throwable) {
            failures++
            status.putString("stack", failure.stackTraceToString())
            status.putString("stream", "FAIL: $name: $failure\n")
            instrumentation.sendStatus(-2, status)
        }
    }

    fun <T> onMain(block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        val result = AtomicReference<Result<T>>()
        instrumentation.runOnMainSync { result.set(runCatching(block)) }
        return result.get().getOrThrow()
    }

    fun await(label: String, timeoutMs: Long = 10_000, condition: () -> Boolean) {
        check(Looper.myLooper() != Looper.getMainLooper()) { "Never wait on the UI thread" }
        val deadline = SystemClock.uptimeMillis() + timeoutMs
        while (!condition()) {
            check(SystemClock.uptimeMillis() < deadline) { "Timed out: $label" }
            SystemClock.sleep(25)
        }
    }
}
