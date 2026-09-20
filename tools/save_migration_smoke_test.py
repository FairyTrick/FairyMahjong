#!/usr/bin/env python3
"""Verify saved-board migrations and recovery on an explicitly selected debug device.

Injects fixture saves, launches the app, and checks persisted state and native UI.
Original save bytes are backed up and restored in finally, including AtomicFile
sidecars. Requires Python 3, ADB, and an installed debuggable application.
"""

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

from test_device_guard import require_test_emulator

SAVE_PATHS = tuple("files/practice-board.json" + suffix for suffix in ("", ".bak", ".new"))
PREFERENCE_PATH = "shared_prefs/instructions.xml"
BACKUP_PATHS = SAVE_PATHS + (PREFERENCE_PATH, PREFERENCE_PATH + ".bak")
RECOVERY_PREFIX = "The previous save could not be read."
TILE_DESCRIPTION = re.compile(r"(.+) tile, (\d+) of (\d+), (available|blocked|game over)")


def catalog_faces():
    source = (Path(__file__).resolve().parents[1] /
              "app/src/main/java/com/fairytrick/fairymahjong/TileCatalog.kt")
    entries = re.findall(r'TileFace\((\d+), ("(?:\\.|[^"\\])*"),', source.read_text(encoding="utf-8"))
    faces = {int(identity): json.loads(name) for identity, name in entries}
    require(faces and len(faces) == len(entries), "Catalog IDs must be unique")
    return faces


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def garden_positions():
    result = []
    for layer, lengths, y_offset in (
        (0, (3, 4, 5, 6, 5, 4, 3), 0),
        (1, (2, 3, 4, 4, 3, 2), 1),
    ):
        for row, count in enumerate(lengths):
            result.extend([6 - count + 2 * column, y_offset + 2 * row, layer]
                          for column in range(count))
    return result + [[5, 5, 2], [5, 7, 2]]


def free_tiles(positions, remaining):
    result = []
    for index in sorted(remaining):
        x, y, layer = positions[index]
        above = left = right = False
        for other_index in remaining:
            ox, oy, olayer = positions[other_index]
            above |= olayer > layer and abs(ox - x) < 2 and abs(oy - y) < 2
            if olayer == layer and abs(oy - y) < 2:
                left |= ox == x - 2
                right |= ox == x + 2
        if not above and not (left and right):
            result.append(index)
    return result


