#!/usr/bin/env python3
"""Exercise FairyMahjong's debug app through ADB, including real process death.

Starts a new board, checks that no haptics toggle is exposed, and clears a generated board
through the native Hint button and tile controls in the development app.
Original save bytes and AtomicFile sidecars are backed up and restored in finally.
Requires Python 3, ADB, and an installed debug APK. The explicit device serial
is never selected implicitly.
"""

import argparse
from collections import Counter
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

from test_device_guard import require_test_emulator

HAND_CAPACITY = 4
SAVE_PATHS = tuple("files/practice-board.json" + suffix for suffix in ("", ".bak", ".new"))
LAYOUT_ID = "garden-v1"
LEGACY_FACE_NAMES = ("Bamboo", "Flower", "Sun", "Waves", "Leaf", "Star")
TILE_DESCRIPTION = re.compile(
    r"(.+) tile, (\d+) of (\d+), (available|blocked|game over|covered|side blocked|hand full)"
    r"(?:\. Hint: (?:pick next|step \d+|matching tile))?"
)
HAND_DESCRIPTION = re.compile(r"Hand slot ([1-4]): (.+?)(?:, Hint: matching tile)?")


def require(condition, message):
    # Unlike assert, these checks remain active if Python is invoked with -O.
    if not condition:
        raise AssertionError(message)


def catalog_face_names():
    """Read the saved-ID contract; verify the installed APK actually renders these names."""
    path = (Path(__file__).resolve().parents[1] / "app/src/main/java/com/fairytrick/fairymahjong/TileCatalog.kt")
    entries = re.findall(r'TileFace\((\d+), ("(?:\\.|[^"\\])*"),', path.read_text(encoding="utf-8"))
    result = {int(face_id): json.loads(name) for face_id, name in entries}
    require(len(entries) == len(result) == 62 and set(result) == set(range(62)),
            "Expected six legacy faces plus 56 stable fairy artwork identities")
    require(tuple(result[index] for index in range(6)) == LEGACY_FACE_NAMES,
            "Legacy saved face names changed")
    require(len(set(result.values())) == len(result), "Catalog names must distinguish every identity")
    return result


FACE_NAMES = catalog_face_names()


def garden_positions():
    """Documented garden-v1 geometry in saved tile-index order.

    Half-tile coordinates keep overlap calculations exact. This independent
    planner only selects a short test path; actual picks still use observed,
    enabled native controls and every resulting save/UI state is checked.
    """
    result = []
    for layer, lengths, y_offset in (
        (0, (3, 4, 5, 6, 5, 4, 3), 0),
        (1, (2, 3, 4, 4, 3, 2), 1),
    ):
        for row, count in enumerate(lengths):
            result.extend((6 - count + 2 * col, y_offset + 2 * row, layer)
                          for col in range(count))
    return result + [(5, 5, 2), (5, 7, 2)]


def saved_positions(state):
    """Use exact persisted index order; only old garden saves need a known skeleton."""
    version = state.get("version")
    require(type(version) is int and version in (2, 3, 4), "Expected version-2, -3 or -4 game save")
    if version in (2, 3):
        require(state.get("layout") == LAYOUT_ID, "Unsupported legacy layout for this device test")
        return tuple(garden_positions())
    require(isinstance(state.get("layout"), str) and bool(state["layout"]), "Missing saved shape ID")
    positions = state.get("positions")
    require(isinstance(positions, list) and 2 <= len(positions) <= 256 and len(positions) % 2 == 0,
            "Invalid saved board size")
    require(all(isinstance(position, list) and len(position) == 3
                and all(type(value) is int for value in position)
                and 0 <= position[2] < 4 for position in positions), "Invalid saved tile coordinates")
    result = tuple(tuple(position) for position in positions)
    require(len(set(result)) == len(result), "Duplicate saved tile positions")
    require(max(x for x, _, _ in result) - min(x for x, _, _ in result) <= 14
            and max(y for _, y, _ in result) - min(y for _, y, _ in result) <= 14,
            "Saved board exceeds an 8 by 8 footprint")
    return result


