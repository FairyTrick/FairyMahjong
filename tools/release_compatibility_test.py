#!/usr/bin/env python3
"""Exercise a signed, minified APK on a disposable Fairy_Test_Compat_* AVD."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

from test_device_guard import require_test_emulator

PACKAGE = "com.fairytrick.fairymahjong"
TILE = re.compile(r"(.+) tile, (\d+) of (\d+), (available|blocked|game over)")
NEXT_DIFFICULTY = {"Easy": "Normal", "Normal": "Hard", "Hard": "Easy"}


def require(value, message):
    if not value:
        raise AssertionError(message)


class Device:
    def __init__(self, args):
        self.prefix = [args.adb, "-s", args.serial]
        self.output = args.output
        self.output.mkdir(parents=True, exist_ok=True)
        self.name = require_test_emulator(self.prefix)
        require(self.name.startswith("Fairy_Test_Compat_"),
                "This destructive test requires a disposable Fairy_Test_Compat_* AVD")

    def command(self, *args, check=True, timeout=40):
        result = subprocess.run(self.prefix + list(args), capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout).decode(errors="replace"))
        return result

    def adb(self, *args, **kwargs):
        return self.command(*args, **kwargs).stdout.decode(errors="replace").strip()

    def launch(self):
        result = self.adb("shell", "am", "start", "-W", "-n", PACKAGE + "/.MainActivity")
        require("Status: ok" in result, "Activity launch failed: " + result)

    def ui_markup(self, timeout=40):
        remote = "/data/local/tmp/fairy-compat-ui.xml"
        self.adb("shell", "rm", "-f", remote, timeout=timeout)
        self.adb("shell", "uiautomator", "dump", remote, timeout=timeout)
        return self.adb("shell", "cat", remote, timeout=timeout)

    def ui(self):
        markup = self.ui_markup()
        nodes = list(ET.fromstring(markup).iter("node"))
        # First full-screen launch can have a platform-owned acknowledgement.
        lesson = [n for n in nodes if n.get("resource-id") in
                  ("com.android.systemui:id/ok", "android:id/ok")
                  and n.get("text", "").casefold() == "got it"]
        if lesson:
            self.tap(lesson[0])
            raise AssertionError("Waiting for Android's full-screen lesson to close")
        require(any(n.get("package") == PACKAGE for n in nodes), "Game window is not visible")
        return nodes

    def wait(self, operation, label, timeout=40):
        deadline, error = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            try:
                return operation()
            except (AssertionError, RuntimeError, ET.ParseError) as problem:
                error = problem
                time.sleep(.15)
        raise AssertionError(f"Timed out: {label}: {error}")

    def tap(self, node):
        require(node.get("enabled") == "true", "Control is disabled")
        bounds = list(map(int, re.findall(r"-?\d+", node.get("bounds", ""))))
        require(len(bounds) == 4 and bounds[2] > bounds[0] and bounds[3] > bounds[1],
                "Control has no visible touch area")
        self.adb("shell", "input", "tap", str((bounds[0] + bounds[2]) // 2),
                 str((bounds[1] + bounds[3]) // 2))

    def button(self, nodes, label):
        matches = [n for n in nodes if n.get("content-desc") == label and n.get("clickable") == "true"]
        require(len(matches) == 1, "Missing unique control: " + label)
        require(matches[0].get("enabled") == "true", "Disabled control: " + label)
        return matches[0]

    def difficulty(self, nodes, expected=None):
        matches = [n for n in nodes if n.get("resource-id") == PACKAGE + ":id/difficulty_button"]
        require(len(matches) == 1, "Missing unique difficulty control")
        node = matches[0]
        require(node.get("enabled") == "true" and node.get("clickable") == "true" and
                node.get("class") == "android.widget.Button", "Difficulty action is unavailable")
        description = node.get("content-desc", "")
        modes = [mode for mode, next_mode in NEXT_DIFFICULTY.items() if description ==
                 f"Difficulty: {mode}. Switch to {next_mode} and start a new board"]
        require(len(modes) == 1, "Difficulty action has an incorrect state or next action: " + description)
        require(expected is None or modes[0] == expected, "Expected difficulty " + str(expected))
        return node

    def guide(self):
        nodes = self.ui()
        ids = {n.get("resource-id") for n in nodes}
        for name in ("instructions_screen", "instructions_pick_panel", "instructions_pairs_panel"):
            require(PACKAGE + ":id/" + name in ids, "Missing guide panel: " + name)
        self.button(nodes, "Back to game")
        return nodes

    def require_game_focus(self):
        windows = self.adb("shell", "dumpsys", "window")
        focused = re.findall(r"^\s*mCurrentFocus=(.*)$", windows, flags=re.MULTILINE)
        require(any(PACKAGE + "/" in window for window in focused),
                "Game window does not have input focus: " + "; ".join(focused))

    def settled_guide(self):
        # Read the UI first so the platform's first-use lesson can still be
        # dismissed before checking which window receives game input.
        first = self.guide()
        self.require_game_focus()
        second = self.guide()
        self.require_game_focus()

        def bounds(nodes):
            return sorted((node.get("resource-id", ""), node.get("content-desc", ""),
                           node.get("bounds", "")) for node in nodes
                          if node.get("clickable") == "true" or
                          node.get("resource-id", "").startswith(PACKAGE + ":id/instructions_"))

        require(bounds(first) == bounds(second), "Guide layout is still changing")
        return second

    def game(self):
        nodes = self.ui()
        require(not any(n.get("resource-id") == PACKAGE + ":id/instructions_screen" for n in nodes),
                "Guide still open")
        require(any(n.get("content-desc", "").startswith("Board status: playing") for n in nodes),
                "Board is not playable")
        tiles = {int(m[2]): n for n in nodes if (m := TILE.match(n.get("content-desc", "")))}
        require(tiles, "No board tiles visible")
        require(all(int(TILE.match(n.get("content-desc"))[3]) >= 48 for n in tiles.values()),
                "Generated board is unexpectedly tiny")
        for label in ("Hint", "How to play", "New board"):
            self.button(nodes, label)
        self.rotation(nodes)
        self.difficulty(nodes)
        return nodes, tiles

    def fingerprint(self, game):
        nodes, tiles = game
        return {
            "tiles": sorted((index, node.get("content-desc").split(". Hint:")[0]) for index, node in tiles.items()),
            "hand": sorted(n.get("content-desc").split(", Hint:")[0] for n in nodes
                           if n.get("content-desc", "").startswith("Hand slot ")),
            "difficulty": self.difficulty(nodes).get("content-desc"),
        }

    def settled_game(self):
        first = self.game()
        self.require_game_focus()
        second = self.game()
        self.require_game_focus()
        def bounds(state):
            return sorted((n.get("content-desc", ""), n.get("bounds", ""))
                          for n in state[0] if n.get("clickable") == "true")
        require(self.fingerprint(first) == self.fingerprint(second) and bounds(first) == bounds(second),
                "Board layout is still changing")
        return second

    def rotation(self, nodes):
        matches = [n for n in nodes if n.get("content-desc", "").startswith("Switch to ")
                   and n.get("clickable") == "true"]
        require(len(matches) == 1 and matches[0].get("enabled") == "true", "Rotation control is stuck")
        return matches[0]

    def capture(self, name):
        image = self.command("exec-out", "screencap", "-p").stdout
        require(image.startswith(b"\x89PNG\r\n\x1a\n"), "Screenshot failed")
        (self.output / (name + ".png")).write_bytes(image)

    def capture_failure(self, crash_since):
        # Inspect the failed configuration before main's finally resets it or
        # force-stops the process. One unavailable source must not hide the others.
        errors = []

        def collect(name, action):
            try:
                value = action()
                if value is not None:
                    (self.output / name).write_text(value, encoding="utf-8")
                return value or ""
            except Exception as failure:
                errors.append(f"{name}: {failure}")
                return ""

        collect("failure.png", lambda: self.capture("failure"))
        markup = collect("failure-ui.xml", lambda: self.ui_markup(timeout=15))
        windows = collect("failure-windows.txt", lambda: self.adb(
            "shell", "dumpsys", "window", timeout=15))
        activities = collect("failure-activities.txt", lambda: self.adb(
            "shell", "dumpsys", "activity", "activities", timeout=15))
        exits = collect("failure-exit-info.txt", lambda: self.adb(
            "shell", "dumpsys", "activity", "exit-info", PACKAGE, check=False, timeout=15))
        events = collect("failure-events.txt", lambda: self.adb(
            "logcat", "-b", "events", "-d", "-T", crash_since, timeout=15))
        crashes = collect("failure-crashes.txt", lambda: self.adb(
            "logcat", "-b", "crash", "-d", "-T", crash_since, timeout=15))

        # CI console output survives even if artifacts cannot be uploaded.
        print("Failure state before restoring device settings:", flush=True)
        window_fields = re.compile(r"mCurrentFocus|mFocusedApp|mTopFocusedDisplayId|"
                                   r"mAppTransitionState|mDisplayReady|mObscuringWindow")
        activity_fields = re.compile(r"mResumedActivity|topResumedActivity|mLastReportedConfiguration|"
                                     r"mCurrentConfig|mLastReportedMultiWindowMode")
        for title, source, pattern in (("Window", windows, window_fields),
                                        ("Activity", activities, activity_fields)):
            print(title + ":\n" + "\n".join(line for line in source.splitlines()
                                             if pattern.search(line)), flush=True)
        if markup:
            try:
                controls = [dict(node.attrib) for node in ET.fromstring(markup).iter("node")
                            if node.get("clickable") == "true"
                            and not TILE.match(node.get("content-desc", ""))]
                print("Visible controls: " + json.dumps(controls), flush=True)
            except ET.ParseError as failure:
                errors.append(f"failure-ui.xml parsing: {failure}")
        print("App events:\n" + "\n".join(line for line in events.splitlines() if PACKAGE in line),
              flush=True)
        print("Exit info:\n" + exits, flush=True)
        print("Crash buffer:\n" + crashes, flush=True)
        if errors:
            print("Unavailable failure diagnostics:\n" + "\n".join(errors), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace-test-app", action="store_true")
    args = parser.parse_args()
    device = Device(args)
    require(args.apk.is_file(), "APK not found")
    installed = device.adb("shell", "pm", "path", PACKAGE, check=False)
    require(not installed or args.replace_test_app, "Test app exists; explicitly allow replacing its disposable data")
    saved_settings = {
        (namespace, key): device.adb("shell", "settings", "get", namespace, key)
        for namespace, key in (("system", "font_scale"), ("system", "haptic_feedback_enabled"),
                               ("global", "animator_duration_scale"))
    }
    original_size = device.adb("shell", "wm", "size")
    original_density = device.adb("shell", "wm", "density")
    crash_since = device.adb("shell", "date", "'+%m-%d %H:%M:%S.000'")
    report = {"status": "running", "avd": device.name,
              "sdk": device.adb("shell", "getprop", "ro.build.version.sdk"),
              "abi": device.adb("shell", "getprop", "ro.product.cpu.abi"),
              "apkSha256": hashlib.sha256(args.apk.read_bytes()).hexdigest(), "checks": []}

    def record(message):
        report["checks"].append(message)
        print("PASS: " + message, flush=True)

    def stable(expected):
        result = device.game()
        require(device.fingerprint(result) == expected, "Board or hand changed unexpectedly")
        return result

    try:
        if installed:
            device.adb("uninstall", PACKAGE)
        device.adb("install", str(args.apk.resolve()), timeout=60)
        require(device.command("shell", "run-as", PACKAGE, "id", check=False).returncode != 0,
                "Must test an actual non-debuggable release build")
        device.adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        device.adb("shell", "wm", "dismiss-keyguard")
        device.launch()
        nodes = device.wait(device.settled_guide, "fresh-launch onboarding")
        device.capture("01-first-launch")
        device.tap(device.button(nodes, "Back to game"))
        state = device.wait(device.settled_game, "first playable board")
        device.difficulty(state[0], "Normal")
        record("Fresh signed release launches, renders onboarding and a substantial board")

        # Finish on Hard so the existing process-death and Activity-recreation
        # checks below verify a saved non-default difficulty with real progress.
        for mode in ("Hard", "Easy", "Normal", "Hard"):
            previous = device.fingerprint(state)
            device.tap(device.difficulty(state[0]))
            def redealt():
                result = device.settled_game()
                device.difficulty(result[0], mode)
                current = device.fingerprint(result)
                require(current["tiles"] != previous["tiles"], "Difficulty change reused the board")
                require(current["hand"] == [f"Hand slot {index}: empty" for index in range(1, 5)],
                        "Difficulty change did not clear all four hand slots")
                return result
            state = device.wait(redealt, mode + " difficulty redeal")
        record("Difficulty defaults to Normal and cycles all three states with fresh boards")
        nodes, tiles = state
        device.tap(device.button(nodes, "Hint"))
        def hinted():
            nodes, tiles = device.game()
            next_tiles = [n for n in tiles.values() if ". Hint: pick next" in n.get("content-desc", "")]
            require(len(next_tiles) == 1, "No unique next hint")
            return nodes, tiles, next_tiles[0]
        nodes, tiles, tile = device.wait(hinted, "hint search")
        picked_id = int(TILE.match(tile.get("content-desc"))[2])
        device.tap(tile)
        def picked():
            state = device.game()
            require(picked_id not in state[1], "Pick did not leave the board")
            require(any(n.get("content-desc", "").startswith("Hand slot ") and
                        not n.get("content-desc").split(", Hint:")[0].endswith(": empty") for n in state[0]),
                    "Picked tile missing from hand")
            return state
        device.wait(picked, "hinted pick")
        state = device.wait(device.settled_game, "settled board after hinted pick")
        expected = device.fingerprint(state)
        record("Hint search and native tile pick update the hand")

        device.tap(device.button(state[0], "How to play"))
        device.wait(device.settled_guide, "reopened guide")
        device.adb("shell", "input", "keyevent", "KEYCODE_BACK")
        device.wait(lambda: stable(expected), "guide preserves progress")
        record("Guide and Android Back preserve board and held tiles")

        # Real process death, rather than Activity recreation alone.
        device.adb("shell", "input", "keyevent", "KEYCODE_HOME")
        device.adb("shell", "am", "force-stop", PACKAGE)
        device.launch()
        device.wait(lambda: stable(expected), "process restart restores progress")
        record("Process death restores Hard difficulty, board, hand and dismissed onboarding")

        # The developer-setting value alone does not activate Activity destruction
        # on every Android release. am's repeat option explicitly finishes the old
        # Activity, then starts another instance without killing the app process.
        recreate_since = device.adb("shell", "date", "'+%m-%d %H:%M:%S.000'")
        process_before = device.adb("shell", "pidof", PACKAGE)
        recreated = device.adb("shell", "am", "start", "-W", "-R", "2", "-n",
                               PACKAGE + "/.MainActivity")
        require(recreated.count("Status: ok") == 2, "Activity repeat launch failed: " + recreated)
        def recreated_state():
            events = device.adb("logcat", "-b", "events", "-d", "-T", recreate_since)
            app_events = "\n".join(line for line in events.splitlines() if PACKAGE in line)
            require(re.search(r"(?:am|wm)_finish_activity:", app_events),
                    "Old Activity was not finished")
            require(re.search(r"(?:am|wm)_(?:on_create_called|create_activity):", app_events),
                    "Replacement Activity was not created")
            require(process_before and device.adb("shell", "pidof", PACKAGE) == process_before,
                    "Activity test unexpectedly restarted the whole process")
            result = device.settled_game()
            require(device.fingerprint(result) == expected, "Board or hand changed during Activity recreation")
            (args.output / "activity-recreation-events.txt").write_text(app_events, encoding="utf-8")
            return result
        state = device.wait(recreated_state, "replacement Activity restores progress")
        record("Finishing and recreating the Activity preserves Hard difficulty and progress in the same process")

        for index in range(4):
            device.tap(device.rotation(state[0]))
            state = device.wait(device.settled_game, "rotation rebuild")
            device.rotation(state[0])
            device.tap(device.button(state[0], "How to play"))
            nodes = device.wait(device.settled_guide, "rotated guide")
            device.tap(device.button(nodes, "Back to game"))
            state = device.wait(device.settled_game, "rotated board")
        record("Repeated manual rotations rebuild playable boards and both guide arrangements")

        device.adb("shell", "settings", "put", "global", "animator_duration_scale", "0")
        device.adb("shell", "settings", "put", "system", "haptic_feedback_enabled", "0")
        device.tap(device.button(state[0], "New board"))
        nodes, _ = device.wait(device.game, "disabled animations and haptics")
        device.tap(device.button(nodes, "Hint"))
        _, _, tile = device.wait(hinted, "hint without animations or haptics")
        device.tap(tile)
        device.wait(device.game, "pick without animations or haptics")
        record("Disabled system animations and haptics do not break interactions")

        for label, size, density, font in (("small-large-text", "720x1280", "360", "2.0"),
                                           ("large-window", "1600x1200", "240", "1.0")):
            device.adb("shell", "wm", "size", size)
            device.adb("shell", "wm", "density", density)
            device.adb("shell", "settings", "put", "system", "font_scale", font)
            # The saved orientation can be applied after am start returns; wait
            # for stable control bounds before using coordinates from the dump.
            device.adb("shell", "am", "force-stop", PACKAGE)
            device.launch()
            state = device.wait(device.settled_game, label + " board")
            device.tap(device.button(state[0], "How to play"))
            nodes = device.wait(device.settled_guide, label + " guide")
            device.capture(label)
            device.tap(device.button(nodes, "Back to game"))
            state = device.wait(device.settled_game, label + " guide close")
            device.tap(device.rotation(state[0]))
            state = device.wait(device.settled_game, label + " rotation")
            device.rotation(state[0])
            record(label + " renders and keeps help/rotation controls usable")

        crashes = device.adb("logcat", "-b", "crash", "-d", "-T", crash_since)
        (args.output / "crashes.txt").write_text(crashes, encoding="utf-8")
        require(PACKAGE not in crashes, "App crash appeared in native crash buffer")
        anr = device.adb("shell", "dumpsys", "activity", "lastanr")
        require(PACKAGE not in anr, "App ANR recorded")
        record("No native app crash or ANR during the suite")
        report["status"] = "passed"
    except BaseException as failure:
        report["status"] = "failed"
        report["error"] = str(failure)
        device.capture_failure(crash_since)
        raise
    finally:
        # All mutations are limited to an explicitly disposable, guarded test AVD.
        for (namespace, key), value in saved_settings.items():
            if value == "null":
                device.adb("shell", "settings", "delete", namespace, key, check=False)
            else:
                device.adb("shell", "settings", "put", namespace, key, value, check=False)
        for kind, original in (("size", original_size), ("density", original_density)):
            override = re.search(r"Override (?:size|density): (\S+)", original)
            device.adb("shell", "wm", kind, override.group(1) if override else "reset", check=False)
        device.adb("shell", "am", "force-stop", PACKAGE, check=False)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
