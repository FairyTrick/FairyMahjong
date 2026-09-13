#!/usr/bin/env python3
"""Validate short meadow motion on a dedicated test emulator.

Native events exercise normal and consecutive picks, a matching fourth tile,
hint/help/new-deal/rotation/background interruptions, and portrait scrolling.
The same pick sequence runs with Android animator_duration_scale 1 and 0.
Screenrecord clips and decoded frame comparisons verify actual transient drawing
and settling; screenshots/videos still need visual review. No app test hooks.
Interruption batches briefly use scale 10 and must finish within the resulting
2.2-second flight, then immediately restore normal speed before inspecting state.
Original private file bytes and modified system settings are restored in finally.
Requires an installed debug APK, Pillow, NumPy, ffmpeg, and ffprobe.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageChops

from artwork_smoke_test import catalog_names
from hint_smoke_test import TILE
from meadow_ui_smoke_test import meadow_fixture
from save_migration_smoke_test import SAVE_PATHS, free_tiles, replay, require
from test_device_guard import require_test_emulator


PRIVATE_PATHS = SAVE_PATHS + ("shared_prefs/instructions.xml", "shared_prefs/instructions.xml.bak")
ANIMATION_KEY = "animator_duration_scale"
SEEN_PREFERENCES = b'<?xml version="1.0" encoding="utf-8"?><map><boolean name="seen" value="true" /></map>\n'


def video_evidence(path, ffmpeg, ffprobe):
    """Count pixels absent from both settled states, excluding system gesture UI."""
    info = json.loads(subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(path)], capture_output=True,
        check=True, timeout=20).stdout)["streams"][0]
    width = 360
    height = round(info["height"] * width / info["width"] / 2) * 2
    decoded = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-vf", f"fps=30,scale={width}:{height}",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], capture_output=True,
        check=True, timeout=40).stdout
    pixels = np.frombuffer(decoded, dtype=np.uint8).reshape(-1, height, width, 3)
    # Android records changed frames only. A completely still tail may have no
    # encoded timestamps even though screenrecord ran for the full six seconds.
    require(len(pixels) >= 20, "Screenrecord did not capture the baseline and transition")
    # App bars are immersive, but exclude the animated Android gesture strip.
    pixels = pixels[:, :int(height * .955)]
    before = np.median(pixels[3:15], axis=0).astype(np.int16)
    after = pixels[-1].astype(np.int16)
    transient = []
    settled = []
    for frame in pixels:
        values = frame.astype(np.int16)
        from_before = np.max(np.abs(values - before), axis=2) > 24
        from_after = np.max(np.abs(values - after), axis=2) > 24
        transient.append(int(np.count_nonzero(from_before & from_after)))
        settled.append(int(np.count_nonzero(from_after)))
    peak = int(np.argmax(transient))
    frame_path = path.with_name(path.stem + "-motion-frame.png")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", str(peak / 30), "-i", str(path),
                    "-frames:v", "1", str(frame_path)], capture_output=True,
                   check=True, timeout=20)
    return {"clip": path.name, "frame_count": len(pixels), "analysis_size": [width, height],
            "transient_frames_over_80_pixels": sum(value > 80 for value in transient),
            "peak_transient_pixels": max(transient), "peak_seconds": round(peak / 30, 3),
            "encoded_duration_seconds": round(len(pixels) / 30, 3),
            "last_five_frames_changed_pixels": settled[-5:],
            "motion_frame": frame_path.name}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", type=Path, default=Path("artifacts/motion-smoke-test"))
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--interruptions-only", action="store_true",
                       help="Run only interruption checks, omitting the completed video/idle matrix")
    scope.add_argument("--video-matrix-only", action="store_true",
                       help="Run only the enabled/disabled video and idle matrix in both orientations")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package")
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    prefix = [args.adb, "-s", args.serial]
    avd = require_test_emulator(prefix)
    names = catalog_names()
    report = {"status": "running", "serial": args.serial, "avd": avd, "checks": [],
              "videos": [], "screenshots": [], "original_save_restored": False,
              "original_preferences_restored": False, "system_animation_setting_restored": False,
              "screenshots_and_videos_require_visual_review": True}
    backup, backed_up = {}, False
    original_scale = None
    scale_changed = False
    originally_running = False
    recorder = None
    started = time.monotonic()

    def command(*parts, data=None, check=True):
        result = subprocess.run(prefix + list(parts), input=data, capture_output=True, timeout=30)
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout).decode(errors="replace"))
        return result

    def adb(*parts, check=True):
        return command(*parts, check=check).stdout.decode(errors="replace").strip()

    def wait_for(operation, label, seconds=25):
        deadline, last = time.monotonic() + seconds, None
        while time.monotonic() < deadline:
            try:
                return operation()
            except (RuntimeError, AssertionError, ET.ParseError, json.JSONDecodeError) as error:
                last = error
            time.sleep(.15)
        raise AssertionError(f"Timed out waiting for {label}; last error: {last}")

    def stop():
        adb("shell", "am", "force-stop", args.package)

    def launch():
        adb("shell", "am", "start", "-W", "-n", args.package + "/.MainActivity")

    def file_bytes(path):
        require(path in PRIVATE_PATHS, "Unexpected private file path")
        return command("exec-out", "run-as", args.package, "cat", path).stdout

    def write_bytes(path, data):
        require(path in PRIVATE_PATHS, "Unexpected private file path")
        command("shell", "run-as", args.package, "sh", "-c", f"'cat > {path}'", data=data)

    def saved():
        state = json.loads(file_bytes(SAVE_PATHS[0]))
        replay(state)
        return state

    def exact(state):
        require(saved() == state, "Accepted picks, identities, geometry, orientation, or haptics changed")
        return state

    def inject(state, full_board=True):
        stop()
        adb("shell", "run-as", args.package, "rm", "-f", *SAVE_PATHS)
        write_bytes(SAVE_PATHS[0], json.dumps(state).encode("utf-8"))
        launch()
        wait_for(lambda: exact(state), "fixture save")
        if full_board:
            return wait_for(lambda: game_ui(state), "fixture native board")
        nodes = ui()
        return nodes, {int(match.group(1)) - 1: node for node in nodes
                       if (match := TILE.match(node.get("content-desc", "")))}

    def ui_xml():
        remote = "/data/local/tmp/fairymahjong-motion-ui.xml"
        adb("shell", "rm", "-f", remote)
        adb("shell", "uiautomator", "dump", remote)
        return adb("shell", "cat", remote)

    def ui():
        return list(ET.fromstring(ui_xml()).iter("node"))

    def bounds(node):
        values = [int(value) for value in re.findall(r"-?\d+", node.get("bounds", ""))]
        require(len(values) == 4 and values[0] < values[2] and values[1] < values[3], "Missing native bounds")
        return values

    def center(node):
        left, top, right, bottom = bounds(node)
        return (left + right) // 2, (top + bottom) // 2

    def action(nodes, description):
        matches = [node for node in nodes if node.get("content-desc") == description
                   and node.get("class") == "android.widget.ImageButton"]
        require(len(matches) == 1 and matches[0].get("enabled") == "true", "Missing action " + description)
        return matches[0]

    def tap(node):
        x, y = center(node)
        adb("shell", "input", "tap", str(x), str(y))

    def batch(nodes, final_command=None, motion_interrupt=None):
        commands = [f"input tap {x} {y}" for x, y in map(center, nodes)]
        if final_command:
            commands.append(final_command)
        if motion_interrupt:
            # Extend the real 220ms flight solely to make interruption coverage
            # independent of ADB process latency. Restore normal speed immediately.
            set_scale(10)
        began = time.monotonic()
        try:
            adb("shell", "sh", "-c", "'" + "; ".join(commands) + "'")
            elapsed = round((time.monotonic() - began) * 1000)
        finally:
            if motion_interrupt:
                set_scale(1)
        if motion_interrupt:
            report.setdefault("interruptions_within_2200ms_flight", {})[motion_interrupt] = elapsed
            require(elapsed < 2200, "ADB interruption batch exceeded the deliberately slowed flight")
        return elapsed

    def game_ui(state):
        nodes = ui()
        require(not any(node.get("resource-id") == args.package + ":id/instructions_screen" for node in nodes),
                "Game unexpectedly shows instructions")
        remaining, hand = replay(state)
        tiles = {int(match.group(1)) - 1: node for node in nodes
                 if (match := TILE.match(node.get("content-desc", "")))}
        require(set(tiles) == remaining, "Removed tiles or decorative ghosts remain accessible")
        available = set(free_tiles(state["positions"], remaining)) if len(hand) < 4 else set()
        for index, node in tiles.items():
            require((node.get("enabled") == "true") == (index in available), "Motion blocked a legal next pick")
        for slot in range(4):
            name = names[state["faces"][hand[slot]]] if slot < len(hand) else "empty"
            require(sum(node.get("content-desc", "").startswith(f"Hand slot {slot + 1}: {name}")
                        for node in nodes) == 1, "Hand identity/order differs from accepted picks")
        exact(state)
        return nodes, tiles

    def screenshot(name):
        data = command("exec-out", "screencap", "-p").stdout
        require(data.startswith(b"\x89PNG\r\n\x1a\n"), "Screenshot is not a PNG")
        path = output / name
        path.write_bytes(data)
        report["screenshots"].append(name)
        return path

    def same_picture(first, second):
        with Image.open(first) as a, Image.open(second) as b:
            require(a.size == b.size, "Settled screenshots have different sizes")
            region = (0, 0, a.width, int(a.height * .955))
            difference = ImageChops.difference(a.convert("RGB").crop(region), b.convert("RGB").crop(region))
            require(difference.getbbox() is None, "Settled board keeps animating or contains a stale transient")

    def record(label):
        report["checks"].append(label)
        print("PASS: " + label, flush=True)

    def set_scale(value):
        nonlocal scale_changed
        scale_changed = True
        adb("shell", "settings", "put", "global", ANIMATION_KEY, str(value))
        require(float(adb("shell", "settings", "get", "global", ANIMATION_KEY)) == value,
                "Android animation preference was not applied")

    def clip_pick(state, tile, label, moving):
        nonlocal recorder
        remote = "/data/local/tmp/fairymahjong-motion-" + label + ".mp4"
        adb("shell", "rm", "-f", remote)
        recorder = subprocess.Popen(prefix + ["shell", "screenrecord", "--time-limit", "6",
                                    "--bit-rate", "12000000", remote],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(1.15)
            require(recorder.poll() is None, "Native video recording stopped before the pick")
            tap(tile)
            wait_for(lambda: exact(state), "immediately persisted recorded pick")
            _, errors = recorder.communicate(timeout=12)
            require(recorder.returncode == 0, "Screenrecord failed: " + errors.decode(errors="replace"))
        finally:
            if recorder.poll() is None:
                recorder.terminate()
                recorder.wait(timeout=5)
            recorder = None
        local = output / (label + ".mp4")
        adb("pull", remote, str(local))
        adb("shell", "rm", "-f", remote)
        evidence = video_evidence(local, args.ffmpeg, args.ffprobe)
        report["videos"].append(evidence)
        if moving:
            require(evidence["transient_frames_over_80_pixels"] >= 2, "No multi-frame tile motion was captured")
        else:
            require(evidence["transient_frames_over_80_pixels"] <= 1, "Android disabled-motion preference still animates")
        return evidence

    try:
        require(adb("get-state") == "device", "Test emulator is not authorized")
        adb("shell", "run-as", args.package, "pwd")
        originally_running = bool(adb("shell", "pidof", args.package, check=False))
        original_scale = adb("shell", "settings", "get", "global", ANIMATION_KEY)
        report["original_animator_duration_scale"] = original_scale
        stop()
        adb("shell", "run-as", args.package, "mkdir", "-p", "files", "shared_prefs")
        for path in PRIVATE_PATHS:
            exists = command("shell", "run-as", args.package, "test", "-f", path, check=False)
            if exists.returncode == 0:
                backup[path] = file_bytes(path)
            else:
                require(exists.returncode == 1, "Cannot inspect original private file " + path)
        backed_up = True
        for path, data in backup.items():
            (output / (Path(path).name + ".original")).write_bytes(data)
        write_bytes(PRIVATE_PATHS[3], SEEN_PREFERENCES)
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")

        for orientation in (() if args.interruptions_only else ("portrait", "landscape")):
            base = dict(meadow_fixture(), orientation=orientation)
            third = dict(base, picks=[0, 4, 1])
            fourth = dict(base, picks=[0, 4, 1, 3])
            require(replay(third)[1] == [0, 4, 1] and replay(fourth)[1] == [4, 1],
                    "Fourth-tile fixture does not clear and compact the hand")
            settled = []
            for scale in (1, 0):
                set_scale(scale)
                nodes, tiles = inject(base)
                elapsed = batch([tiles[index] for index in [0, 4, 1, 3]])
                wait_for(lambda: game_ui(fourth), "consecutive picks with matching fourth")
                report.setdefault("consecutive_input_ms", {})[f"{orientation}-{scale}"] = elapsed
                record(f"{orientation}, motion {scale}: consecutive picks accept a matching fourth tile and retain the two survivors in order")
                nodes, tiles = inject(third)
                clip_pick(fourth, tiles[3], f"{orientation}-motion-{scale}", moving=bool(scale))
                wait_for(lambda: game_ui(fourth), "recorded fourth-tile outcome")
                settled.append(screenshot(f"{orientation}-settled-{scale}.png"))
                time.sleep(1)
                idle = screenshot(f"{orientation}-idle-{scale}.png")
                same_picture(settled[-1], idle)
                record(f"{orientation}, motion {scale}: recorded transition settles completely and the idle board remains still")
            same_picture(*settled)
            record(f"{orientation}: enabled and disabled motion produce identical saved state and settled pixels")

        if args.video_matrix_only:
            report["status"] = "passed"
            return

        set_scale(1)
        base = meadow_fixture()
        held = dict(base, picks=[0])
        nodes, tiles = inject(base)
        # Read real accessibility nodes while the flying picture is still active.
        # A 22-second test-only flight gives uiautomator time to inspect one frame;
        # help cancels it immediately afterwards, so this never waits 22 seconds.
        set_scale(100)
        began = time.monotonic()
        tap(tiles[0])
        nodes, _ = game_ui(held)
        elapsed = round((time.monotonic() - began) * 1000)
        require(elapsed < 22000, "Accessibility inspection outlasted the slowed active flight")
        report["in_flight_accessibility_read_ms"] = elapsed
        screenshot("in-flight-accessibility.png")
        tap(action(nodes, "How to play"))
        set_scale(1)
        tap(action(ui(), "Back to game"))
        wait_for(lambda: game_ui(held), "hand after cancelling inspected flight")
        record("All four hand slots expose their final identities to accessibility while the tile picture is still flying")

        nodes, tiles = inject(base)
        batch([tiles[index] for index in [0, 4, 1, 3]], motion_interrupt="consecutive picks")
        wait_for(lambda: game_ui(dict(base, picks=[0, 4, 1, 3])), "consecutive in-flight picks")
        record("Consecutive in-flight picks interrupt prior decoration and accept a matching fourth without a lock")
        nodes, tiles = inject(base)
        batch([tiles[0], action(nodes, "Hint")], motion_interrupt="hint")

        def hinted():
            nodes, _ = game_ui(held)
            markers = sorted(node.get("content-desc") for node in nodes
                             if "Hint:" in node.get("content-desc", ""))
            require(any("Hint: pick next" in marker for marker in markers), "Missing hint after immediate request")
            return nodes, markers

        nodes, markers = wait_for(hinted, "hint after accepted pick")
        first_hint = screenshot("hint-settled.png")
        time.sleep(1)
        same_picture(first_hint, screenshot("hint-idle.png"))
        tap(action(nodes, "How to play"))
        guide = ui()
        close = action(guide, "Back to game")
        exact(held)
        screenshot("help-after-hint.png")
        tap(close)
        require(wait_for(hinted, "hint after guide")[1] == markers, "Guide changed hint continuation")
        record("Hint requested during a pick remains correct, settles without looping, and survives help unchanged")

        nodes, tiles = inject(base)
        batch([tiles[0], action(nodes, "How to play")], motion_interrupt="help")
        guide = ui()
        close = action(guide, "Back to game")
        exact(held)
        before = file_bytes(SAVE_PATHS[0])
        screenshot("help-during-pick.png")
        tap(close)
        wait_for(lambda: game_ui(held), "accepted pick after guide interruption")
        require(file_bytes(SAVE_PATHS[0]) == before, "Closing guide rewrote accepted state")
        record("Help immediately after a pick hides decoration and preserves its accepted save and hand")

        for target in ("New board", "Switch to landscape and start a new board"):
            nodes, tiles = inject(base)
            batch([tiles[0], action(nodes, target)], motion_interrupt=target)

            def fresh():
                state = saved()
                expected_orientation = "landscape" if target.startswith("Switch") else "portrait"
                require(not state["picks"] and state["orientation"] == expected_orientation,
                        "New deal retained old picks or has wrong orientation")
                require(state["faces"] != base["faces"] or state["positions"] != base["positions"],
                        "New deal reused fixture")
                nodes, _ = game_ui(state)
                require(not any("Hint:" in node.get("content-desc", "") for node in nodes), "New deal retained a hint")
                return state

            wait_for(fresh, target + " after pick")
            screenshot("rotate-during-pick.png" if target.startswith("Switch") else "new-board-during-pick.png")
            record(target + " immediately after a pick clears the old hand/hint and settles on a fresh deal")

        nodes, tiles = inject(base)
        batch([tiles[0]], final_command="input keyevent KEYCODE_HOME", motion_interrupt="background")
        wait_for(lambda: exact(held), "accepted pick after backgrounding")
        launch()
        wait_for(lambda: game_ui(held), "return from background")
        after_pause = screenshot("resumed-after-pick.png")
        stop()
        launch()
        wait_for(lambda: game_ui(held), "process recreation")
        same_picture(after_pause, screenshot("recreated-after-pick.png"))
        record("Backgrounding during a pick and process recreation preserve exact progress with no stale decoration")

        tall = dict(base, layout="motion-scroll", positions=[[column * 2, row * 2, 0]
                    for row in range(14) for column in range(4)], faces=[6, 23, 44, 61] * 14)
        nodes, tiles = inject(tall, full_board=False)
        viewport = [node for node in nodes if node.get("resource-id") == args.package + ":id/board_viewport"]
        require(len(viewport) == 1, "Missing portrait scrolling viewport")
        left, top, right, bottom = bounds(viewport[0])
        x = (left + right) // 2
        batch([tiles[0]], final_command=f"input swipe {x} {bottom - 40} {x} {top + 120} 180",
              motion_interrupt="scroll")
        wait_for(lambda: exact(dict(tall, picks=[0])), "accepted pick after immediate scroll")
        screenshot("scroll-after-pick.png")
        record("Portrait scrolling immediately after a pick preserves the accepted state")
        report["status"] = "passed"
    except Exception as error:
        report["status"], report["error"] = "failed", str(error)
        for label, capture in (("screenshot", lambda: screenshot("failure.png")),
                               ("hierarchy", lambda: (output / "failure-ui.xml").write_text(ui_xml(), encoding="utf-8"))):
            try:
                capture()
            except Exception as diagnostic:
                report.setdefault("diagnostic_errors", {})[label] = str(diagnostic)
        raise
    finally:
        try:
            if recorder and recorder.poll() is None:
                recorder.terminate()
                recorder.wait(timeout=5)
            if scale_changed:
                if original_scale == "null":
                    adb("shell", "settings", "delete", "global", ANIMATION_KEY)
                else:
                    adb("shell", "settings", "put", "global", ANIMATION_KEY, original_scale)
                require(adb("shell", "settings", "get", "global", ANIMATION_KEY) == original_scale,
                        "Android motion setting was not restored")
                report["system_animation_setting_restored"] = True
            if backed_up:
                stop()
                adb("shell", "run-as", args.package, "rm", "-f", *PRIVATE_PATHS)
                for path, data in backup.items():
                    write_bytes(path, data)
                    require(file_bytes(path) == data, "Original bytes were not restored for " + path)
                for path in set(PRIVATE_PATHS) - set(backup):
                    require(command("shell", "run-as", args.package, "test", "-f", path, check=False).returncode == 1,
                            "Unexpected private file after restoration")
                report["original_save_restored"] = report["original_preferences_restored"] = True
                if originally_running:
                    launch()
        except Exception as error:
            report["status"], report["restore_error"] = "failed", str(error)
            raise
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 1)
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Report: {(output / 'report.json').resolve()}", flush=True)


if __name__ == "__main__":
    main()
