#!/usr/bin/env python3
"""Check button-controlled orientation on one explicitly selected debug device.

Backs up and restores the exact save, AtomicFile sidecars, and instructions
preferences, plus any modified
Android rotation settings and emulator acceleration vector. Checks v4 migration,
fresh deals on orientation changes, held tiles, hints, direct help/new board, process
recreation, and native landscape fixture layouts. Requires a dedicated test AVD;
--skip-sensor-checks omits sensor and rotation-setting changes on that emulator.
Screenshots require visual review. Always reopens the app after an accepted run.
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
from artwork_smoke_test import catalog_names
from hint_smoke_test import TILE
from save_migration_smoke_test import RECOVERY_PREFIX, SAVE_PATHS, free_tiles, replay, require


PREFERENCE_PATHS = ("shared_prefs/instructions.xml", "shared_prefs/instructions.xml.bak")
RESTORED_PATHS = SAVE_PATHS + PREFERENCE_PATHS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", type=Path, default=Path("artifacts/orientation-smoke-test"))
    parser.add_argument("--shape-fixtures", type=Path,
                        help="Directory of generated landscape v4 or v5 saves")
    parser.add_argument("--skip-sensor-checks", action="store_true")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package name")
    fixtures = sorted(args.shape_fixtures.glob("*.json")) if args.shape_fixtures else []
    require(args.shape_fixtures is None or fixtures, "No landscape fixtures found")
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    prefix = [args.adb, "-s", args.serial]
    require_test_emulator(prefix)
    names = catalog_names()
    report = {"status": "running", "serial": args.serial, "checks": [], "screenshots": [],
              "shapes": [], "original_save_restored": False, "original_preferences_restored": False,
              "rotation_state_restored": False,
              "screenshots_require_visual_review": True}
    backup, rotation_settings = {}, {}
    backed_up = rotation_modified = False
    original_acceleration = None
    density = 1.0
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
            time.sleep(.2)
        raise AssertionError(f"Timed out waiting for {label}; last error: {last}")

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
        require(state["version"] == 5, "App did not persist v5")
        return state

    def exact(state):
        require(saved() == state, "Geometry, faces, picks, haptics, or orientation changed unexpectedly")
        return state

    def inject(state):
        stop()
        clear_saves()
        write_bytes(SAVE_PATHS[0], json.dumps(state).encode("utf-8"))
        launch()

    def ui_xml():
        remote = "/data/local/tmp/fairymahjong-orientation-ui.xml"
        adb("shell", "rm", "-f", remote)
        adb("shell", "uiautomator", "dump", remote)
        return adb("shell", "cat", remote)

    def ui():
        return list(ET.fromstring(ui_xml()).iter("node"))

    def bounds(node):
        rectangle = [int(value) for value in re.findall(r"-?\d+", node.get("bounds", ""))]
        require(len(rectangle) == 4, "Native node has no bounds")
        require(rectangle[2] > rectangle[0] and rectangle[3] > rectangle[1], "Empty native bounds")
        return rectangle

    def tap(node):
        require(node.get("enabled") == "true", "Refusing to tap a disabled node")
        left, top, right, bottom = bounds(node)
        adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))

    def screenshot(name, orientation=None):
        data = command("exec-out", "screencap", "-p").stdout
        require(data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24, "Screenshot is not a PNG")
        width, height = struct.unpack(">II", data[16:24])
        if orientation:
            require((width > height) == (orientation == "landscape"), "Screenshot has wrong native orientation")
        (output / name).write_bytes(data)
        report["screenshots"].append({"file": name, "width": width, "height": height})

    def button(label, nodes):
        found = [node for node in nodes if node.get("content-desc") == label and
                 node.get("class") == "android.widget.ImageButton" and node.get("clickable") == "true"]
        require(len(found) == 1, "Missing or duplicate " + label)
        return found[0]

    def next_orientation(mode):
        return "portrait" if mode == "landscape" else "landscape"

    def rotate_label(mode):
        return "Switch to " + next_orientation(mode) + " and start a new board"

    def dismiss_initial_instructions(nodes):
        screens = [node for node in nodes if node.get("package") == args.package and
                   node.get("resource-id") == args.package + ":id/instructions_screen"]
        if not screens:
            return
        require(len(screens) == 1 and not report.get("first_run_instructions_dismissed"),
                "Unexpected repeated instructions screen during gameplay checks")
        require(not any(node.get("package") == args.package and
                        node.get("text") in ("How to play", "1", "2") for node in nodes),
                "Removed guide title or panel numbers remain visible")
        actions = [node for node in nodes if node.get("package") == args.package and
                   node.get("resource-id") == args.package + ":id/instructions_back_button" and
                   node.get("class") == "android.widget.ImageButton" and
                   node.get("content-desc") == "Back to game" and node.get("clickable") == "true"]
        require(len(actions) == 1, "First-run guide lacks its Back to game action")
        tap(actions[0])
        report["first_run_instructions_dismissed"] = True
        raise AssertionError("First-run guide dismissed; waiting for a fresh game hierarchy")

    def checked_ui(state, allow_hint=False, nodes=None):
        nodes = ui() if nodes is None else nodes
        exact(state)
        dismiss_initial_instructions(nodes)
        remaining, held = replay(state)
        mode = state["orientation"]
        viewport = bounds(nodes[0])
        width, height = viewport[2] - viewport[0], viewport[3] - viewport[1]
        require((width > height) == (mode == "landscape"), "Native hierarchy has the wrong orientation")
        require(any(node.get("content-desc", "").startswith("Board status: playing") for node in nodes),
                "Missing accessible board status")
        labels = ["Hint", "How to play", rotate_label(mode), "New board"]
        if allow_hint and any(node.get("content-desc") == "Finding…" for node in nodes):
            labels[0] = "Finding…"
        controls = [button(label, nodes) for label in labels]
        require(controls[2].get("enabled") == "true", "Rotation transition has not settled")
        actions = [node for node in nodes if node.get("package") == args.package and
                   node.get("clickable") == "true" and not TILE.match(node.get("content-desc", ""))]
        require(len(actions) == 4, "Expected exactly Hint, How to play, orientation, and New board controls")
        require(not any(node.get("text") == "Play again" or node.get("content-desc") == "Restart" for node in nodes),
                "Removed Restart menu is still visible")
        require(not any(node.get("text") == "FairyMahjong" or
                        node.get("content-desc", "").startswith("Score:") for node in nodes),
                "Persistent title or counter is visible")
        available = set(free_tiles(state["positions"], remaining))
        tiles = {}
        for node in nodes:
            description = node.get("content-desc", "")
            match = TILE.match(description)
            if match:
                index = int(match.group(1)) - 1
                require(index in remaining and index not in tiles, "Removed or duplicate board tile")
                require(description.startswith(names[state["faces"][index]] + " tile, "), "Wrong fairy identity")
                require((node.get("enabled") == "true") == (index in available), "Wrong tile pickability")
                tiles[index] = node
        require(set(tiles) == remaining, "Some board tiles are outside the visible native layout")
        hand = []
        for slot in range(4):
            name = names[state["faces"][held[slot]]] if slot < len(held) else "empty"
            found = [node for node in nodes if node.get("content-desc", "").startswith(f"Hand slot {slot + 1}: {name}")]
            require(len(found) == 1, "Missing or incorrect hand slot")
            hand.append(found[0])
        controls_rects, hand_rects, tile_rects = ([bounds(node) for node in group]
                                               for group in (controls, hand, tiles.values()))
        for rectangle in controls_rects + hand_rects + tile_rects:
            require(viewport[0] <= rectangle[0] < rectangle[2] <= viewport[2] and
                    viewport[1] <= rectangle[1] < rectangle[3] <= viewport[3], "Native content is clipped")
        require(all(rect[2] - rect[0] >= 52 * density - 1 and rect[3] - rect[1] >= 52 * density - 1
                    for rect in controls_rects), "Control touch target is smaller than52dp")
        require(all(abs((rect[3] - rect[1]) - (rect[2] - rect[0]) * 1.22) <= 2 for rect in hand_rects),
                "Hand tiles are rotated or distorted")
        if mode == "portrait":
            require(all(left[2] <= right[0] for left, right in zip(controls_rects, controls_rects[1:])),
                    "Top controls overlap or are not ordered Hint, How to play, Rotate, New board")
            centers = [(rect[0] + rect[2]) / 2 for rect in controls_rects]
            require(all(abs(center - (centers[0] + (centers[-1] - centers[0]) * index / 3)) <= 3 * density
                        for index, center in enumerate(centers)), "Portrait controls are not evenly spaced")
            require(all(abs((rect[1] + rect[3]) - (controls_rects[0][1] + controls_rects[0][3])) <= 2
                        for rect in controls_rects), "Portrait controls do not form one horizontal row")
            require(max(rect[3] for rect in controls_rects) <= min(rect[1] for rect in tile_rects + hand_rects),
                    "Board or hand overlaps top controls")
            require(all(left[2] <= right[0] for left, right in zip(hand_rects, hand_rects[1:])),
                    "Portrait hand slots overlap or are not ordered left to right")
            require(all(abs((rect[1] + rect[3]) - (hand_rects[0][1] + hand_rects[0][3])) <= 2
                        for rect in hand_rects), "Portrait hand slots do not form one horizontal row")
            board_viewports = [node for node in nodes
                               if node.get("resource-id") == args.package + ":id/board_viewport"]
            require(len(board_viewports) == 1, "Portrait board has no unique scrolling viewport")
            board_viewport = bounds(board_viewports[0])
            require(board_viewport[3] <= min(rect[1] for rect in hand_rects),
                    "Portrait hand is not clear of the bottom of the board")
            hand_span = hand_rects[-1][2] - hand_rects[0][0]
            require(hand_span >= min(width * .65, 390 * density), "Portrait hand does not use the screen width")
        else:
            require(all(upper[3] <= lower[1] for upper, lower in zip(controls_rects, controls_rects[1:])),
                    "Landscape controls overlap or are not ordered Hint, How to play, Rotate, New board from top to bottom")
            require(all(abs((rect[0] + rect[2]) - (controls_rects[0][0] + controls_rects[0][2])) <= 2
                        for rect in controls_rects), "Landscape controls do not form one vertical column")
            centers = [(rect[1] + rect[3]) / 2 for rect in controls_rects]
            require(all(abs(center - (centers[0] + (centers[-1] - centers[0]) * index / 3)) <= 3 * density
                        for index, center in enumerate(centers)), "Landscape controls are not evenly spaced")
            control_column_center_y = (centers[0] + centers[-1]) / 2
            require(controls_rects[-1][3] - controls_rects[0][1] >= height * .8,
                    "Landscape controls do not span the available screen height")
            require(max(rect[2] for rect in controls_rects) <= min(rect[0] for rect in tile_rects + hand_rects),
                    "Landscape board or hand overlaps the left control column")
            if not state["picks"]:
                board_center_y = (min(rect[1] for rect in tile_rects) + max(rect[3] for rect in tile_rects)) / 2
                require(abs(board_center_y - control_column_center_y) <= 3 * density,
                        "Landscape board is not centered in the full height beside the controls")
            require(all(upper[3] <= lower[1] for upper, lower in zip(hand_rects, hand_rects[1:])),
                    "Landscape hand slots overlap or are not ordered top to bottom")
            require(all(abs((rect[0] + rect[2]) - (hand_rects[0][0] + hand_rects[0][2])) <= 2
                        for rect in hand_rects), "Landscape hand slots do not form one vertical column")
            require(max(rect[2] for rect in tile_rects) <= min(rect[0] for rect in hand_rects),
                    "Landscape hand is not clear of the right side of the board")
            require(all(rect[2] - rect[0] >= 44 * density - 1 for rect in hand_rects),
                    "Landscape hand tiles are too small")
            require(not any(node.get("resource-id") == args.package + ":id/board_viewport" for node in nodes),
                    "Landscape board unexpectedly uses the portrait scrolling viewport")
        return nodes, tiles, viewport

    def check_pool(state):
        xs, ys = ([point[axis] for point in state["positions"]] for axis in (0, 1))
        columns, rows = 1 + (max(xs) - min(xs)) / 2, 1 + (max(ys) - min(ys)) / 2
        if state["orientation"] == "portrait":
            require(6 <= columns <= 7 and 8 <= rows <= 10 and rows / columns <= 1.6,
                    "New portrait deal has the wrong bounded footprint")
        else:
            require(rows < columns <= 12 and rows == 4, "New landscape deal exceeds twelve columns or has the wrong footprint")
        coordinates = set(map(tuple, state["positions"]))
        require(len(coordinates) >= 64, "New deal is a tiny board")
        require({(min(xs) + max(xs) - x, y, z) for x, y, z in coordinates} == coordinates and
                {(x, min(ys) + max(ys) - y, z) for x, y, z in coordinates} == coordinates,
                "Deal is not symmetric along both axes")
        counts = Counter(state["faces"])
        require(all(6 <= face < 62 and count % 2 == 0 for face, count in counts.items()) and
                max(counts.values()) - min(counts.values()) <= 2, "Unbalanced fairy pairs")
        return columns, rows

    def fresh(previous, mode):
        state = saved()
        require(state["orientation"] == mode and state["haptics"] == previous["haptics"] and
                not state["picks"], "Fresh orientation deal retained picks or changed preference")
        require(state["positions"] != previous["positions"] or state["faces"] != previous["faces"],
                "Orientation/New board reused the previous deal")
        check_pool(state)
        nodes, _, _ = checked_ui(state)
        require(not any("Hint:" in node.get("content-desc", "") for node in nodes), "Old hint survived a new deal")
        return state

    def rotate(state):
        nodes, _, _ = checked_ui(state)
        tap(button(rotate_label(state["orientation"]), nodes))
        return wait_for(lambda: fresh(state, next_orientation(state["orientation"])), "new orientation deal")

    def help_round_trip(state):
        nodes, _, _ = checked_ui(state)
        markers = sorted(node.get("content-desc") for node in nodes if "Hint:" in node.get("content-desc", ""))
        tap(button("How to play", nodes))

        def guide():
            nodes = ui()
            exact(state)
            require(any(node.get("resource-id") == args.package + ":id/instructions_screen" for node in nodes),
                    "Direct help did not open the instructions screen")
            found = [node for node in nodes if node.get("resource-id") == args.package + ":id/instructions_back_button"
                     and node.get("class") == "android.widget.ImageButton" and node.get("content-desc") == "Back to game"]
            require(len(found) == 1, "Guide has no unique Back to game icon button")
            return found[0]

        tap(wait_for(guide, "direct help screen"))
        nodes, _, _ = wait_for(lambda: checked_ui(state), "exact game after guide icon close")
        require(markers == sorted(node.get("content-desc") for node in nodes if "Hint:" in node.get("content-desc", "")),
                "Direct help changed existing hint markers")
        return nodes

    def acceleration():
        response = adb("emu", "sensor", "get", "acceleration")
        match = re.search(r"acceleration\s*=\s*([-+\d.eE]+):([-+\d.eE]+):([-+\d.eE]+)", response)
        require(match, "Cannot read emulator acceleration vector: " + response)
        return tuple(float(value) for value in match.groups())

    def set_acceleration(vector):
        response = adb("emu", "sensor", "set", "acceleration", ":".join(format(value, ".9g") for value in vector))
        require("KO:" not in response, "Emulator rejected sensor vector")
        actual = acceleration()
        require(all(abs(a - b) < .01 for a, b in zip(actual, vector)), "Emulator did not apply acceleration")

    def sensor_check(state):
        nonlocal rotation_modified
        rotation_modified = True
        adb("shell", "settings", "put", "system", "accelerometer_rotation", "1")
        require(adb("shell", "settings", "get", "system", "accelerometer_rotation") == "1", "Auto-rotate is not enabled")
        for vector in ((9.8, 0, .5), (0, 9.8, .5)):
            set_acceleration(vector)
            # Android's orientation sensor needs a stable sample to settle.
            time.sleep(1.2)
            checked_ui(state)
        record("Verified physical sensor rotations with Android auto-rotate enabled preserve the exact " + state["orientation"] + " deal")

    def record(label):
        report["checks"].append(label)
        print("PASS: " + label, flush=True)

    try:
        require(adb("get-state") == "device", "Device is not authorized")
        adb("shell", "run-as", args.package, "pwd")
        values = re.findall(r"(?:Physical|Override) density:\s*(\d+)", adb("shell", "wm", "density"))
        require(values, "Cannot read display density")
        density = int(values[-1]) / 160
        report["density_dpi"] = int(values[-1])
        if not args.skip_sensor_checks:
            for key in ("accelerometer_rotation", "user_rotation"):
                value = adb("shell", "settings", "get", "system", key)
                require(value == "null" or re.fullmatch(r"-?\d+", value), "Unexpected rotation setting")
                rotation_settings[key] = value
            original_acceleration = acceleration()
            report["original_rotation_settings"] = rotation_settings.copy()
            report["original_acceleration"] = original_acceleration
        stop()
        adb("shell", "run-as", args.package, "mkdir", "-p", "files", "shared_prefs")
        for path in RESTORED_PATHS:
            exists = command("shell", "run-as", args.package, "test", "-f", path, check=False)
            if exists.returncode == 0:
                backup[path] = command("exec-out", "run-as", args.package, "cat", path).stdout
            else:
                require(exists.returncode == 1, "Cannot inspect private file " + path)
        backed_up = True
        for path, data in backup.items():
            (output / (Path(path).name + ".original")).write_bytes(data)
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")
        legacy = {"version": 4, "layout": "orientation-migration", "positions":
                  [[column * 2, row * 2, 0] for row in range(2) for column in range(4)],
                  "faces": [6, 23, 23, 6, 44, 61, 61, 44], "picks": [0], "haptics": False}
        migrated = dict(legacy, version=5, orientation="portrait")
        inject(legacy)

        def migrated_ready():
            nodes = ui()
            # A fresh Android device presents this one-time system-owned lesson.
            # Recognize both exact nodes; never dismiss any other overlay/dialog.
            titles = [node for node in nodes if node.get("package") == "com.android.systemui" and
                      node.get("resource-id") == "com.android.systemui:id/immersive_cling_title" and
                      node.get("text") == "Viewing full screen"]
            if titles:
                require(len(titles) == 1, "Unexpected full-screen lesson hierarchy")
                buttons = [node for node in nodes if node.get("package") == "com.android.systemui" and
                           node.get("resource-id") == "com.android.systemui:id/ok" and
                           node.get("text") == "Got it" and node.get("clickable") == "true"]
                require(len(buttons) == 1, "Full-screen lesson lacks its exact Got it action")
                if not report.get("fullscreen_onboarding_dismissed"):
                    tap(buttons[0])
                    report["fullscreen_onboarding_dismissed"] = True
                raise AssertionError("Waiting for the acknowledged full-screen lesson to disappear")
            return checked_ui(migrated, nodes=nodes)

        nodes, _, _ = wait_for(migrated_ready, "v4 progress migrated to portrait v5")
        screenshot("01-migrated-portrait.png", "portrait")
        record("v4 migrates to v5 portrait preserving exact geometry, faces, nonempty hand, and haptics")
        tap(button("Hint", nodes))

        def highlighted():
            nodes, _, _ = checked_ui(migrated, allow_hint=True)
            require(any("Hint: pick next" in node.get("content-desc", "") for node in nodes), "Hint not highlighted")
            require(any("Hint: matching tile" in node.get("content-desc", "") for node in nodes), "Held match not highlighted")
            return nodes

        wait_for(highlighted, "safe held-match hint")
        landscape = rotate(migrated)
        screenshot("02-new-landscape.png", "landscape")
        record("Orientation button changes native orientation, deals a wide board, and clears the hand and old hint while preserving haptics")
        nodes, tiles, _ = checked_ui(landscape)
        selected = free_tiles(landscape["positions"], set(tiles))[0]
        tap(tiles[selected])
        progressed = dict(landscape, picks=[selected])
        wait_for(lambda: checked_ui(progressed), "held landscape tile")
        if not args.skip_sensor_checks:
            sensor_check(progressed)
        stop()
        launch()
        wait_for(lambda: checked_ui(progressed), "landscape process recreation")
        screenshot("03-landscape-reloaded.png", "landscape")
        record("Process recreation retains landscape orientation, exact deal, picked tile, hand, and preference")
        nodes = help_round_trip(progressed)
        record("Direct How to play and its icon close preserve the exact landscape deal and nonempty hand")
        tap(button("New board", nodes))
        current = wait_for(lambda: fresh(progressed, "landscape"), "direct new landscape deal")
        record("New board immediately creates a different landscape deal and clears the hand/hint without a popup or preference change")
        for fixture_path in fixtures:
            current = dict(json.loads(fixture_path.read_text(encoding="utf-8-sig")), version=5, orientation="landscape")
            replay(current)
            require(not current["picks"], "Landscape fixture is not an untouched deal")
            columns, rows = check_pool(current)
            inject(current)
            nodes, tiles, viewport = wait_for(lambda: checked_ui(current), "landscape " + fixture_path.stem)
            rectangles = [bounds(node) for node in tiles.values()]
            hand_rectangles = [bounds(node) for node in nodes if node.get("content-desc", "").startswith("Hand slot ")]
            hand_left = min(rect[0] for rect in hand_rectangles)
            control_rectangles = [bounds(button(label, nodes)) for label in
                                  ("Hint", "How to play", rotate_label("landscape"), "New board")]
            control_right = max(rect[2] for rect in control_rectangles)
            available_height = max(rect[3] for rect in control_rectangles) - min(rect[1] for rect in control_rectangles)
            minimum_width = min(rect[2] - rect[0] for rect in rectangles) / density
            can_fit_40dp = ((hand_left - control_right) / density - 32 >= columns * 40 and
                            available_height / density - 8 >= rows * 40 * 1.22)
            if can_fit_40dp:
                require(minimum_width >= 40 - 1 / density, "Landscape tiles are below40dp despite adequate space")
            report["shapes"].append({"fixture": fixture_path.name, "columns": columns, "rows": rows,
                                     "tiles": len(rectangles), "smallest_tile_width_dp": minimum_width,
                                     "room_for_40dp_tiles": can_fit_40dp, "viewport": viewport,
                                     "hand": hand_rectangles, "controls": control_rectangles})
            screenshot("shape-" + fixture_path.stem + ".png", "landscape")
            record(f"{fixture_path.stem}: {columns:g}×{rows:g}, all tiles visible and pickability correct, no controls/hand overlap")
        portrait = rotate(current)
        screenshot("04-new-portrait.png", "portrait")
        record("Toggling back creates a fresh tall portrait deal with an empty hand and no surviving hint")
        if not args.skip_sensor_checks:
            sensor_check(portrait)
        for label, invalid_orientation in (("missing", None), ("nonstring", 1), ("unknown", "sideways")):
            invalid = dict(portrait, layout="orientation-invalid-" + label,
                           picks=[free_tiles(portrait["positions"], set(range(len(portrait["faces"]))))[0]])
            if label == "missing":
                del invalid["orientation"]
            else:
                invalid["orientation"] = invalid_orientation
            inject(invalid)

            def recovered():
                state = saved()
                require(state["orientation"] == "portrait" and not state["picks"],
                        "Invalid orientation did not recover to an empty portrait deal")
                require(state["layout"] != invalid["layout"] and
                        (state["positions"] != invalid["positions"] or state["faces"] != invalid["faces"]),
                        "Invalid orientation was silently repaired instead of replacing the deal")
                check_pool(state)
                nodes, _, _ = checked_ui(state)
                require(any(node.get("text", "").startswith(RECOVERY_PREFIX) for node in nodes),
                        "Invalid orientation has no visible save recovery notice")
                return state

            wait_for(recovered, label + " v5 orientation recovery")
            screenshot("recovery-" + label + ".png", "portrait")
            record("v5 " + label + " orientation is rejected; fresh portrait deal, empty hand, and recovery notice are present")
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
            if rotation_modified:
                set_acceleration(original_acceleration)
                for key, value in rotation_settings.items():
                    if value == "null":
                        adb("shell", "settings", "delete", "system", key)
                    else:
                        adb("shell", "settings", "put", "system", key, value)
                    require(adb("shell", "settings", "get", "system", key) == value, "Rotation setting was not restored")
            report["rotation_state_restored"] = True
        except Exception as error:
            restoration_errors.append("Rotation: " + str(error))
        try:
            if backed_up:
                stop()
                adb("shell", "run-as", args.package, "rm", "-f", *RESTORED_PATHS)
                for path, data in backup.items():
                    write_bytes(path, data)
                    require(command("exec-out", "run-as", args.package, "cat", path).stdout == data,
                            "Original private file was not restored: " + path)
                for path in set(RESTORED_PATHS) - set(backup):
                    require(command("shell", "run-as", args.package, "test", "-f", path, check=False).returncode == 1,
                            "Unexpected restored private file: " + path)
                report["original_save_restored"] = True
                report["original_preferences_restored"] = True
        except Exception as error:
            restoration_errors.append("Save: " + str(error))
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