def garden_fixture(version):
    positions = garden_positions()
    remaining = set(range(len(positions)))
    order = []
    while remaining:
        available = free_tiles(positions, remaining)
        require(available, "Fixture garden must be removable")
        index = available[0]
        order.append(index)
        remaining.remove(index)
    in_order = [0, 1, 2, 0, 1, 2]
    for pair in range((len(positions) - len(in_order)) // 2):
        in_order.extend([pair % 6] * 2)
    faces = [0] * len(positions)
    for index, face in zip(order, in_order):
        faces[index] = face
    return {"version": version, "layout": "garden-v1", "faces": faces,
            "picks": order[:4], "haptics": False}


def replay(state, face_names=None):
    if face_names is None:
        face_names = catalog_faces()
    require(state.get("version") in (4, 5), "Expected v4 or v5 save")
    if state["version"] == 5:
        require(type(state.get("orientation")) is str and
                state["orientation"] in {"portrait", "landscape"}, "Invalid v5 orientation")
    positions = state.get("positions")
    faces = state.get("faces")
    picks = state.get("picks")
    require(isinstance(positions, list) and 2 <= len(positions) <= 256, "Invalid shape size")
    require(all(isinstance(p, list) and len(p) == 3 and all(type(c) is int for c in p)
                for p in positions), "Invalid position triples")
    require(isinstance(faces, list) and len(faces) == len(positions)
            and all(type(face) is int and face in face_names for face in faces), "Invalid faces")
    require(all(count % 2 == 0 for count in Counter(faces).values()), "Odd face counts")
    require(isinstance(picks, list) and type(state.get("haptics")) is bool, "Invalid save fields")
    remaining = set(range(len(positions)))
    hand = []
    for index in picks:
        require(type(index) is int and len(hand) < 4
                and index in free_tiles(positions, remaining), "Invalid saved pick history")
        remaining.remove(index)
        match = next((tile for tile in hand if faces[tile] == faces[index]), None)
        if match is None:
            hand.append(index)
        else:
            hand.remove(match)
    return remaining, hand


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", default="artifacts/save-migration-smoke-test")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package name")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    prefix = [args.adb, "-s", args.serial]
    require_test_emulator(prefix)
    report = {"status": "running", "serial": args.serial, "checks": [], "original_save_restored": False}
    backup = {}
    face_names = catalog_faces()
    backed_up = False
    originally_running = False

    def command(*parts, data=None, check=True):
        result = subprocess.run(prefix + list(parts), input=data, capture_output=True, timeout=40)
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout).decode(errors="replace"))
        return result

    def adb(*parts, check=True):
        return command(*parts, check=check).stdout.decode(errors="replace").strip()

    def wait_for(operation, label, seconds=25):
        deadline = time.monotonic() + seconds
        last_error = None
        while time.monotonic() < deadline:
            try:
                return operation()
            except (RuntimeError, AssertionError, ET.ParseError, json.JSONDecodeError) as error:
                last_error = error
            time.sleep(0.25)
        raise AssertionError(f"Timed out waiting for {label}; last error: {last_error}")

    def stop():
        adb("shell", "am", "force-stop", args.package)

    def launch():
        adb("shell", "am", "start", "-W", "-n", args.package + "/.MainActivity")

    def write_bytes(path, data):
        # Only fixed, enumerated paths enter this remote shell redirection.
        require(path in BACKUP_PATHS, "Unexpected private test path")
        command("shell", "run-as", args.package, "sh", "-c", f"'cat > {path}'", data=data)

    def clear_saves():
        adb("shell", "run-as", args.package, "rm", "-f", *SAVE_PATHS)

    def inject(state):
        stop()
        clear_saves()
        write_bytes(SAVE_PATHS[0], json.dumps(state).encode("utf-8"))
        launch()

    def saved():
        state = json.loads(adb("shell", "run-as", args.package, "cat", SAVE_PATHS[0]))
        replay(state, face_names)
        return state

    def ui():
        remote = "/data/local/tmp/fairymahjong-migration-ui.xml"
        adb("shell", "rm", "-f", remote)
        adb("shell", "uiautomator", "dump", remote)
        return ET.fromstring(adb("shell", "cat", remote))

    def check_ui(state, notice=None):
        remaining, hand = replay(state, face_names)
        nodes = list(ui().iter("node"))
        expected_status = "complete" if not remaining and not hand else "game over" if len(hand) == 4 else "playing"
        require(any(node.get("content-desc", "").split(". ", 1)[0] == f"Board status: {expected_status}" for node in nodes),
                "Loaded board status differs from save")
        tiles = {}
        for node in nodes:
            description = TILE_DESCRIPTION.fullmatch(node.get("content-desc", ""))
            if description:
                index = int(description[2]) - 1
                require(index not in tiles, "Duplicate rendered tile index")
                require(description[1] == face_names[state["faces"][index]], "Loaded tile face differs from save")
                require(int(description[3]) == len(state["faces"]), "Wrong board tile count")
                tiles[index] = node
        # Accessibility dumps omit children outside the scroll viewport on tall boards.
        # Every exposed node must still agree with the persisted board and its rules.
        require(set(tiles) <= remaining, "Loaded board shows a tile already removed in the save")
        require(not remaining or tiles, "Loaded board has no visible tiles")
        available = set(free_tiles(state["positions"], remaining)) if len(hand) < 4 else set()
        require({index for index, node in tiles.items() if node.get("enabled") == "true"} == available & tiles.keys(),
                "Loaded board's pickability differs from saved geometry and hand")
        for slot in range(4):
            label = face_names[state["faces"][hand[slot]]] if slot < len(hand) else "empty"
            require(sum(node.get("content-desc") == f"Hand slot {slot + 1}: {label}" for node in nodes) == 1,
                    "Loaded hand differs from save")
        require(not any(node.get("text") == "Gentle haptics"
                        or node.get("content-desc") == "Gentle haptics" for node in nodes),
                "The removed haptics toggle must not be exposed")
        texts = [node.get("text", "") for node in nodes]
        if notice == "recovery":
            require(any(text.startswith(RECOVERY_PREFIX) for text in texts), "Missing unreadable-save notice")
        elif notice == "migration":
            require(any("practice board" in text.lower() for text in texts), "Missing v1 migration notice")
        else:
            require(not any(text.startswith(RECOVERY_PREFIX) for text in texts), "Valid save was treated as unreadable")
        return state

    def expect_state(expected, notice=None):
        def check():
            current = saved()
            require(current == dict(expected, difficulty=expected.get("difficulty", "NORMAL")),
                    "Restored save changed faces, picks, positions or preferences")
            return check_ui(current, notice)
        return wait_for(check, "exact restored save and UI")

    def expect_fresh(notice):
        def check():
            current = saved()
            require(not current["picks"], "Replacement board retains invalid progress")
            require(current["haptics"] == (notice == "recovery"), "Wrong replacement haptics preference")
            return check_ui(current, notice)
        return wait_for(check, "fresh board and notice")

    def record(label):
        report["checks"].append(label)
        print(f"PASS: {label}", flush=True)

    try:
        require(adb("get-state") == "device", "Device is not authorized")
        adb("shell", "run-as", args.package, "pwd")  # Refuse non-debuggable production installs.
        originally_running = bool(adb("shell", "pidof", args.package, check=False))
        stop()
        adb("shell", "run-as", args.package, "mkdir", "-p", "files", "shared_prefs")
        for path in BACKUP_PATHS:
            exists = command("shell", "run-as", args.package, "test", "-f", path, check=False)
            if exists.returncode == 0:
                backup[path] = command("exec-out", "run-as", args.package, "cat", path).stdout
            else:
                require(exists.returncode == 1, f"Cannot inspect original save {path}")
        backed_up = True
        for path, data in backup.items():
            (output / (Path(path).name + ".original")).write_bytes(data)
        adb("shell", "run-as", args.package, "rm", "-f", PREFERENCE_PATH + ".bak")
        write_bytes(PREFERENCE_PATH, b'<?xml version="1.0" encoding="utf-8"?><map><boolean name="seen" value="true" /></map>')
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")

        for version in (2, 3):
            fixture = garden_fixture(version)
            inject(fixture)
            expected = dict(fixture, version=5, positions=garden_positions(), orientation="portrait")
            expect_state(expected)
            stop()
            launch()
            expect_state(expected)
            record(f"v{version} garden preserves faces, nonempty hand, matched pair, picks and haptics through v5 rewrite and relaunch")

        legacy = {"version": 1, "faces": [face for face in range(6) for _ in range(2)],
                  "matches": [[0, 1]], "selected": 2, "haptics": False}
        inject(legacy)
        migrated = expect_fresh("migration")
        stop()
        launch()
        expect_state(migrated)
        record("valid v1 practice save migrates to a fresh persistent v5 board and preserves haptics")

        custom = {"version": 4, "layout": "archived-custom-style-2026",
                  "positions": [[0, 0, 0], [2, 0, 0], [0, 2, 0], [2, 2, 0]],
                  "faces": [0, 1, 0, 1], "picks": [0, 1, 2], "haptics": False}
        inject(custom)
        current_custom = dict(custom, version=5, orientation="portrait")
        expect_state(current_custom)
        stop()
        launch()
        expect_state(current_custom)
        record("v4 custom style outside catalog restores exact coordinates and progress across process restart")

        last_face = max(face_names)
        fairy_custom = dict(current_custom, faces=[last_face, 23, last_face, 23])
        inject(fairy_custom)
        expect_state(fairy_custom)
        stop()
        launch()
        expect_state(fairy_custom)
        record("v5 sparse fairy IDs preserve exact coordinates, progress and haptics across process restart")

        landscape = dict(fairy_custom, orientation="landscape")
        inject(landscape)
        expect_state(landscape)
        stop()
        launch()
        expect_state(landscape)
        record("landscape preference and in-progress hand survive process restart without regenerating the deal")

        complete = dict(current_custom, picks=[0, 1, 2, 3])
        inject(complete)
        expect_state(complete)
        record("completed board restores with an empty hand and no tiles")

        full_hand = dict(current_custom, positions=[[x * 2, y * 2, 0] for y in range(2) for x in range(4)],
                         faces=[0, 1, 2, 3, 0, 1, 2, 3], picks=[0, 1, 2, 3])
        inject(full_hand)
        expect_state(full_hand)
        record("full hand restores as game over with remaining tiles locked")

        for sidecar, base in ((".bak", b"interrupted replacement"), (".bak", None), (".new", json.dumps(current_custom).encode())):
            stop()
            clear_saves()
            if base is not None:
                write_bytes(SAVE_PATHS[0], base)
            write_bytes(SAVE_PATHS[0] + sidecar, json.dumps(current_custom if sidecar == ".bak" else landscape).encode())
            launch()
            expect_state(current_custom)
            record(f"AtomicFile recovers committed progress with {sidecar} and {'an existing' if base is not None else 'no'} base file")

        malformed = []
        overlap = copy.deepcopy(custom)
        overlap["positions"][1] = overlap["positions"][0]
        malformed.append(("overlapping positions", overlap))
        bounds = copy.deepcopy(custom)
        bounds["positions"][0][0] = 32
        malformed.append(("out-of-bounds position", bounds))
        unsupported = copy.deepcopy(custom)
        for position in unsupported["positions"]:
            position[2] = 1
        malformed.append(("unsupported upper layer", unsupported))
        fractional = copy.deepcopy(custom)
        fractional["positions"][0][0] = 0.5
        malformed.append(("fractional coordinate", fractional))
        repeated_pick = copy.deepcopy(custom)
        repeated_pick["picks"] = [0, 0]
        malformed.append(("repeated pick history", repeated_pick))
        invalid_legacy = copy.deepcopy(legacy)
        invalid_legacy["matches"] = [[0, 2]]
        malformed.append(("invalid v1 match", invalid_legacy))
        invalid_face = dict(custom, faces=[last_face + 1, 23, last_face + 1, 23], picks=[])
        malformed.append(("unknown face ID", invalid_face))
        malformed.append(("unsupported save version", dict(current_custom, version=1000)))
        malformed.append(("invalid orientation", dict(current_custom, orientation="automatic")))
        malformed.append(("out-of-range pick", dict(current_custom, picks=[len(current_custom["faces"])])))
        malformed.append(("pick after full hand", dict(full_hand, picks=[0, 1, 2, 3, 4])))
        for label, fixture in malformed:
            inject(fixture)
            expect_fresh("recovery")
            record(f"{label} rejected and reported as unreadable save")
        stop()
        clear_saves()
        write_bytes(SAVE_PATHS[0], b'{"version":5,"faces":[')
        launch()
        expect_fresh("recovery")
        record("truncated JSON recovers to a usable persistent board")
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        try:
            if backed_up:
                stop()
                adb("shell", "run-as", args.package, "rm", "-f", *BACKUP_PATHS)
                for path, data in backup.items():
                    write_bytes(path, data)
                    actual = command("exec-out", "run-as", args.package, "cat", path).stdout
                    require(actual == data, f"Original save bytes were not restored for {path}")
                report["original_save_restored"] = True
                if originally_running:
                    launch()
        except Exception as error:
            report["status"] = "failed"
            report["restore_error"] = str(error)
            raise
        finally:
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Report: {(output / 'report.json').resolve()}", flush=True)


if __name__ == "__main__":
    main()
