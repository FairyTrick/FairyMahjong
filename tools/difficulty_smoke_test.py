#!/usr/bin/env python3
"""Verify difficulty changes and saved progress on a dedicated debug test AVD.

Uses real toolbar taps in both orientations. Restores original save bytes,
AtomicFile sidecars, and instruction preferences in finally. Screenshots need
visual review. Does not alter device display or rotation settings.
"""

import argparse
import json
from pathlib import Path
import re
import struct
import subprocess
import time
import xml.etree.ElementTree as ET

from test_device_guard import require_test_emulator
from smoke_fixtures import TILE
from save_migration_smoke_test import SAVE_PATHS, free_tiles, replay, require


PRIVATE_PATHS = SAVE_PATHS + ("shared_prefs/instructions.xml", "shared_prefs/instructions.xml.bak")
NEXT = {"NORMAL": "HARD", "HARD": "EASY", "EASY": "NORMAL"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", type=Path, default=Path("artifacts/difficulty"))
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package name")
    prefix = [args.adb, "-s", args.serial]
    avd = require_test_emulator(prefix)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "serial": args.serial, "avd": avd, "checks": [], "screenshots": [],
              "original_state_restored": False, "screenshots_require_visual_review": True}
    backup, backed_up = {}, False
    started = time.monotonic()

    def command(*parts, data=None, check=True):
        result = subprocess.run(prefix + list(parts), input=data, capture_output=True, timeout=30)
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout).decode(errors="replace"))
        return result

    def adb(*parts, check=True):
        return command(*parts, check=check).stdout.decode(errors="replace").strip()

    def stop():
        adb("shell", "am", "force-stop", args.package)

    def launch():
        adb("shell", "am", "start", "-W", "-n", args.package + "/.MainActivity")

    def write(path, data):
        require(path in PRIVATE_PATHS, "Unexpected private file")
        command("shell", "run-as", args.package, "sh", "-c", f"'cat > {path}'", data=data)

    def wait(operation, label):
        deadline, last = time.monotonic() + 25, None
        while time.monotonic() < deadline:
            try:
                return operation()
            except (RuntimeError, AssertionError, ET.ParseError, json.JSONDecodeError) as error:
                last = error
            time.sleep(.2)
        raise AssertionError(f"Timed out waiting for {label}: {last}")

    def saved():
        state = json.loads(adb("shell", "run-as", args.package, "cat", SAVE_PATHS[0]))
        replay(state)
        require(state["difficulty"] in NEXT, "Missing saved difficulty")
        return state

    def ui():
        remote = "/data/local/tmp/fairymahjong-difficulty-ui.xml"
        adb("shell", "rm", "-f", remote)
        adb("shell", "uiautomator", "dump", remote)
        xml = adb("shell", "cat", remote)
        (args.output / "latest-ui.xml").write_text(xml, encoding="utf-8")
        return list(ET.fromstring(xml).iter("node"))

    def bounds(node):
        rectangle = [int(number) for number in re.findall(r"-?\d+", node.get("bounds", ""))]
        require(len(rectangle) == 4 and rectangle[2] > rectangle[0] and rectangle[3] > rectangle[1],
                "Missing visible bounds")
        return rectangle

    def tap(node):
        require(node.get("enabled") == "true" and node.get("clickable") == "true", "Unavailable action")
        left, top, right, bottom = bounds(node)
        adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))

    def find(nodes, predicate, label):
        matches = [node for node in nodes if predicate(node)]
        require(len(matches) == 1, "Missing or duplicate " + label)
        return matches[0]

    def button(nodes, description):
        return find(nodes, lambda node: node.get("content-desc") == description and
                    node.get("clickable") == "true", description)

    def checked(state):
        nodes = ui()
        for resource, text in ((args.package + ":id/instructions_back_button", "Back to game"),
                               ("com.android.systemui:id/ok", "Got it")):
            candidates = [node for node in nodes if node.get("resource-id") == resource and
                          text in (node.get("content-desc"), node.get("text"))]
            if candidates:
                require(len(candidates) == 1, "Duplicate onboarding close action")
                tap(candidates[0])
                raise AssertionError("Onboarding dismissed; waiting for the game")
        require(saved() == state, "Saved board or settings changed unexpectedly")
        expected = f"Difficulty: {state['difficulty'].title()}. Switch to {NEXT[state['difficulty']].title()} and start a new board"
        control = button(nodes, expected)
        require(control.get("resource-id") == args.package + ":id/difficulty_button", "Wrong difficulty action")
        viewport = bounds(nodes[0])
        require((viewport[2] - viewport[0] > viewport[3] - viewport[1]) == (state["orientation"] == "landscape"),
                "Wrong native orientation")
        remaining, held = replay(state)
        tiles = {}
        for node in nodes:
            match = TILE.match(node.get("content-desc", ""))
            if match:
                tiles[int(match.group(1)) - 1] = node
        require(set(tiles) == remaining, "Board tiles are missing from the screen")
        hand = [node for node in nodes if node.get("content-desc", "").startswith("Hand slot ")]
        require(len(hand) == 4, "Missing hand slots")
        require(sum(": empty" not in node.get("content-desc", "") for node in hand) == len(held), "Wrong visible hand")
        return nodes, tiles, control

    def fresh(previous, difficulty, orientation):
        state = saved()
        require(state["difficulty"] == difficulty and state["orientation"] == orientation,
                "Fresh board has incorrect difficulty or orientation")
        require(not state["picks"], "Fresh board retained previous picks")
        require(state["positions"] != previous["positions"] or state["faces"] != previous["faces"],
                "Action reused the previous board")
        checked(state)
        return state

    def hold_tile(state):
        _, tiles, _ = checked(state)
        remaining, held = replay(state)
        require(not held, "Expected an empty hand before the test pick")
        chosen = free_tiles(state["positions"], remaining)[0]
        tap(tiles[chosen])
        expected = dict(state, picks=state["picks"] + [chosen])
        wait(lambda: checked(expected), "picked tile saved and visible")
        return expected

    def change(state):
        _, _, control = checked(state)
        tap(control)
        return wait(lambda: fresh(state, NEXT[state["difficulty"]], state["orientation"]), "difficulty change")

    def screenshot(name, orientation=None):
        data = command("exec-out", "screencap", "-p").stdout
        require(data.startswith(b"\x89PNG\r\n\x1a\n"), "Invalid screenshot")
        width, height = struct.unpack(">II", data[16:24])
        if orientation:
            require((width > height) == (orientation == "landscape"), "Wrong screenshot orientation")
        (args.output / name).write_bytes(data)
        report["screenshots"].append({"file": name, "width": width, "height": height})

    def record(message):
        report["checks"].append(message)
        print("PASS: " + message, flush=True)

    try:
        stop()
        adb("shell", "run-as", args.package, "mkdir", "-p", "files", "shared_prefs")
        for path in PRIVATE_PATHS:
            result = command("shell", "run-as", args.package, "test", "-f", path, check=False)
            require(result.returncode in (0, 1), "Cannot inspect original private file")
            if result.returncode == 0:
                backup[path] = command("exec-out", "run-as", args.package, "cat", path).stdout
        backed_up = True
        for path, data in backup.items():
            (args.output / (Path(path).name + ".original")).write_bytes(data)
        adb("shell", "run-as", args.package, "rm", "-f", *SAVE_PATHS)
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")
        launch()
        state = wait(saved, "fresh saved board")
        require(state["difficulty"] == "NORMAL" and state["orientation"] == "portrait" and not state["picks"],
                "Fresh installation does not default to portrait Normal")
        wait(lambda: checked(state), "fresh game controls")
        screenshot("portrait-normal.png", "portrait")
        record("Fresh game defaults to Normal with a visible difficulty action")

        for mode in ("portrait", "landscape"):
            require(state["orientation"] == mode, "Unexpected test orientation")
            for _ in range(3):
                before = hold_tile(state)
                state = change(before)
                record(f"{mode}: {before['difficulty']} to {state['difficulty']} deals a different board and clears the hand")
            if state["difficulty"] != "HARD":
                state = change(state)
            nodes, _, _ = checked(state)
            tap(button(nodes, "New board"))
            previous = state
            state = wait(lambda: fresh(previous, "HARD", mode), "new board retaining Hard")
            record(f"{mode}: New board retains Hard")
            if mode == "landscape":
                screenshot("landscape-hard.png", "landscape")
            state = hold_tile(state)
            stop()
            launch()
            wait(lambda: checked(state), "exact progress after process restart")
            record(f"{mode}: process restart retains Hard and the exact geometry, faces, picks, and held tile")
            nodes, _, _ = checked(state)
            target = "landscape" if mode == "portrait" else "portrait"
            tap(button(nodes, f"Switch to {target} and start a new board"))
            previous = state
            state = wait(lambda: fresh(previous, "HARD", target), "rotation retaining Hard")
            record(f"{mode} to {target}: rotation retains Hard, regenerates the board, and clears the hand")
        report["status"] = "passed"
    except Exception as error:
        report["status"], report["error"] = "failed", str(error)
        try:
            screenshot("failure.png")
        except Exception as capture_error:
            report["capture_error"] = str(capture_error)
        raise
    finally:
        try:
            if backed_up:
                stop()
                adb("shell", "run-as", args.package, "rm", "-f", *PRIVATE_PATHS)
                for path, data in backup.items():
                    write(path, data)
                    require(command("exec-out", "run-as", args.package, "cat", path).stdout == data,
                            "Original private file was not restored: " + path)
                for path in set(PRIVATE_PATHS) - set(backup):
                    require(command("shell", "run-as", args.package, "test", "-f", path, check=False).returncode == 1,
                            "Unexpected private file after restoration")
                report["original_state_restored"] = True
                launch()
        except Exception as error:
            report["status"], report["restore_error"] = "failed", str(error)
            raise
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 1)
            (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print("Report: " + str((args.output / "report.json").resolve()), flush=True)


if __name__ == "__main__":
    main()
