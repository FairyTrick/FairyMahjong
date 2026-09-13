#!/usr/bin/env python3
"""Check the quiet meadow UI on one explicitly selected debug Android device.

Uses one eight-tile fairy fixture, native accessibility nodes, and screenshots.
Original save bytes, AtomicFile sidecars, and instructions preferences are restored
in finally. First-run instructions are acknowledged before checking play. The normal
run checks top controls, a horizontal bottom hand, blocked taps, Hint, direct help,
absence of the haptics toggle, process restoration, and direct New board. --end-states also captures completion
and a full hand. --shape-fixtures checks additional generated v4 saves and captures
their native layouts. --vertical-shapes also requires at least ten rows and a
height-to-width ratio of at least 10:7. Does not alter device density, orientation, or display
settings. Requires Pillow for a read-only tile color comparison. Screenshots need
visual review.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import struct
import subprocess
import time
import xml.etree.ElementTree as ET

from test_device_guard import require_test_emulator
from PIL import Image, ImageChops, ImageStat

from artwork_smoke_test import catalog_names
from hint_smoke_test import TILE, fixture, winnable
from save_migration_smoke_test import SAVE_PATHS, free_tiles, require, replay


PREFERENCE_PATHS = ("shared_prefs/instructions.xml", "shared_prefs/instructions.xml.bak")
RESTORED_PATHS = SAVE_PATHS + PREFERENCE_PATHS


def meadow_fixture():
    positions = [[column * 2, row * 2, 0] for row in range(2) for column in range(4)]
    state = fixture("meadow-ui", positions, [6, 23, 23, 6, 44, 61, 61, 44])
    require(winnable(state), "Meadow fixture must have a complete winning path")
    return dict(state, version=5, orientation="portrait")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", default="artifacts/meadow-ui-smoke-test")
    parser.add_argument("--end-states", action="store_true")
    parser.add_argument("--shape-fixtures", type=Path,
                        help="Directory containing generated v4 board saves to check and capture")
    parser.add_argument("--vertical-shapes", action="store_true",
                        help="Require shape fixtures to be at least ten rows tall and at least 10:7 in height:width")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package name")
    shape_fixtures = sorted(args.shape_fixtures.glob("*.json")) if args.shape_fixtures else []
    require(args.shape_fixtures is None or shape_fixtures, "No shape fixture JSON files found")
    require(not args.vertical_shapes or shape_fixtures, "--vertical-shapes requires --shape-fixtures")
    names, initial = catalog_names(), meadow_fixture()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    prefix = [args.adb, "-s", args.serial]
    require_test_emulator(prefix)
    report = {"status": "running", "serial": args.serial, "checks": [], "screenshots": [], "shapes": [],
              "vertical_shapes_required": args.vertical_shapes,
              "original_save_restored": False, "original_preferences_restored": False,
              "screenshots_require_visual_review": True}
    backup = {}
    backed_up = originally_running = False
    screen_size = density = None
    started = time.monotonic()

    def command(*parts, data=None, check=True):
        result = subprocess.run(prefix + list(parts), input=data, capture_output=True, timeout=30)
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout).decode(errors="replace"))
        return result

    def adb(*parts, check=True):
        return command(*parts, check=check).stdout.decode(errors="replace").strip()

    def wait_for(operation, label, seconds=20):
        deadline, last_error = time.monotonic() + seconds, None
        while time.monotonic() < deadline:
            try:
                return operation()
            except (RuntimeError, AssertionError, ET.ParseError, json.JSONDecodeError) as error:
                last_error = error
            time.sleep(0.2)
        raise AssertionError(f"Timed out waiting for {label}; last error: {last_error}")

    def stop():
        adb("shell", "am", "force-stop", args.package)

    def launch():
        adb("shell", "am", "start", "-W", "-n", args.package + "/.MainActivity")

    def write_bytes(path, data):
        require(path in RESTORED_PATHS, "Unexpected private file path")
        command("shell", "run-as", args.package, "sh", "-c", f"'cat > {path}'", data=data)

    def clear_saves():
        adb("shell", "run-as", args.package, "rm", "-f", *SAVE_PATHS)

    def saved():
        state = json.loads(adb("shell", "run-as", args.package, "cat", SAVE_PATHS[0]))
        replay(state)
        return state

    def expect_save(state):
        require(saved() == state, "Geometry, faces, picks, or haptics differ from the expected save")
        return state

    def inject(state):
        stop()
        clear_saves()
        write_bytes(SAVE_PATHS[0], json.dumps(state).encode("utf-8"))
        launch()
        wait_for(lambda: expect_save(state), "fixture save")

    def ui_xml():
        remote = "/data/local/tmp/fairymahjong-meadow-ui.xml"
        adb("shell", "rm", "-f", remote)
        adb("shell", "uiautomator", "dump", remote)
        return adb("shell", "cat", remote)

    def ui():
        return list(ET.fromstring(ui_xml()).iter("node"))

    def bounds(node):
        coords = [int(value) for value in re.findall(r"-?\d+", node.get("bounds", ""))]
        require(len(coords) == 4, "Native node has no bounds")
        left, top, right, bottom = coords
        require(right > left and bottom > top, "Native node has empty bounds")
        if screen_size:
            require(0 <= left < right <= screen_size[0] and 0 <= top < bottom <= screen_size[1],
                    f"Native node is outside the screen: {node.get('content-desc') or node.get('text')}")
        return coords

    def tap(node, allow_disabled=False):
        require(allow_disabled or node.get("enabled") == "true", "Refusing to tap a disabled node")
        left, top, right, bottom = bounds(node)
        adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))

    def screenshot(name):
        nonlocal screen_size
        data = command("exec-out", "screencap", "-p").stdout
        require(data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24, "Screenshot is not a PNG")
        screen_size = struct.unpack(">II", data[16:24])
        (output / name).write_bytes(data)
        report["screen_pixels"] = list(screen_size)
        report["screenshots"].append(name)

    def unchanged_tile_colors(before, after, old_node, new_node):
        rectangle = bounds(old_node)
        require(rectangle == bounds(new_node), "Unlocking a tile shifted its screen position")
        left, top, right, bottom = rectangle
        width, height = right - left, bottom - top
        # Sample the interior art, clear of neighboring sidewalls and their shadows.
        region = (left + round(width * .18), top + round(height * .15),
                  left + round(width * .68), top + round(height * .75))
        with Image.open(output / before) as old_image, Image.open(output / after) as new_image:
            difference = ImageChops.difference(old_image.convert("RGB").crop(region),
                                              new_image.convert("RGB").crop(region))
            channel_changes = ImageStat.Stat(difference).mean
        report["blocked_to_available_rgb_mean_difference"] = channel_changes
        require(max(channel_changes) < 1, "Blocked tile changes color when it becomes available")

    def action(label, nodes, enabled=True):
        matches = [node for node in nodes if node.get("content-desc") == label
                   and node.get("class") == "android.widget.ImageButton"
                   and node.get("clickable") == "true"]
        require(len(matches) == 1, f"Expected exactly one native {label} icon button")
        node = matches[0]
        require((node.get("enabled") == "true") == enabled, f"Unexpected {label} enabled state")
        left, top, right, bottom = bounds(node)
        if density:
            minimum = 52 * density / 160
            require(right - left >= minimum - 1 and bottom - top >= minimum - 1,
                    f"{label} touch target is smaller than 52dp")
        return node

    def dismiss_initial_instructions(nodes):
        screens = [node for node in nodes if node.get("package") == args.package and
                   node.get("resource-id") == args.package + ":id/instructions_screen"]
        if not screens:
            return
        require(len(screens) == 1 and not report.get("first_run_instructions_dismissed"),
                "Unexpected repeated instructions screen during gameplay checks")
        require(not any(node.get("package") == args.package and
                        node.get("text", "").casefold() in {"how to play", "1", "2"} for node in nodes),
                "First-run guide still has its removed title or numbered panel badges")
        headings = [node for node in nodes if node.get("package") == args.package and
                    node.get("text") in {"Pick free tiles", "Make pairs"}]
        actions = [node for node in nodes if node.get("package") == args.package and
                   node.get("resource-id") == args.package + ":id/instructions_back_button" and
                   node.get("class") == "android.widget.ImageButton" and
                   node.get("content-desc") == "Back to game" and node.get("clickable") == "true"]
        require(len(headings) == 2 and len(actions) == 1,
                "First-run guide lacks its panel headings or Back to game action")
        tap(actions[0])
        report["first_run_instructions_dismissed"] = True
        raise AssertionError("First-run guide dismissed; waiting for a fresh game hierarchy")

    def checked_ui(state, allow_finding=False, nodes=None):
        nodes = ui() if nodes is None else nodes
        dismiss_initial_instructions(nodes)
        remaining, held = replay(state)
        status = "complete" if not remaining and not held else "game over" if len(held) == 4 else "playing"
        require(any(node.get("content-desc", "").startswith("Board status: " + status) for node in nodes),
                "Accessible board status is missing or incorrect")
        require(not any(node.get("text") == "FairyMahjong" or
                        node.get("content-desc", "").startswith("Score:") for node in nodes),
                "Title or cleared-tile counter remains on the game screen")
        require(not any(node.get("class") == "android.widget.TextView" and node.get("text") in
                        {"1", "2", "3", "4"} for node in nodes), "Hand-number labels remain visible")
        if status == "playing":
            require(not any(node.get("text") for node in nodes if node.get("package") == args.package),
                    "Ordinary play still has a persistent text label")
        hint_label = "Finding…" if allow_finding and any(
            node.get("content-desc") == "Finding…" for node in nodes) else "Hint"
        controls = [action(hint_label, nodes, enabled=status == "playing" and hint_label == "Hint"),
                    action("How to play", nodes), action("Switch to landscape and start a new board", nodes),
                    action("New board", nodes)]
        actions = [node for node in nodes if node.get("clickable") == "true"
                   and node.get("package") == args.package and not TILE.match(node.get("content-desc", ""))]
        require(len(actions) == 4, "Expected exactly four main-screen action nodes")
        require(not any(node.get("text") == "Play again" or node.get("content-desc") == "Restart" for node in nodes),
                "Removed Restart menu is still visible")
        available = set(free_tiles(state["positions"], remaining)) if len(held) < 4 else set()
        tiles = {}
        for node in nodes:
            description = node.get("content-desc", "")
            match = TILE.match(description)
            if not match:
                continue
            index = int(match.group(1)) - 1
            require(index in remaining and index not in tiles, "Duplicate or removed board tile")
            require(int(match.group(2)) == len(state["faces"]), "Wrong accessible tile total")
            require(description.startswith(names[state["faces"][index]] + " tile, "), "Wrong fairy identity")
            require((node.get("enabled") == "true") == (index in available), "Wrong native tile pickability")
            bounds(node)
            tiles[index] = node
        require(set(tiles) == remaining, "Remaining board tiles are missing from native UI")
        hand_slots = []
        for slot in range(4):
            name = names[state["faces"][held[slot]]] if slot < len(held) else "empty"
            matches = [node for node in nodes if node.get("content-desc", "").startswith(
                f"Hand slot {slot + 1}: {name}")]
            require(len(matches) == 1, "Missing or duplicate native hand slot identity")
            bounds(matches[0])
            hand_slots.append(matches[0])
        control_rects = [bounds(node) for node in controls]
        hand_rects = [bounds(node) for node in hand_slots]
        board_rects = [bounds(node) for node in tiles.values()]
        top_of_play = min(rect[1] for rect in board_rects + hand_rects)
        require(all(rect[3] <= top_of_play for rect in control_rects),
                "Top controls overlap or sit below the board or hand")
        require(all(left[2] <= right[0] for left, right in zip(control_rects, control_rects[1:])),
                "Top controls overlap each other")
        centers = [(rect[0] + rect[2]) / 2 for rect in control_rects]
        spacing_tolerance = 3 * density / 160 if density else 2
        require(all(abs(center - (centers[0] + (centers[-1] - centers[0]) * index / 3)) <= spacing_tolerance
                    for index, center in enumerate(centers)), "Four top controls are not evenly spaced")
        if board_rects:
            require(max(rect[3] for rect in board_rects) <= min(rect[1] for rect in hand_rects),
                    "Board tiles overlap the hand")
        require(all(left[2] <= right[0] for left, right in zip(hand_rects, hand_rects[1:])),
                "Portrait hand slots overlap or are not ordered left to right")
        require(all(abs((rect[1] + rect[3]) - (hand_rects[0][1] + hand_rects[0][3])) <= 2
                    for rect in hand_rects), "Hand slots do not form one horizontal row")
        require(all(abs((rect[3] - rect[1]) - (rect[2] - rect[0]) * 1.22) <= 2 for rect in hand_rects),
                "Hand tiles are rotated or distorted")
        board_viewports = [node for node in nodes
                           if node.get("resource-id") == args.package + ":id/board_viewport"]
        require(len(board_viewports) == 1, "Portrait board has no unique scrolling viewport")
        viewport = bounds(board_viewports[0])
        require(viewport[3] <= min(rect[1] for rect in hand_rects), "Hand is not below the board")
        display = bounds(nodes[0])
        hand_span = hand_rects[-1][2] - hand_rects[0][0]
        minimum_span = (display[2] - display[0]) * .65
        if density:
            require(all(rect[2] - rect[0] >= 44 * density / 160 - 1 for rect in hand_rects),
                    "Bottom hand tiles are too small")
            minimum_span = min(minimum_span, 390 * density / 160)
        require(hand_span >= minimum_span, "Portrait hand does not use the screen width")
        expect_save(state)
        return nodes, tiles

    def checked_guide(state, nodes=None):
        nodes = ui() if nodes is None else nodes
        require(sum(node.get("resource-id") == args.package + ":id/instructions_screen" for node in nodes) == 1,
                "Missing direct instructions screen")
        require(not any(node.get("package") == args.package and
                        node.get("text", "").casefold() in {"how to play", "1", "2"} for node in nodes),
                "Removed guide title or numbered panel badge remains visible")
        require(not any(TILE.match(node.get("content-desc", "")) or
                        node.get("content-desc", "").startswith(("Hand slot ", "Board status:")) for node in nodes),
                "Guide still exposes the underlying board")
        close = [node for node in nodes if node.get("resource-id") == args.package + ":id/instructions_back_button"
                 and node.get("content-desc") == "Back to game" and node.get("class") == "android.widget.ImageButton"]
        require(len(close) == 1 and close[0].get("clickable") == "true" and not close[0].get("text"),
                "Guide has no unique icon-only Back to game button")
        require(not any(node.get("class") == "android.widget.CheckBox" or
                        node.get("resource-id") == args.package + ":id/instructions_haptics" or
                        node.get("text", "").casefold() == "gentle haptics" for node in nodes),
                "Removed haptics checkbox remains in the guide")
        bounds(close[0])
        expect_save(state)
        return nodes, close[0]

    def open_guide(state, nodes):
        tap(action("How to play", nodes))
        return wait_for(lambda: checked_guide(state), "direct instructions screen")

    def close_guide(state, back=False):
        _, close = checked_guide(state)
        before = command("exec-out", "run-as", args.package, "cat", SAVE_PATHS[0]).stdout
        if back:
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
        else:
            tap(close)
        result = wait_for(lambda: checked_ui(state), "exact game after guide close")
        require(command("exec-out", "run-as", args.package, "cat", SAVE_PATHS[0]).stdout == before,
                "Closing help rewrote the saved game bytes")
        return result

    def hinted_game(state):
        nodes, tiles = checked_ui(state, allow_finding=True)
        markers = sorted(node.get("content-desc") for node in nodes if "Hint:" in node.get("content-desc", ""))
        require(any("Hint: pick next" in marker for marker in markers), "Expected an active safe hint")
        require(any("Hint: matching tile" in marker for marker in markers), "Expected a highlighted held match")
        return nodes, tiles, markers

    def fresh_deal(previous):
        state = saved()
        require(not state["picks"] and state["haptics"] == previous["haptics"] and
                state["orientation"] == previous["orientation"], "Fresh deal changed preferences/orientation or retained picks")
        require(state["positions"] != previous["positions"] or state["faces"] != previous["faces"], "New board reused previous deal")
        counts = Counter(state["faces"])
        require(all(6 <= face <= 61 and count % 2 == 0 for face, count in counts.items()), "New board lacks paired fairy IDs")
        require(max(counts.values()) - min(counts.values()) <= 2, "Unbalanced new-board identity counts")
        nodes, _ = checked_ui(state)
        require(not any("Hint:" in node.get("content-desc", "") for node in nodes), "New board retained old hint markers")
        return state

    def record(label):
        report["checks"].append(label)
        print(f"PASS: {label}", flush=True)

    try:
        require(adb("get-state") == "device", "Device is not authorized")
        adb("shell", "run-as", args.package, "pwd")
        densities = re.findall(r"(?:Physical|Override) density:\s*(\d+)", adb("shell", "wm", "density"))
        density = int(densities[-1]) if densities else None
        report["density_dpi"] = density
        originally_running = bool(adb("shell", "pidof", args.package, check=False))
        stop()
        adb("shell", "run-as", args.package, "mkdir", "-p", "files", "shared_prefs")
        for path in RESTORED_PATHS:
            exists = command("shell", "run-as", args.package, "test", "-f", path, check=False)
            if exists.returncode == 0:
                backup[path] = command("exec-out", "run-as", args.package, "cat", path).stdout
            else:
                require(exists.returncode == 1, f"Cannot inspect original private file {path}")
        backed_up = True
        for path, data in backup.items():
            (output / (Path(path).name + ".original")).write_bytes(data)
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")
        inject(initial)
        nodes, tiles = wait_for(lambda: checked_ui(initial), "quiet meadow fixture")
        screenshot("01-quiet-meadow.png")
        nodes, tiles = checked_ui(initial)
        for node in nodes:
            description = node.get("content-desc", "")
            if description in {"Hint", "How to play", "New board"} or description.startswith("Hand slot ") or TILE.match(description):
                bounds(node)
        record("Four evenly spaced 52dp actions sit above the board; upright hand slots form a full-width bottom row; no title, counter, routine text, or hand numbers")

        require(tiles[1].get("enabled") == "false", "Blocked-tap fixture is not blocked")
        tap(tiles[1], allow_disabled=True)
        nodes, tiles = wait_for(lambda: checked_ui(initial), "unchanged board after blocked tap")
        record("Tapping a visible side-blocked tile leaves the deal, hand, and picks unchanged")

        blocked_node = tiles[1]
        tap(tiles[0])
        held = dict(initial, picks=[0])
        wait_for(lambda: expect_save(held), "held fairy save")
        nodes, tiles = wait_for(lambda: checked_ui(held), "held fairy identity")
        require(tiles[1].get("enabled") == "true", "Removing the left neighbor did not unlock the tile")
        screenshot("02-unblocked-same-tile.png")
        unchanged_tile_colors("01-quiet-meadow.png", "02-unblocked-same-tile.png", blocked_node, tiles[1])
        record("The same tile keeps its original colors when it changes from blocked to available")
        tap(action("Hint", nodes))

        def matching_hint():
            nodes, tiles = checked_ui(held, allow_finding=True)
            next_nodes = [node for node in nodes if TILE.match(node.get("content-desc", ""))
                          and "Hint: pick next" in node.get("content-desc", "")]
            require(len(next_nodes) == 1 and next_nodes[0] is tiles[3], "Hint must select the unique safe held match")
            require(any(node.get("content-desc", "").startswith("Hand slot 1: Teal Spell")
                        and "Hint: matching tile" in node.get("content-desc", "") for node in nodes),
                    "Held matching fairy has no accessible highlight")
            return nodes, tiles

        nodes, tiles = wait_for(matching_hint, "held matching hint")
        screenshot("02-held-matching-hint.png")
        tap(tiles[3])
        matched = dict(initial, picks=[0, 3])
        wait_for(lambda: expect_save(matched), "matched pair save")
        nodes, tiles = wait_for(lambda: checked_ui(matched), "empty hand after match")
        require(not any("Hint:" in node.get("content-desc", "") for node in nodes), "Matched hint remains active")
        record("Held fairy retains identity; Hint highlights the identical board tile and hand; matching clears both and the hint")

        tap(tiles[1])
        progressed = dict(initial, picks=[0, 3, 1])
        wait_for(lambda: expect_save(progressed), "progress for direct-help checks")
        nodes, _ = wait_for(lambda: checked_ui(progressed), "progressed hand")
        tap(action("Hint", nodes))
        nodes, _, markers = wait_for(lambda: hinted_game(progressed), "held hint before direct help")
        open_guide(progressed, nodes)
        screenshot("03-direct-help.png")
        nodes, _ = close_guide(progressed)
        require(hinted_game(progressed)[2] == markers, "Direct help changed the active hint")
        record("Direct How to play and its icon close preserve exact saved bytes, nonempty hand, and active hint")

        open_guide(progressed, nodes)
        close_guide(progressed, back=True)
        require(hinted_game(progressed)[2] == markers, "Reopening help or Android Back changed the active hint")
        stop()
        launch()
        nodes, _ = wait_for(lambda: checked_ui(progressed), "progress after process recreation")
        open_guide(progressed, nodes)
        nodes, _ = close_guide(progressed)
        record("Guide stays free of haptics checkboxes; Android Back and process recreation preserve the exact deal, nonempty hand, and legacy save")
        tap(action("Hint", nodes))
        nodes, _, _ = wait_for(lambda: hinted_game(progressed), "active hint before new board")
        tap(action("New board", nodes))
        real = wait_for(lambda: fresh_deal(progressed), "direct fresh fairy board")
        screenshot("04-new-fairy-board.png")
        (output / "new-board.json").write_text(json.dumps(real, indent=2) + "\n", encoding="utf-8")
        record("New board immediately creates a different paired fairy deal, clears the hand and active hint, and preserves orientation and legacy save fields without a popup")

        for fixture_path in shape_fixtures:
            state = json.loads(fixture_path.read_text(encoding="utf-8-sig"))
            state = dict(state, version=5, orientation="portrait")
            require(not state["picks"], "Shape fixture must be an untouched board")
            replay(state)
            coordinates = set(map(tuple, state["positions"]))
            xs = [point[0] for point in coordinates]
            ys = [point[1] for point in coordinates]
            columns = 1 + (max(xs) - min(xs)) / 2
            rows = 1 + (max(ys) - min(ys)) / 2
            require(max(xs) - min(xs) <= 12, "Shape is wider than seven tile columns")
            if args.vertical_shapes:
                require(rows >= 10, "Vertical shape is shorter than ten tile rows")
                require(rows * 7 >= columns * 10, "Vertical shape is too wide for the required 10:7 height:width ratio")
            require({(min(xs) + max(xs) - x, y, z) for x, y, z in coordinates} == coordinates,
                    "Shape is not symmetric left to right")
            require({(x, min(ys) + max(ys) - y, z) for x, y, z in coordinates} == coordinates,
                    "Shape is not symmetric top to bottom")
            inject(state)
            _, tiles = wait_for(lambda: checked_ui(state), "native " + fixture_path.stem + " layout")
            tile_rects = [bounds(node) for node in tiles.values()]
            board_rectangle = [min(rect[0] for rect in tile_rects), min(rect[1] for rect in tile_rects),
                               max(rect[2] for rect in tile_rects), max(rect[3] for rect in tile_rects)]
            report["shapes"].append({"fixture": fixture_path.name, "layout": state["layout"],
                                     "columns": columns, "rows": rows, "tile_count": len(coordinates),
                                     "height_to_width": rows / columns,
                                     "native_board_bounds": board_rectangle})
            screenshot("shape-" + fixture_path.stem + ".png")
            record(f"{fixture_path.stem} is {columns:g} columns by {rows:g} rows, symmetric on both axes, "
                   "at most seven columns wide, and fits between the top controls and bottom hand")

        if args.end_states:
            for label, picks in (("complete", [0, 3, 1, 2, 4, 7, 5, 6]), ("full-hand", [0, 4, 1, 5])):
                state = dict(initial, picks=picks)
                replay(state)
                inject(state)
                nodes, _ = wait_for(lambda: checked_ui(state), label + " presentation")
                notices = [node for node in nodes if node.get("package") == args.package and node.get("text")]
                require(notices, "Terminal board has no visible explanation")
                hand_rects = [bounds(node) for node in nodes
                              if node.get("content-desc", "").startswith("Hand slot ")]
                for notice in notices:
                    rectangle = bounds(notice)
                    require(all(rectangle[2] <= hand[0] or hand[2] <= rectangle[0] or
                                rectangle[3] <= hand[1] or hand[3] <= rectangle[1] for hand in hand_rects),
                            "Terminal explanation overlaps a hand tile")
                screenshot("05-" + label + ".png")
                open_guide(state, nodes)
                nodes, _ = close_guide(state)
                tap(action("New board", nodes))
                wait_for(lambda: fresh_deal(state), "direct new board after " + label)
                record(label + " exposes its status and explanation, disables Hint, keeps direct help available, and starts a new deal directly")
        report["status"] = "passed"
    except Exception as error:
        report["status"], report["error"] = "failed", str(error)
        # Keep evidence of the failing screen before finally restores the user's save.
        for label, capture in (
            ("screenshot", lambda: screenshot("failure.png")),
            ("hierarchy", lambda: (output / "failure-ui.xml").write_text(ui_xml(), encoding="utf-8")),
        ):
            try:
                capture()
            except Exception as diagnostic_error:
                report.setdefault("diagnostic_errors", {})[label] = str(diagnostic_error)
        raise
    finally:
        try:
            if backed_up:
                stop()
                adb("shell", "run-as", args.package, "rm", "-f", *RESTORED_PATHS)
                for path, data in backup.items():
                    write_bytes(path, data)
                    require(command("exec-out", "run-as", args.package, "cat", path).stdout == data,
                            f"Original private file bytes were not restored for {path}")
                for path in set(RESTORED_PATHS) - set(backup):
                    require(command("shell", "run-as", args.package, "test", "-f", path, check=False).returncode == 1,
                            f"Unexpected restored private file {path}")
                report["original_save_restored"] = True
                report["original_preferences_restored"] = True
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
