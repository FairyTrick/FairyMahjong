#!/usr/bin/env python3
"""Verify readable portrait boards and scrolling on a dedicated test emulator.

Requires exact, untouched v5 portrait saves in --shape-fixtures. Checks every
shape above the horizontal bottom hand, then the tallest shape in short and narrow windows.
Restores original saves, instructions preferences, display size and density.
Never permits the user's play AVD, regardless of its emulator port number.
Screenshots still require visual review.
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
from artwork_smoke_test import catalog_names
from hint_smoke_test import TILE
from save_migration_smoke_test import SAVE_PATHS, free_tiles, replay, require


PREFERENCE_PATHS = ("shared_prefs/instructions.xml", "shared_prefs/instructions.xml.bak")
RESTORED_PATHS = SAVE_PATHS + PREFERENCE_PATHS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", type=Path, default=Path("artifacts/board-size-smoke-test"))
    parser.add_argument("--shape-fixtures", type=Path)
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package name")
    prefix = [args.adb, "-s", args.serial]
    require_test_emulator(prefix)
    require(args.shape_fixtures, "--shape-fixtures is required after test emulator verification")
    fixtures = sorted(args.shape_fixtures.glob("*.json"))
    require(fixtures, "No portrait fixtures found")
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    names = catalog_names()
    report = {"status": "running", "serial": args.serial, "checks": [], "shapes": [],
              "screenshots": [], "original_save_restored": False,
              "original_preferences_restored": False, "display_state_restored": False,
              "screenshots_require_visual_review": True}
    backup, display = {}, {}
    backed_up = display_modified = False
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

    def save_bytes():
        return command("exec-out", "run-as", args.package, "cat", SAVE_PATHS[0]).stdout

    def inject(state):
        stop()
        adb("shell", "run-as", args.package, "rm", "-f", *SAVE_PATHS)
        write_bytes(SAVE_PATHS[0], json.dumps(state).encode("utf-8"))
        launch()

    def ui_xml():
        remote = "/data/local/tmp/fairymahjong-board-size-ui.xml"
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

    def screenshot(name):
        data = command("exec-out", "screencap", "-p").stdout
        require(data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24, "Screenshot is not a PNG")
        width, height = struct.unpack(">II", data[16:24])
        require(height > width, "Native display is not portrait")
        (output / name).write_bytes(data)
        report["screenshots"].append({"file": name, "width": width, "height": height})

    def button(label, nodes):
        found = [node for node in nodes if node.get("content-desc") == label and
                 node.get("class") == "android.widget.ImageButton" and node.get("clickable") == "true"]
        require(len(found) == 1, "Missing or duplicate " + label)
        return found[0]

    def dismiss_onboarding(nodes):
        screens = [node for node in nodes if node.get("resource-id") == args.package + ":id/instructions_screen"]
        if screens:
            require(len(screens) == 1 and not report.get("first_run_instructions_dismissed"),
                    "Unexpected repeated instructions screen")
            targets = [node for node in nodes if node.get("resource-id") == args.package + ":id/instructions_back_button"
                       and node.get("content-desc") == "Back to game" and node.get("class") == "android.widget.ImageButton"
                       and node.get("clickable") == "true"]
            require(len(targets) == 1, "Guide lacks its exact Back to game action")
            tap(targets[0])
            report["first_run_instructions_dismissed"] = True
            raise AssertionError("Guide dismissed; waiting for game hierarchy")
        titles = [node for node in nodes if node.get("package") == "com.android.systemui" and
                  node.get("resource-id") == "com.android.systemui:id/immersive_cling_title" and
                  node.get("text") == "Viewing full screen"]
        if titles:
            targets = [node for node in nodes if node.get("package") == "com.android.systemui" and
                       node.get("resource-id") == "com.android.systemui:id/ok" and
                       node.get("text") == "Got it" and node.get("clickable") == "true"]
            require(len(titles) == 1 and len(targets) == 1, "Unexpected full-screen lesson hierarchy")
            if not report.get("fullscreen_onboarding_dismissed"):
                tap(targets[0])
                report["fullscreen_onboarding_dismissed"] = True
            raise AssertionError("Waiting for full-screen lesson to disappear")

    def within(rectangle, container):
        return (container[0] <= rectangle[0] < rectangle[2] <= container[2] and
                container[1] <= rectangle[1] < rectangle[3] <= container[3])

    def inspect(state, all_visible):
        nodes = ui()
        dismiss_onboarding(nodes)
        require(json.loads(save_bytes()) == state, "Board, faces, picks, preference, or orientation changed")
        remaining, held = replay(state)
        display_rect = bounds(nodes[0])
        require(display_rect[3] - display_rect[1] > display_rect[2] - display_rect[0], "Wrong native orientation")
        viewport_nodes = [node for node in nodes if node.get("resource-id") == args.package + ":id/board_viewport"]
        require(len(viewport_nodes) == 1, "Missing unique accessible board_viewport")
        viewport = bounds(viewport_nodes[0])
        labels = ("Hint", "How to play", "Switch to landscape and start a new board", "New board")
        controls = [bounds(button(label, nodes)) for label in labels]
        hand = []
        for slot in range(4):
            identity = names[state["faces"][held[slot]]] if slot < len(held) else "empty"
            found = [node for node in nodes if node.get("content-desc", "").startswith(f"Hand slot {slot + 1}: {identity}")]
            require(len(found) == 1, "Missing or incorrect hand slot")
            hand.append(bounds(found[0]))
        require(all(within(rect, display_rect) for rect in controls + hand + [viewport]), "Fixed UI is clipped")
        require(all(rect[2] - rect[0] >= 52 * density - 1 and rect[3] - rect[1] >= 52 * density - 1
                    for rect in controls), "Control target smaller than 52dp")
        require(all(left[2] <= right[0] for left, right in zip(controls, controls[1:])), "Top controls overlap")
        centers = [(rect[0] + rect[2]) / 2 for rect in controls]
        require(all(abs(center - (centers[0] + (centers[-1] - centers[0]) * index / 3)) <= 3 * density
                    for index, center in enumerate(centers)), "Four top controls are not evenly spaced")
        require(all(left[2] <= right[0] for left, right in zip(hand, hand[1:])),
                "Portrait hand slots overlap or are not ordered left to right")
        require(all(abs((rect[1] + rect[3]) - (hand[0][1] + hand[0][3])) <= 2 for rect in hand),
                "Portrait hand slots do not form one horizontal row")
        require(all(abs((rect[3] - rect[1]) - (rect[2] - rect[0]) * 1.22) <= 2 for rect in hand),
                "Hand tiles are rotated or distorted")
        require(all(rect[2] - rect[0] >= 44 * density - 1 for rect in hand), "Bottom hand tiles are too small")
        require(max(rect[3] for rect in controls) <= viewport[1], "Board viewport overlaps controls")
        require(max(rect[3] for rect in controls) <= min(rect[1] for rect in hand), "Hand overlaps top controls")
        require(viewport[3] <= min(rect[1] for rect in hand), "Hand is not clear of the bottom of the board")
        hand_span = hand[-1][2] - hand[0][0]
        display_width = display_rect[2] - display_rect[0]
        require(hand_span >= min(display_width * .65, 390 * density), "Portrait hand does not use the screen width")
        available = set(free_tiles(state["positions"], remaining))
        tiles = {}
        for node in nodes:
            description = node.get("content-desc", "")
            match = TILE.match(description)
            if match:
                index = int(match.group(1)) - 1
                require(index in remaining and index not in tiles, "Removed or duplicate tile")
                require(description.startswith(names[state["faces"][index]] + " tile, "), "Wrong fairy identity")
                require((node.get("enabled") == "true") == (index in available), "Wrong tile availability")
                tiles[index] = bounds(node)
        require(tiles, "No visible board tiles")
        require(all(within(rect, viewport) for rect in tiles.values()), "Tile escapes scrolling viewport")
        if all_visible:
            require(set(tiles) == remaining, "Normal screen does not expose every tile")
            require(all(abs((rect[3] - rect[1]) - (rect[2] - rect[0]) * 1.22) <= 2
                        for rect in tiles.values()), "Normal screen clips a tile vertically")
        columns = 1 + (max(p[0] for p in state["positions"]) - min(p[0] for p in state["positions"])) / 2
        # Allow a conservative 4dp overlap between 48dp tile sidewalls, with
        # room for the board's outer padding and the layered tile offset.
        enough_width = (viewport[2] - viewport[0]) / density >= columns * 48 - (columns - 1) * 4 + 28
        smallest = min(rect[2] - rect[0] for rect in tiles.values()) / density
        if enough_width:
            require(smallest >= 48 - 1 / density, f"Tiles shrank below 48dp ({smallest:.2f}dp) despite sufficient width")
        return {"nodes": nodes, "tiles": tiles, "viewport": viewport, "controls": controls,
                "hand": hand, "smallest_tile_width_dp": smallest, "room_for_48dp_tiles": enough_width,
                "scrollable": viewport_nodes[0].get("scrollable") == "true"}

    def fully_visible_tiles(current):
        return {index for index, rect in current["tiles"].items()
                if abs((rect[3] - rect[1]) - (rect[2] - rect[0]) * 1.22) <= 2}

    def shape(state):
        replay(state)
        require(state["version"] == 5 and state["orientation"] == "portrait" and not state["picks"],
                "Fixture must be an untouched v5 portrait deal")
        require(len(state["positions"]) >= 64, "Fixture is a tiny board")
        xs, ys = ([p[axis] for p in state["positions"]] for axis in (0, 1))
        columns, rows = 1 + (max(xs) - min(xs)) / 2, 1 + (max(ys) - min(ys)) / 2
        require(columns <= 7 and rows <= 10 and rows / columns <= 1.6, "Portrait shape is too tall or wide")
        return columns, rows

    def record(label):
        report["checks"].append(label)
        print("PASS: " + label, flush=True)

    def display_config(kind):
        value = adb("shell", "wm", kind)
        pattern = r"(Physical|Override) " + kind + r":\s*([\dx]+)"
        parsed = dict(re.findall(pattern, value))
        require("Physical" in parsed, "Cannot read display " + kind)
        return parsed

    try:
        require(adb("get-state") == "device", "Device is not authorized")
        adb("shell", "run-as", args.package, "pwd")
        display = {kind: display_config(kind) for kind in ("size", "density")}
        report["original_display"] = display
        density = int(display["density"].get("Override", display["density"]["Physical"])) / 160
        report["default_density_dpi"] = round(density * 160)
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
        states = []
        for fixture_path in fixtures:
            state = json.loads(fixture_path.read_text(encoding="utf-8-sig"))
            columns, rows = shape(state)
            states.append((state, fixture_path.stem, columns, rows))
            inject(state)
            current = wait_for(lambda: inspect(state, True), fixture_path.stem + " fits default screen")
            require(not current["scrollable"], "Default Pixel screen requires scrolling")
            report["shapes"].append({"fixture": fixture_path.name, "columns": columns, "rows": rows,
                                     "tiles": len(state["positions"]), "smallest_tile_width_dp": current["smallest_tile_width_dp"],
                                     "room_for_48dp_tiles": current["room_for_48dp_tiles"], "viewport": current["viewport"],
                                     "hand": current["hand"]})
            screenshot("shape-" + fixture_path.stem + ".png")
            record(f"{fixture_path.stem}: {columns:g}x{rows:g}, {len(state['positions'])} tiles; all visible at {current['smallest_tile_width_dp']:.1f}dp, controls and hand clear")
        state, name, columns, rows = max(states, key=lambda item: (item[3] / item[2], item[3]))
        stop()
        display_modified = True
        adb("shell", "wm", "size", "1080x1600")
        adb("shell", "wm", "density", "420")
        density = 420 / 160
        require(display_config("size").get("Override") == "1080x1600", "Short display size override failed")
        require(display_config("density").get("Override", display_config("density")["Physical"]) == "420",
                "Short display density override failed")
        inject(state)
        current = wait_for(lambda: inspect(state, False), "short portrait board")
        require(current["scrollable"], "Short portrait board has no vertical scrolling")
        # Tile sidewalls overlap, so a column-count times tile-width estimate is
        # deliberately conservative. This fixed short-screen scenario must
        # demonstrate the floor directly from native tile bounds instead.
        require(current["smallest_tile_width_dp"] >= 48 - 1 / density,
                f"Short-screen tiles fell below 48dp ({current['smallest_tile_width_dp']:.2f}dp)")
        original_game = save_bytes()
        fixed_controls, fixed_hand = current["controls"], current["hand"]
        top_tiles = dict(current["tiles"])
        seen = fully_visible_tiles(current)
        screenshot("short-top-" + name + ".png")
        previous = None
        scroll_count = 0
        for _ in range(12):
            view = current["viewport"]
            x = (view[0] + view[2]) // 2
            top, bottom = view[1] + (view[3] - view[1]) // 5, view[3] - (view[3] - view[1]) // 5
            adb("shell", "input", "swipe", str(x), str(bottom), str(x), str(top), "450")
            current = wait_for(lambda: inspect(state, False), "scrolled short board")
            scroll_count += 1
            require(current["smallest_tile_width_dp"] >= 48 - 1 / density,
                    "Scrolling reduced short-screen tiles below 48dp")
            require(current["controls"] == fixed_controls and current["hand"] == fixed_hand,
                    "Controls or hand moved with board scrolling")
            require(save_bytes() == original_game, "Scrolling changed the exact saved game")
            seen.update(fully_visible_tiles(current))
            if current["tiles"] == previous:
                break
            previous = dict(current["tiles"])
        require(current["tiles"] != top_tiles, "Swipe did not move the board")
        require(seen == set(range(len(state["positions"]))), "Some tiles cannot be reached by vertical scrolling")
        screenshot("short-bottom-" + name + ".png")
        report["short_screen"] = {"fixture": name, "size_px": [1080, 1600], "density_dpi": 420,
                                  "smallest_tile_width_dp": current["smallest_tile_width_dp"],
                                  "visited_tiles": len(seen), "swipes": scroll_count, "save_bytes_unchanged": True}
        record(f"Short 411x610dp screen: all {len(seen)} tiles reachable at >=48dp; scrolling preserves exact save and fixed controls/hand")
        stop()
        adb("shell", "wm", "size", "960x1600")
        require(display_config("size").get("Override") == "960x1600", "Narrow display size override failed")
        inject(state)
        current = wait_for(lambda: inspect(state, False), "narrow portrait board")
        require(current["room_for_48dp_tiles"], "Narrow portrait screen lost width reserved for its board")
        require(current["smallest_tile_width_dp"] >= 48 - 1 / density,
                "Narrow screen shrank tiles below 48dp despite the full-width board")
        screenshot("narrow-top-" + name + ".png")
        report["narrow_screen"] = {"fixture": name, "size_px": [960, 1600], "density_dpi": 420,
                                   "smallest_tile_width_dp": current["smallest_tile_width_dp"],
                                   "viewport": current["viewport"], "hand": current["hand"],
                                   "save_unchanged": json.loads(save_bytes()) == state}
        record("Narrow 366x610dp screen keeps the horizontal hand below the full-width board and retains >=48dp tiles")
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
            if display_modified:
                stop()
                for kind, config in display.items():
                    adb("shell", "wm", kind, config.get("Override", "reset"))
                    require(display_config(kind) == config, "Display " + kind + " not restored")
            report["display_state_restored"] = True
        except Exception as error:
            restoration_errors.append("Display: " + str(error))
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
                report["original_save_restored"] = report["original_preferences_restored"] = True
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