@lru_cache(maxsize=16)
def blocker_masks(positions):
    masks = []
    for x, y, layer in positions:
        above = left = right = 0
        for index, (other_x, other_y, other_layer) in enumerate(positions):
            if other_layer > layer and abs(other_x - x) < 2 and abs(other_y - y) < 2:
                above |= 1 << index
            if other_layer == layer and abs(other_y - y) < 2:
                if other_x == x - 2:
                    left |= 1 << index
                if other_x == x + 2:
                    right |= 1 << index
        masks.append((above, left, right))
    return tuple(masks)


def free_tiles(picked_mask, positions):
    remaining = ((1 << len(positions)) - 1) & ~picked_mask
    return [index for index, (above, left, right) in enumerate(blocker_masks(positions))
            if remaining & (1 << index)
            and not (remaining & above)
            and not (remaining & left and remaining & right)]


def advance_hand(hand, index, faces):
    matching = next((old for old in hand if faces[old] == faces[index]), None)
    return tuple(old for old in hand if old != matching) if matching is not None else hand + (index,)


def replay(state):
    positions = saved_positions(state)
    faces = state.get("faces")
    picks = state.get("picks")
    require(isinstance(faces, list) and len(faces) == len(positions)
            and all(type(face) is int and face in FACE_NAMES for face in faces), "Invalid saved faces")
    require(all(count % 2 == 0 for count in Counter(faces).values()), "Saved face counts must be even")
    require(isinstance(picks, list), "Missing saved picks")
    require(type(state.get("haptics")) is bool, "Missing haptics boolean")
    mask = 0
    hand = ()
    for index in picks:
        require(type(index) is int and index in free_tiles(mask, positions), f"Illegal saved pick {index}")
        require(len(hand) < HAND_CAPACITY, "Saved pick occurred with a full hand")
        mask |= 1 << index
        hand = advance_hand(hand, index, faces)
    return mask, hand


