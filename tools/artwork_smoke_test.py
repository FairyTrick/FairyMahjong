#!/usr/bin/env python3
"""Capture every fairy artwork ID in native tiles on one explicit debug device.

Seven 4x4 flat fixture boards show eight identities twice per board, covering
stable IDs 6 through 61. Also checks a held tile, a safe matching hint, and the
real New board controls. Existing saves and AtomicFile sidecars are preserved
byte-for-byte in finally. Screenshots require separate visual inspection.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

from test_device_guard import require_test_emulator
from hint_smoke_test import TILE, fixture, replay, winnable
from save_migration_smoke_test import SAVE_PATHS, free_tiles, require


def artwork_fixtures():
    positions = [[column * 2, row * 2, 0] for row in range(4) for column in range(4)]
    boards = []
    for group in range(7):
        first = 6 + group * 8
        faces = []
        # Each row clears from its matching outer pair to its matching inner
        # pair, providing an independently obvious winning path.
        for offset in range(0, 8, 2):
            faces.extend([first + offset, first + offset + 1,
                          first + offset + 1, first + offset])
        board = fixture(f"artwork-catalog-{group + 1}", positions, faces)
        require(winnable(board), "Artwork fixture is not independently winnable")
        boards.append(board)
    require(set(face for board in boards for face in board["faces"]) == set(range(6, 62)),
            "Artwork fixtures do not cover every fairy ID")
    return boards


def catalog_names():
    source = (Path(__file__).resolve().parents[1] /
              "app/src/main/java/com/fairytrick/fairymahjong/TileCatalog.kt").read_text(encoding="utf-8")
    names = {int(index): name for index, name in
             re.findall(r'TileFace\((\d+), "([^"\n]+)"', source)}
    require(set(names) == set(range(62)), "Cannot read the complete stable tile catalog")
    require(len(set(names.values())) == len(names), "Catalog names are not unique")
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", default="artifacts/artwork-smoke-test")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package name")
    boards, names = artwork_fixtures(), catalog_names()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    prefix = [args.adb, "-s", args.serial]
    require_test_emulator(prefix)
    report = {"status": "running", "serial": args.serial, "checks": [], "fixtures": [],
              "original_save_restored": False, "screenshots_require_visual_review": True}
    backup = {}
    backed_up = False
    originally_running = False

    def command(*parts, data=None, check=True):
        result = subprocess.run(prefix + list(parts), input=data, capture_output=True, timeout=40)
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout).decode(errors="replace"))
        return result

    def adb(*parts, check=True):
        return command(*parts, check=check).stdout.decode(errors="replace").strip()

    def wait_for(operation, label, seconds=30):
        deadline = time.monotonic() + seconds
        last_error = None
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
        require(path in SAVE_PATHS, "Unexpected save path")
        command("shell", "run-as", args.package, "sh", "-c", f"'cat > {path}'", data=data)

    def clear_saves():
        adb("shell", "run-as", args.package, "rm", "-f", *SAVE_PATHS)

    def saved():
        state = json.loads(adb("shell", "run-as", args.package, "cat", SAVE_PATHS[0]))
        replay(state)
        return state

    def expect_save(expected):
        actual = saved()
        require(actual == expected, "Faces, geometry, picks or preferences changed unexpectedly")
        return actual

    def inject(state):
        stop()
        clear_saves()
        write_bytes(SAVE_PATHS[0], json.dumps(state).encode("utf-8"))
        launch()
        wait_for(lambda: expect_save(state), "exact fixture save")

    def ui():
        remote = "/data/local/tmp/fairymahjong-artwork-ui.xml"
        adb("shell", "rm", "-f", remote)
        adb("shell", "uiautomator", "dump", remote)
        return list(ET.fromstring(adb("shell", "cat", remote)).iter("node"))

    def bounds(node):
        coordinates = [int(value) for value in re.findall(r"\d+", node.get("bounds", ""))]
        require(len(coordinates) == 4, "Native node has no bounds")
        left, top, right, bottom = coordinates
        require(right > left and bottom > top, "Native node has empty bounds")
        return coordinates

    def tap(node):
        require(node.get("enabled") == "true", "Refusing to tap a disabled control")
        left, top, right, bottom = bounds(node)
        adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))

    def control(label, nodes):
        matches = [node for node in nodes if node.get("text") == label
                   and node.get("clickable") == "true" and node.get("enabled") == "true"]
        require(len(matches) == 1, f"Expected one enabled {label} control")
        bounds(matches[0])
        return matches[0]

    def screenshot(name):
        data = command("exec-out", "screencap", "-p").stdout
        require(data.startswith(b"\x89PNG\r\n\x1a\n"), "ADB did not return a PNG screenshot")
        (output / name).write_bytes(data)

    def checked_ui(state):
        nodes = ui()
        remaining, hand = replay(state)
        available = set(free_tiles(state["positions"], remaining)) if len(hand) < 4 else set()
        tiles = {}
        for node in nodes:
            description = node.get("content-desc", "")
            match = TILE.match(description)
            if not match:
                continue
            index = int(match.group(1)) - 1
            require(index in remaining and index not in tiles, "Duplicate or removed tile node")
            require(int(match.group(2)) == len(state["faces"]), "Native tile total differs from save")
            expected_name = names[state["faces"][index]]
            require(description.startswith(expected_name + " tile, "),
                    f"Artwork ID {state['faces'][index]} has an incorrect accessible name")
            require((node.get("enabled") == "true") == (index in available),
                    "Artwork node pickability differs from geometry")
            bounds(node)
            tiles[index] = node
        require(set(tiles) == remaining, "Not every remaining artwork has a visible native tile node")
        for slot, index in enumerate(hand, 1):
            expected = f"Hand slot {slot}: {names[state['faces'][index]]}"
            require(any(node.get("content-desc", "").startswith(expected) for node in nodes),
                    "Held artwork identity differs from its board tile")
        score = len(state["picks"]) - len(hand)
        require(any(node.get("content-desc") == f"Score: {score} of {len(state['faces'])}" for node in nodes),
                "Native score differs from fixture")
        for label in ("Hint", "Restart", "New board"):
            control(label, nodes)
        expect_save(state)
        return nodes, tiles

    def record(label):
        report["checks"].append(label)
        print(f"PASS: {label}", flush=True)

    try:
        require(adb("get-state") == "device", "Device is not authorized")
        adb("shell", "run-as", args.package, "pwd")
        originally_running = bool(adb("shell", "pidof", args.package, check=False))
        stop()
        adb("shell", "run-as", args.package, "mkdir", "-p", "files")
        for path in SAVE_PATHS:
            exists = command("shell", "run-as", args.package, "test", "-f", path, check=False)
            if exists.returncode == 0:
                backup[path] = command("exec-out", "run-as", args.package, "cat", path).stdout
            else:
                require(exists.returncode == 1, f"Cannot inspect original save {path}")
        backed_up = True
        for path, data in backup.items():
            (output / (Path(path).name + ".original")).write_bytes(data)
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")

        for number, board in enumerate(boards, 1):
            inject(board)
            nodes, tiles = wait_for(lambda: checked_ui(board), "all sixteen artwork nodes")
            ids = sorted(set(board["faces"]))
            image = f"{number:02d}-catalog-{ids[0]:02d}-{ids[-1]:02d}.png"
            screenshot(image)
            (output / f"{number:02d}-fixture.json").write_text(
                json.dumps(board, indent=2) + "\n", encoding="utf-8")
            report["fixtures"].append({"board": number, "artwork_ids": ids, "screenshot": image,
                                       "names": {str(face): names[face] for face in ids}})
            record(f"Fixture {number}: all 16 native tiles display exact catalog names for IDs {ids[0]}–{ids[-1]}")

            if number == 1:
                tap(tiles[0])
                held = dict(board, picks=[0])
                wait_for(lambda: expect_save(held), "first artwork in hand")
                nodes, _ = wait_for(lambda: checked_ui(held), "held artwork name")
                screenshot("08-held-artwork.png")
                tap(control("Hint", nodes))

                def checked_hint():
                    hint_ui, hint_tiles = checked_ui(held)
                    next_nodes = [node for node in hint_ui
                                  if "Hint: pick next" in node.get("content-desc", "")]
                    require(len(next_nodes) == 1, "Expected exactly one next-pick highlight")
                    require(next_nodes[0] is hint_tiles[3], "Hint did not highlight the safe identical tile")
                    require(next_nodes[0].get("enabled") == "true", "Next hinted tile is disabled")
                    expected = f"Hand slot 1: {names[board['faces'][0]]}"
                    require(any(node.get("content-desc", "").startswith(expected)
                                and "Hint: matching tile" in node.get("content-desc", "")
                                for node in hint_ui), "Matching hand artwork is not highlighted")
                    expect_save(held)
                    return hint_ui

                wait_for(checked_hint, "matching hand and next-board highlights")
                screenshot("09-held-matching-hint.png")
                record("Picking artwork preserves its hand identity; Hint highlights the available identical tile and hand without changing save")

        previous = boards[-1]
        nodes, _ = checked_ui(previous)
        tap(control("New board", nodes))

        def fresh_board():
            state = saved()
            require(not state["picks"], "New board retained fixture picks")
            require(state["positions"] != previous["positions"] or state["faces"] != previous["faces"],
                    "New board did not replace the fixture")
            require(state["haptics"] == previous["haptics"], "New board changed preferences")
            counts = Counter(state["faces"])
            require(all(6 <= face <= 61 and count % 2 == 0 for face, count in counts.items()),
                    "Real deal does not use paired fairy identities")
            checked_ui(state)
            return state

        real = wait_for(fresh_board, "real generated board and native controls")
        screenshot("10-real-new-board.png")
        (output / "real-new-board.json").write_text(json.dumps(real, indent=2) + "\n", encoding="utf-8")
        record("Real New board uses paired fairy artwork with visible native Hint, Restart and New board buttons")
        report["artwork_ids_checked"] = sorted({face for board in boards for face in board["faces"]})
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        try:
            if backed_up:
                stop()
                clear_saves()
                for path, data in backup.items():
                    write_bytes(path, data)
                    actual = command("exec-out", "run-as", args.package, "cat", path).stdout
                    require(actual == data, f"Original save bytes were not restored for {path}")
                for path in set(SAVE_PATHS) - set(backup):
                    exists = command("shell", "run-as", args.package, "test", "-f", path, check=False)
                    require(exists.returncode == 1, f"Unexpected restored save sidecar {path}")
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
