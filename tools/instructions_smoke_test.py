#!/usr/bin/env python3
"""Check the native two-panel guide on an explicitly selected debug device.

Exercises automatic onboarding, both panel arrangements, direct help, Back,
held tiles and hint preservation, removal of the haptics toggle, and process relaunch. Uses only observed native
nodes and an independently replayed v5 save. Original save/AtomicFile sidecars
and instructions preferences are restored byte-for-byte, then the app reopens.
Requires an installed debug APK. Does not install, launch an emulator, or change
device rotation/font settings. Screenshots require visual review.
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


PREFERENCE_PATHS = ("shared_prefs/instructions.xml", "shared_prefs/instructions.xml.bak")
RESTORED_PATHS = SAVE_PATHS + PREFERENCE_PATHS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", type=Path, default=Path("artifacts/instructions-smoke-test"))
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package")
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    prefix = [args.adb, "-s", args.serial]
    require_test_emulator(prefix)
    report = {"status": "running", "serial": args.serial, "checks": [], "screenshots": [],
              "panels": [], "original_save_restored": False, "original_preferences_restored": False,
              "screenshots_require_visual_review": True}
    backup, backed_up = {}, False
    density = font_scale = 1.0
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
            except (AssertionError, RuntimeError, ET.ParseError, json.JSONDecodeError) as error:
                last = error
            time.sleep(.2)
        raise AssertionError(f"Timed out waiting for {label}; last error: {last}")

    def stop():
        adb("shell", "am", "force-stop", args.package)

    def launch():
        adb("shell", "am", "start", "-W", "-n", args.package + "/.MainActivity")

    def file_bytes(path):
        require(path in RESTORED_PATHS, "Unexpected private file path")
        return command("exec-out", "run-as", args.package, "cat", path).stdout

    def write_bytes(path, data):
        require(path in RESTORED_PATHS, "Unexpected private file path")
        command("shell", "run-as", args.package, "sh", "-c", f"'cat > {path}'", data=data)

    def saved():
        state = json.loads(file_bytes(SAVE_PATHS[0]))
        replay(state)
        require(state["version"] == 5, "Expected v5 saved game")
        return state

    def exact(state):
        require(saved() == state, "Guide changed geometry, faces, picks, haptics, or orientation")

    def ui_xml():
        remote = "/data/local/tmp/fairymahjong-instructions-ui.xml"
        adb("shell", "rm", "-f", remote)
        adb("shell", "uiautomator", "dump", remote)
        return adb("shell", "cat", remote)

    def ui():
        return list(ET.fromstring(ui_xml()).iter("node"))

    def bounds(node):
        rect = [int(value) for value in re.findall(r"-?\d+", node.get("bounds", ""))]
        require(len(rect) == 4 and rect[0] < rect[2] and rect[1] < rect[3], "Missing or empty native bounds")
        return rect

    def tap(node):
        require(node.get("enabled") == "true", "Refusing to tap a disabled node")
        left, top, right, bottom = bounds(node)
        adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))

    def unique(nodes, *, text=None, resource=None, description=None, class_name=None):
        found = [node for node in nodes if
                 (text is None or node.get("text", "").casefold() == text.casefold()) and
                 (resource is None or node.get("resource-id") == args.package + ":id/" + resource) and
                 (description is None or node.get("content-desc") == description) and
                 (class_name is None or node.get("class") == class_name)]
        require(len(found) == 1, "Missing or duplicate native node " + str(text or resource or description))
        return found[0]

    def image_button(nodes, label):
        return unique(nodes, description=label, class_name="android.widget.ImageButton")

    def screenshot(name, mode=None):
        data = command("exec-out", "screencap", "-p").stdout
        require(data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24, "Screenshot is not a PNG")
        width, height = struct.unpack(">II", data[16:24])
        if mode:
            require((width > height) == (mode == "landscape"), "Screenshot has wrong native orientation")
        (output / name).write_bytes(data)
        report["screenshots"].append({"file": name, "width": width, "height": height})
        (output / (Path(name).stem + ".xml")).write_text(ui_xml(), encoding="utf-8")

    def dismiss_system_lesson(nodes):
        titles = [node for node in nodes if node.get("package") == "com.android.systemui" and
                  node.get("resource-id") == "com.android.systemui:id/immersive_cling_title" and
                  node.get("text") == "Viewing full screen"]
        if titles:
            require(len(titles) == 1, "Unexpected system full-screen lesson")
            actions = [node for node in nodes if node.get("package") == "com.android.systemui" and
                       node.get("resource-id") == "com.android.systemui:id/ok" and
                       node.get("text") == "Got it" and node.get("clickable") == "true"]
            require(len(actions) == 1, "System full-screen lesson has no exact Got it action")
            if not report.get("fullscreen_onboarding_dismissed"):
                tap(actions[0])
                report["fullscreen_onboarding_dismissed"] = True
            raise AssertionError("Waiting for acknowledged system lesson to disappear")

    def game_ui(state, nodes=None):
        nodes = ui() if nodes is None else nodes
        dismiss_system_lesson(nodes)
        exact(state)
        require(not any(node.get("resource-id") == args.package + ":id/instructions_screen" for node in nodes),
                "Instructions are still visible")
        require(any(node.get("content-desc", "").startswith("Board status: playing") for node in nodes),
                "Game has no visible accessible playing status")
        tiles = {int(match.group(1)) - 1: node for node in nodes
                 if (match := TILE.match(node.get("content-desc", "")))}
        remaining, held = replay(state)
        require(set(tiles) == remaining, "Expected all remaining game tiles visible")
        for index, node in tiles.items():
            require((node.get("enabled") == "true") == (index in free_tiles(state["positions"], remaining)),
                    "Game pickability changed")
        for slot in range(4):
            require(sum(node.get("content-desc", "").startswith(f"Hand slot {slot + 1}:") for node in nodes) == 1,
                    "Missing game hand slot")
        viewport = bounds(nodes[0])
        require((viewport[2] - viewport[0] > viewport[3] - viewport[1]) == (state["orientation"] == "landscape"),
                "Game orientation changed unexpectedly")
        hint_label = "Finding…" if any(node.get("content-desc") == "Finding…" for node in nodes) else "Hint"
        other_orientation = "portrait" if state["orientation"] == "landscape" else "landscape"
        for label in (hint_label, "How to play", "Switch to " + other_orientation + " and start a new board", "New board"):
            image_button(nodes, label)
        require(sum(node.get("class") == "android.widget.ImageButton" and node.get("package") == args.package and
                    not TILE.match(node.get("content-desc", ""))
                    for node in nodes) == 4, "Expected exactly four gameplay icon buttons")
        require(not any(node.get("text") in ("Play again", "Restart") or node.get("content-desc") == "Restart"
                        for node in nodes), "Removed Restart menu remains visible")
        return nodes, tiles

    def guide_ui(state):
        nodes = ui()
        dismiss_system_lesson(nodes)
        exact(state)
        screen = unique(nodes, resource="instructions_screen")
        panels = [unique(nodes, resource=name) for name in ("instructions_pick_panel", "instructions_pairs_panel")]
        headings = [unique(nodes, text=label) for label in ("Pick free tiles", "Make pairs")]
        require(not any(node.get("package") == args.package and
                        node.get("text", "").casefold() in {"how to play", "1", "2"} for node in nodes),
                "Removed guide title or numbered panel badge remains visible")
        close = unique(nodes, resource="instructions_back_button", description="Back to game",
                       class_name="android.widget.ImageButton")
        require(not close.get("text") and close.get("clickable") == "true", "Missing native Back to game icon button")
        require(not any(node.get("class") == "android.widget.CheckBox" or
                        node.get("resource-id") == args.package + ":id/instructions_haptics" or
                        node.get("text", "").casefold() == "gentle haptics" for node in nodes),
                "Removed haptics checkbox remains in the guide")
        require(not any(TILE.match(node.get("content-desc", "")) or
                        node.get("content-desc", "").startswith(("Hand slot ", "Board status:", "Switch to ")) or
                        node.get("content-desc") in ("Hint", "Finding…", "Restart", "New board") or
                        (node.get("content-desc") == "How to play" and node.get("class") == "android.widget.ImageButton")
                        for node in nodes),
                "Underlying game is still exposed through accessibility")
        viewport, close_rect = bounds(screen), bounds(close)
        panel_rects, heading_rects = [bounds(node) for node in panels], [bounds(node) for node in headings]
        for rect in [close_rect] + panel_rects + heading_rects:
            require(viewport[0] <= rect[0] < rect[2] <= viewport[2] and
                    viewport[1] <= rect[1] < rect[3] <= viewport[3], "Guide content is clipped outside its screen")
        require(close_rect[2] - close_rect[0] >= 48 * density - 1 and
                close_rect[3] - close_rect[1] >= 48 * density - 1, "Close target is below 48dp")
        mode = state["orientation"]
        require((viewport[2] - viewport[0] > viewport[3] - viewport[1]) == (mode == "landscape"),
                "Guide native orientation differs from saved game")
        first, second = panel_rects
        corner_panel = second if mode == "landscape" else first
        require(close_rect[0] < corner_panel[2] <= close_rect[2] and
                close_rect[1] <= corner_panel[1] < close_rect[3],
                "Guide close button does not overlap the upper-right panel corner")
        require(min(rect[1] for rect in panel_rects) - viewport[1] <= 24 * density + 1,
                "Guide still reserves a header row above its panels")
        for node in nodes:
            if node.get("package") == args.package and node.get("text"):
                rect = bounds(node)
                require(close_rect[2] <= rect[0] or rect[2] <= close_rect[0] or
                        close_rect[3] <= rect[1] or rect[3] <= close_rect[1],
                        "Guide close button obscures instruction text: " + node.get("text"))
        if mode == "portrait":
            require(first[3] <= second[1], "Portrait guide panels are not stacked")
            require(abs(first[0] - second[0]) <= 2 * density and abs(first[2] - second[2]) <= 2 * density,
                    "Portrait panel widths are inconsistent")
        else:
            require(first[2] <= second[0], "Landscape guide panels are not side by side")
            require(abs(first[1] - second[1]) <= 2 * density and abs(first[3] - second[3]) <= 2 * density,
                    "Landscape panel heights are inconsistent")
        for panel, heading in zip(panel_rects, heading_rects):
            require(panel[0] <= heading[0] < heading[2] <= panel[2] and
                    panel[1] <= heading[1] < heading[3] <= panel[3], "Panel heading is clipped")
        final_rules = []
        if font_scale <= 1.05:
            # Checking the final rules with room below their native bounds catches
            # text cropped at a ScrollView edge, even when Android exposes its full
            # text value. Enlarged system fonts may legitimately require scrolling.
            for panel, text in zip(panel_rects, (
                "Rotate: new view, new board.",
                "Four unmatched tiles end the game. Clear every tile to win.",
            )):
                rect = bounds(unique(nodes, text=text))
                require(panel[0] <= rect[0] < rect[2] <= panel[2] and
                        panel[1] <= rect[1] < rect[3] <= panel[3] - 2 * density,
                        "A final instruction rule needs scrolling or is clipped at the default system font")
                final_rules.append(rect)
        return nodes, close, {"orientation": mode, "screen": viewport, "panels": panel_rects,
                              "headings": heading_rects, "close": close_rect,
                              "close_corner_panel": "pairs" if mode == "landscape" else "pick",
                              "final_rules": final_rules, "full_content_checked": font_scale <= 1.05}

    def open_guide(state):
        nodes, _ = game_ui(state)
        tap(unique(nodes, resource="instructions_button", description="How to play",
                   class_name="android.widget.ImageButton"))
        return wait_for(lambda: guide_ui(state), "instructions opened directly from help icon")

    def close_guide(state, back=False):
        _, close, _ = guide_ui(state)
        before = file_bytes(SAVE_PATHS[0])
        if back:
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
        else:
            tap(close)
        nodes, tiles = wait_for(lambda: game_ui(state), "exact game after closing instructions")
        require(file_bytes(SAVE_PATHS[0]) == before, "Closing instructions rewrote the saved game bytes")
        return nodes, tiles

    def seen():
        prefs = ET.fromstring(file_bytes(PREFERENCE_PATHS[0]))
        require(any(node.tag == "boolean" and node.get("name") == "seen" and node.get("value") == "true"
                    for node in prefs), "Instructions were not marked as seen")

    def record(label):
        report["checks"].append(label)
        print("PASS: " + label, flush=True)

    try:
        require(adb("get-state") == "device", "Selected device is not authorized")
        adb("shell", "run-as", args.package, "pwd")
        values = re.findall(r"(?:Physical|Override) density:\s*(\d+)", adb("shell", "wm", "density"))
        require(values, "Cannot read display density")
        density = int(values[-1]) / 160
        report["density_dpi"] = int(values[-1])
        scale_setting = adb("shell", "settings", "get", "system", "font_scale")
        font_scale = 1.0 if scale_setting == "null" else float(scale_setting)
        require(0 < font_scale < 10, "Unexpected Android font scale")
        report["system_font_scale"] = font_scale
        stop()
        adb("shell", "run-as", args.package, "mkdir", "-p", "files", "shared_prefs")
        for path in RESTORED_PATHS:
            exists = command("shell", "run-as", args.package, "test", "-f", path, check=False)
            if exists.returncode == 0:
                backup[path] = file_bytes(path)
            else:
                require(exists.returncode == 1, "Cannot inspect private file " + path)
        backed_up = True
        for path, data in backup.items():
            (output / (Path(path).name + ".original")).write_bytes(data)
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")
        adb("shell", "run-as", args.package, "rm", "-f", *RESTORED_PATHS)
        portrait = {"version": 5, "layout": "instructions-preservation", "positions":
                    [[column * 2, row * 2, 0] for row in range(2) for column in range(4)],
                    "faces": [6, 23, 23, 6, 44, 61, 61, 44], "picks": [0],
                    "haptics": False, "orientation": "portrait"}
        replay(portrait)
        write_bytes(SAVE_PATHS[0], json.dumps(portrait).encode("utf-8"))
        launch()
        _, _, geometry = wait_for(lambda: guide_ui(portrait), "first-launch portrait instructions")
        report["panels"].append(geometry)
        screenshot("01-portrait-guide.png", "portrait")
        record("First launch shows stacked portrait panels with a 48dp corner close icon, no title or numbered badges, and no haptics checkbox")
        record("Guide hides board tiles, hand slots, and game actions from accessibility")
        # A text heading cannot activate anything; this still reaches the guide's
        # touch surface at a coordinate occupied by the underlying game window.
        nodes, _, _ = guide_ui(portrait)
        tap(unique(nodes, text="Pick free tiles"))
        exact(portrait)
        record("Tapping guide content cannot pick an underlying tile or change the held tile")
        nodes, _ = close_guide(portrait)
        wait_for(seen, "seen preference flushed")
        record("Back to game preserves exact save bytes, nonempty hand, legacy haptics field, and orientation; marks instructions seen")
        stop()
        launch()
        nodes, _ = wait_for(lambda: game_ui(portrait), "seen guide remains closed after process relaunch")
        record("Completed instructions do not reopen automatically on process relaunch")
        tap(image_button(nodes, "Hint"))

        def hinted():
            visible, _ = game_ui(portrait)
            markers = sorted(node.get("content-desc") for node in visible if "Hint:" in node.get("content-desc", ""))
            require(any("Hint: pick next" in marker for marker in markers), "Safe hint has no next tile")
            require(any("Hint: matching tile" in marker for marker in markers), "Hint does not show the held match")
            return markers

        hint_before = wait_for(hinted, "safe hint with held tile")
        open_guide(portrait)
        close_guide(portrait, back=True)
        require(hinted() == hint_before, "Opening or dismissing instructions changed an existing safe hint")
        record("Help icon opens instructions directly; Android Back returns to the exact hand and existing safe hint")
        open_guide(portrait)
        close_guide(portrait)
        require(hinted() == hint_before, "Reopening the guide changed an existing safe hint")
        stop()
        launch()
        wait_for(lambda: game_ui(portrait), "exact progressed board retained through process relaunch")
        open_guide(portrait)
        close_guide(portrait)
        record("Repeated guide opening and process relaunch keep the haptics checkbox absent and preserve the held tile and legacy save")
        nodes, _ = game_ui(portrait)
        tap(image_button(nodes, "Switch to landscape and start a new board"))

        def landscape_ready():
            state = saved()
            require(state["orientation"] == "landscape" and not state["picks"] and state["haptics"] == portrait["haptics"],
                    "Manual rotation has not produced a fresh landscape deal")
            visible, tiles = game_ui(state)
            return state, visible, tiles

        landscape, nodes, tiles = wait_for(landscape_ready, "landscape game")
        pick = free_tiles(landscape["positions"], set(tiles))[0]
        tap(tiles[pick])
        landscape = dict(landscape, picks=[pick])
        wait_for(lambda: game_ui(landscape), "held landscape tile")
        _, _, geometry = open_guide(landscape)
        report["panels"].append(geometry)
        screenshot("02-landscape-guide.png", "landscape")
        record("Landscape panels sit side by side without a title row or badges; the upper-right corner close icon leaves all text clear")
        close_guide(landscape)
        record("Closing landscape instructions retains the exact landscape deal, held tile, and legacy haptics field")
        open_guide(landscape)
        # Unlike force-stop, killing a stopped background activity can retain its
        # saved instance Bundle. Some Android images keep this process protected;
        # report that limitation instead of pretending a force-stop tests Bundle.
        previous_pid = adb("shell", "pidof", args.package)
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        time.sleep(.5)
        adb("shell", "am", "kill", args.package)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and adb("shell", "pidof", args.package, check=False):
            time.sleep(.25)
        killed = not adb("shell", "pidof", args.package, check=False)
        launch()
        if killed:
            wait_for(lambda: guide_ui(landscape), "instructions retained through background process recreation")
            require(adb("shell", "pidof", args.package) != previous_pid, "Background process was not recreated")
            record("Open instructions survive saved-instance process recreation in landscape without changing progress")
        else:
            wait_for(lambda: guide_ui(landscape), "instructions retained after app background/foreground")
            report["process_recreation_skipped"] = "Android retained the background process after am kill"
            record("Open instructions remain visible after returning from the home screen; background process was protected")
        close_guide(landscape, back=True)
        stop()
        launch()
        wait_for(lambda: game_ui(landscape), "landscape progress after process relaunch")
        screenshot("03-landscape-game-restored.png", "landscape")
        record("Process relaunch retains landscape progress and keeps previously dismissed instructions closed")
        report["status"] = "passed"
    except Exception as error:
        report["status"], report["error"] = "failed", str(error)
        for label, capture in (("screenshot", lambda: screenshot("failure.png")),
                               ("hierarchy", lambda: (output / "failure-ui.xml").write_text(ui_xml(), encoding="utf-8"))):
            try:
                capture()
            except Exception as diagnostic_error:
                report.setdefault("diagnostic_errors", {})[label] = str(diagnostic_error)
        raise
    finally:
        restoration_errors = []
        try:
            if backed_up:
                stop()
                adb("shell", "run-as", args.package, "rm", "-f", *RESTORED_PATHS)
                for path, data in backup.items():
                    write_bytes(path, data)
                    require(file_bytes(path) == data, "Original file not restored: " + path)
                for path in set(RESTORED_PATHS) - set(backup):
                    require(command("shell", "run-as", args.package, "test", "-f", path, check=False).returncode == 1,
                            "Unexpected restored file: " + path)
                report["original_save_restored"] = True
                report["original_preferences_restored"] = True
        except Exception as error:
            restoration_errors.append("Private files: " + str(error))
        try:
            launch()
        except Exception as error:
            restoration_errors.append("Relaunch: " + str(error))
        if restoration_errors:
            report["status"], report["restore_errors"] = "failed", restoration_errors
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("Report: " + str((output / "report.json").resolve()), flush=True)
        require(not restoration_errors, "; ".join(restoration_errors))


if __name__ == "__main__":
    main()