def plan_test_picks(state, max_depth=8, max_states=50000):
    """Find a short legal path with a matched pair and then a full, distinct hand.

    Search is bounded and makes no device changes. If no such path is found,
    report the limitation explicitly instead of skipping the matching checks.
    """
    picked_mask, hand = replay(state)
    require(not state["picks"], "Planner needs a fresh board")
    faces = state["faces"]
    positions = saved_positions(state)
    examined = 0
    visited = set()

    def search(mask, current_hand, path):
        nonlocal examined
        examined += 1
        if examined > max_states:
            return None
        has_match = len(path) > len(current_hand)
        if len(current_hand) == HAND_CAPACITY:
            return path if has_match else None
        remaining_depth = max_depth - len(path)
        minimum_moves = HAND_CAPACITY - len(current_hand) + (0 if has_match else 2)
        if remaining_depth < minimum_moves:
            return None
        key = (mask, tuple(sorted(faces[index] for index in current_hand)))
        if key in visited:
            return None
        visited.add(key)
        hand_faces = {faces[index] for index in current_hand}
        # Establish one match first, then prefer different faces to fill the hand.
        choices = sorted(free_tiles(mask, positions), key=lambda index: (
            (faces[index] in hand_faces) if has_match else (faces[index] not in hand_faces),
            -positions[index][2], index,
        ))
        for index in choices:
            result = search(mask | (1 << index), advance_hand(current_hand, index, faces), path + (index,))
            if result is not None:
                return result
        return None

    result = search(picked_mask, hand, ())
    require(result is not None,
            f"No match-plus-full-hand path found within {max_depth} picks/{max_states} states; "
            "this test cannot cover this deal (no device moves were made)")
    return result, examined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="Explicit ADB serial")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", default="artifacts/smoke-test")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid Android package name")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    prefix = [args.adb, "-s", args.serial]
    require_test_emulator(prefix)
    report = {
        "status": "running", "serial": args.serial, "package": args.package, "checks": [],
        "original_save_restored": False,
        "note": "Device smoke checks are not cold-start benchmarks or physical haptics measurements.",
    }
    backup = {}
    backed_up = False
    originally_running = False

    def write_report():
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def command(*parts, data=None, check=True):
        result = subprocess.run(prefix + list(parts), input=data, capture_output=True, timeout=40)
        if check and result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace") or result.stdout.decode(errors="replace"))
        return result

    def adb(*parts, check=True):
        return command(*parts, check=check).stdout.decode(errors="replace").strip()

    def write_bytes(path, data):
        require(path in SAVE_PATHS, "Unexpected save path")
        command("shell", "run-as", args.package, "sh", "-c", f"'cat > {path}'", data=data)

    def wait_for(operation, predicate, label, seconds=20):
        deadline = time.monotonic() + seconds
        last_error = None
        while time.monotonic() < deadline:
            try:
                result = operation()
                if predicate(result):
                    return result
            except (RuntimeError, AssertionError, ET.ParseError, json.JSONDecodeError) as error:
                last_error = error
            time.sleep(0.3)
        raise AssertionError(f"Timed out waiting for {label}; last error: {last_error}")

    def ui():
        remote = "/data/local/tmp/fairymahjong-smoke-ui.xml"
        # A failed dump must never be mistaken for a fresh hierarchy.
        adb("shell", "rm", "-f", remote)
        adb("shell", "uiautomator", "dump", remote)
        return ET.fromstring(adb("shell", "cat", remote))

    def nodes(root):
        return list(root.iter("node"))

    def board_nodes(root):
        result = {}
        for node in nodes(root):
            match = TILE_DESCRIPTION.fullmatch(node.get("content-desc", ""))
            if match:
                index = int(match[2]) - 1
                require(index not in result, f"Duplicate tile node {index}")
                result[index] = (node, match[1], match[4], int(match[3]))
        return result

    def text_node(root, label):
        return next((node for node in nodes(root) if node.get("text") == label), None)

    def tap(node, allow_disabled=False):
        require(node is not None and (allow_disabled or node.get("enabled") == "true"),
                "Required enabled control is missing")
        bounds = list(map(int, re.findall(r"-?\d+", node.get("bounds", ""))))
        require(len(bounds) == 4 and bounds[2] > bounds[0] and bounds[3] > bounds[1],
                "Control has no visible touch bounds")
        adb("shell", "input", "tap", str((bounds[0] + bounds[2]) // 2), str((bounds[1] + bounds[3]) // 2))

    def saved():
        value = json.loads(adb("shell", "run-as", args.package, "cat", "files/practice-board.json"))
        replay(value)
        return value

    def capture(name):
        result = subprocess.run(prefix + ["exec-out", "screencap", "-p"],
                                capture_output=True, timeout=30, check=True)
        require(result.stdout.startswith(b"\x89PNG\r\n\x1a\n"), "Screenshot is not a PNG")
        (output / name).write_bytes(result.stdout)
        print(f"Screenshot: {(output / name).resolve()}", flush=True)

    def launch():
        return adb("shell", "am", "start", "-W", "-n", args.package + "/.MainActivity")

    # English accessibility labels are part of this smoke test's UI contract.
    face_names = FACE_NAMES

    def check_ui(root, state):
        mask, hand = replay(state)
        positions = saved_positions(state)
        tile_count = len(positions)
        rendered = board_nodes(root)
        expected_indices = set(range(tile_count)) - set(state["picks"])
        require(set(rendered) == expected_indices, "Visible board tiles differ from saved picks")
        enabled_indices = set(free_tiles(mask, positions)) if len(hand) < HAND_CAPACITY else set()
        for index, (node, face_name, availability, described_count) in rendered.items():
            require(described_count == tile_count, f"Tile {index} describes the wrong board size")
            require(face_name == face_names[state["faces"][index]], f"Tile {index} has wrong face")
            is_enabled = node.get("enabled") == "true"
            require(is_enabled == (index in enabled_indices), f"Tile {index} has wrong enabled state")
            require((availability == "available") == is_enabled, f"Tile {index} has wrong accessibility status")
        slots = {}
        for node in nodes(root):
            match = HAND_DESCRIPTION.fullmatch(node.get("content-desc", ""))
            if match:
                slot = int(match[1])
                require(slot not in slots, f"Duplicate hand slot {slot}")
                require(node.get("clickable") != "true", "Hand slots must not accept picks")
                slots[slot] = match[2]
        expected_slots = {slot + 1: (face_names[state["faces"][hand[slot]]]
                                    if slot < len(hand) else "empty")
                          for slot in range(HAND_CAPACITY)}
        require(slots == expected_slots, f"Rendered hand {slots} differs from saved hand {expected_slots}")
        require(text_node(root, "Undo") is None, "The game must not expose an Undo control")
        expected_score = len(state["picks"]) - len(hand)
        require(any(node.get("content-desc") == f"Score: {expected_score} of {tile_count}"
                    for node in nodes(root)), f"Displayed score does not equal cleared tiles out of {tile_count}")
        status = ("complete" if mask == (1 << tile_count) - 1 and not hand else
                  "game over" if len(hand) == HAND_CAPACITY or not free_tiles(mask, positions) else "playing")
        require(any(node.get("content-desc", "").split(". ", 1)[0] == f"Board status: {status}"
                    for node in nodes(root)), f"Expected board status {status}")
        hint = text_node(root, "Hint")
        if status in ("complete", "game over"):
            require(hint is not None and hint.get("enabled") == "false", "Hint must be disabled on an ended board")
        for label in ("Restart", "New board"):
            control = text_node(root, label)
            require(control is not None and control.get("enabled") == "true", f"Missing enabled {label} control")
        require(not any(node.get("text") == "Gentle haptics"
                        or node.get("content-desc") == "Gentle haptics" for node in nodes(root)),
                "The removed haptics toggle must not be exposed")
        return True

    def ready_ui(state):
        return wait_for(ui, lambda root: check_ui(root, state), "board and hand matching the save")

    def change_board(label):
        before = wait_for(saved, lambda state: check_ui(ui(), state), f"state before {label}")
        mask, hand = replay(before)
        in_progress = bool(before["picks"]) and len(hand) < HAND_CAPACITY and bool(
            free_tiles(mask, saved_positions(before)))
        root = wait_for(ui, lambda tree: text_node(tree, label) is not None
                        and text_node(tree, label).get("enabled") == "true", f"{label} control")
        tap(text_node(root, label))

        def positive_button(tree):
            return next((node for node in nodes(tree)
                         if node.get("resource-id") == "android:id/button1"
                         and node.get("text", "").casefold() == label.casefold()), None)

        if in_progress:
            dialog = wait_for(ui, lambda tree: positive_button(tree) is not None, f"{label} confirmation")
            tap(positive_button(dialog))
        # An empty previous deal also has picks=[]; require the saved faces to
        # match the new UI so an asynchronous write cannot yield a stale deal.
        def changed(state):
            if state["picks"] or state["haptics"] != before["haptics"]:
                return False
            if label == "New board" and state["faces"] == before["faces"] and saved_positions(state) == saved_positions(before):
                return False
            return check_ui(ui(), state)

        return wait_for(saved, changed, f"{label} save")

    def highlighted_next(root):
        candidates = [(index, node) for index, (node, _, _, _) in board_nodes(root).items()
                      if node.get("content-desc", "").endswith(". Hint: pick next")]
        require(len(candidates) <= 1, "Multiple next-pick hints are visible")
        if candidates:
            require(candidates[0][1].get("enabled") == "true", "Hint's next tile is disabled")
            return candidates[0]
        return None

    def hint_for_next(state, root):
        current = highlighted_next(root)
        if current is not None:
            return root, current
        # Search budgets increase only on the explicit, supported retry message.
        for attempt in range(3):
            tap(text_node(root, "Hint"))
            root = wait_for(ui, lambda tree: highlighted_next(tree) is not None or
                            any(node.get("text") in {
                                "No safe hint found yet. Tap Hint to search further.",
                                "No winning moves remain. Restart to try this board again.",
                                "Couldn't confirm a safe hint for this position.",
                            } for node in nodes(tree)), "safe next-pick hint", seconds=90)
            require(saved() == state, "Requesting a hint changed game state or preferences")
            check_ui(root, state)
            current = highlighted_next(root)
            if current is not None:
                return root, current
            require(text_node(root, "No safe hint found yet. Tap Hint to search further.") is not None,
                    "Generated board has no confirmed winning hint continuation")
        raise AssertionError("Generated board's hint search exhausted all supported budgets")

    write_report()
    try:
        require(adb("get-state") == "device", "Device is not connected and authorized")
        # Never exercise a non-debuggable production installation.
        adb("shell", "run-as", args.package, "pwd")
        originally_running = bool(adb("shell", "pidof", args.package, check=False))
        adb("shell", "am", "force-stop", args.package)
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
        report["initial_launch_diagnostics"] = launch()
        # A previous interrupted smoke run may have left its confirmation open.
        initial_window = ui()
        cancel = next((node for node in nodes(initial_window)
                       if node.get("package") == args.package
                       and node.get("resource-id") == "android:id/button2"
                       and node.get("text", "").casefold() == "keep playing"), None)
        if cancel is not None:
            tap(cancel)
        expected = change_board("New board")
        ready = ready_ui(expected)
        counts = Counter(expected["faces"])
        require(all(6 <= face <= 61 for face in counts), "New board must select from the 56 fairy artwork identities")
        minimum, maximum = (12, 16) if len(expected["faces"]) >= 64 else (10, 14)
        require(minimum <= len(counts) <= maximum, "New board selected an unexpected number of active identities")
        require(max(counts.values()) - min(counts.values()) <= 2, "New board's pair counts are unbalanced")
        report["shape"] = expected["layout"]
        report["tile_count"] = len(expected["faces"])
        report["active_face_counts"] = dict(sorted(counts.items()))
        report["catalog_id_count"] = len(face_names)
        report["checks"].append(f"fresh {len(expected['faces'])}-tile layered board, empty hand, zero score and no Undo")
        capture("01-board.png")

        plan, searched = plan_test_picks(expected)
        report["planned_picks_zero_based"] = list(plan)
        report["planner_states_examined"] = searched
        (output / "initial-state.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        for index in plan:
            tile = board_nodes(ready).get(index)
            require(tile is not None and tile[2] == "available", f"Planned tile {index} is not available")
            tap(tile[0])
            expected = dict(expected, picks=expected["picks"] + [index])
            wait_for(saved, lambda state: state == expected, f"accepted pick {index} saved")
            ready = ready_ui(expected)
        _, hand = replay(expected)
        matched_pairs = (len(expected["picks"]) - len(hand)) // 2
        require(matched_pairs >= 1 and len(hand) == HAND_CAPACITY, "Planner did not exercise matching/full hand")
        report["matched_pairs_exercised"] = matched_pairs
        report["score_before_restore"] = matched_pairs * 2
        report["checks"].extend(["native legal tile taps and blocked-tile enabled states",
                                 "equal pair removed from hand and score counts cleared tiles",
                                 "full four-slot hand ends game and disables board picks"])
        geometrically_free = free_tiles(replay(expected)[0], saved_positions(expected))
        require(bool(geometrically_free), "No remaining exposed tile for full-hand lock check")
        tap(board_nodes(ready)[geometrically_free[0]][0], allow_disabled=True)
        ready = ready_ui(expected)
        require(saved() == expected, "Tapping the locked board changed the game")
        report["checks"].append("physical tap on full-hand locked tile is ignored")
        capture("02-full-hand.png")

        ready_ui(expected)
        require(saved() == expected, "Removing the haptics toggle changed the saved game")
        report["checks"].append("haptics toggle is absent and the legacy saved value is preserved")

        previous_pid = adb("shell", "pidof", args.package)
        require(bool(previous_pid), "App is not running")
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        wait_for(ui, lambda root: not any(node.get("package") == args.package for node in nodes(root)),
                 "app no longer visible after Home")
        launch()
        ready_ui(expected)
        require(adb("shell", "pidof", args.package) == previous_pid,
                "Ordinary app switch unexpectedly restarted the process")
        require(saved() == expected, "Ordinary app switch changed saved state")
        report["checks"].append("ordinary app switch retains process, board, hand and haptics")

        adb("shell", "input", "keyevent", "KEYCODE_HOME")

        def kill_background_process():
            # Retry through the Home transition, without force-stopping its task.
            adb("shell", "am", "kill", args.package)
            return adb("shell", "pidof", args.package, check=False)

        wait_for(kill_background_process, lambda pid: not pid, "background process death")
        report["checks"].append("background process confirmed dead via pidof")
        report["process_relaunch_diagnostics"] = launch()
        ready = ready_ui(expected)
        restored_pid = adb("shell", "pidof", args.package)
        require(bool(restored_pid) and restored_pid != previous_pid, "A new app process was not started")
        require(saved() == expected, "Process recreation changed faces, picks or haptics")
        report["checks"].append("new process restores exact board, nonempty hand, score, game-over and haptics")
        capture("03-restored.png")

        before_restart = expected
        actual_restart = change_board("Restart")
        expected = dict(before_restart, picks=[])
        require(actual_restart == expected, "Restart did not retain faces/haptics and clear all picks")
        ready_ui(expected)
        report["checks"].append("Restart after loss immediately keeps the same deal, empties hand and restores zero-score play")
        capture("04-restart.png")

        winning_start = expected
        winning_picks = []
        ready = ready_ui(expected)
        while len(expected["picks"]) < len(expected["faces"]):
            ready, (index, tile) = hint_for_next(expected, ready)
            if not winning_picks:
                capture("05-winning-hint.png")
            require(index in free_tiles(replay(expected)[0], saved_positions(expected)),
                    "Hint recommends a geometrically blocked tile")
            tap(tile)
            expected = dict(expected, picks=expected["picks"] + [index])
            wait_for(saved, lambda state: state == expected, f"winning pick {index} saved")
            ready = ready_ui(expected)
            _, hand = replay(expected)
            require(len(hand) < HAND_CAPACITY, "Following the generated board's hints filled the hand")
            winning_picks.append(index)
            print(f"Winning route: {len(winning_picks)}/{len(expected['faces'])} tiles", flush=True)
        require(not replay(expected)[1], "Cleared board left unmatched tiles in hand")
        report["winning_picks_zero_based"] = winning_picks
        report["checks"].append("native Hint highlights and legal tile taps clear the entire generated board with an empty hand and full score")
        capture("06-complete.png")
        actual_restart = change_board("Restart")
        require(actual_restart == winning_start, "Restart after a complete game changed its original deal or settings")
        expected = actual_restart
        ready_ui(expected)
        report["checks"].append("Restart after completion returns the exact winning deal to its original state")
        (output / "final-state.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            pid = adb("shell", "pidof", args.package, check=False).split()
            if pid:
                (output / "logcat.txt").write_text(
                    adb("logcat", "-d", "--pid=" + pid[0], "-t", "400"), encoding="utf-8")
        except Exception as error:
            report["log_capture_error"] = str(error)
        try:
            if backed_up:
                adb("shell", "am", "force-stop", args.package)
                adb("shell", "run-as", args.package, "rm", "-f", *SAVE_PATHS)
                for path, data in backup.items():
                    write_bytes(path, data)
                    require(command("exec-out", "run-as", args.package, "cat", path).stdout == data,
                            f"Original save bytes were not restored for {path}")
                report["original_save_restored"] = True
                if originally_running:
                    launch()
        except Exception as error:
            report["status"] = "failed"
            report["restore_error"] = str(error)
            raise
        finally:
            write_report()
            print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
