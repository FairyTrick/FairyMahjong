#!/usr/bin/env python3
"""Verify safe hints and the fairy catalog on a selected debug device.

Injects small, independently solvable v4 saves and interacts through observed
native UI nodes. Original saves and AtomicFile sidecars are restored byte-for-byte
in finally. Includes legacy and sparse fairy IDs, plus a sample of real New board
deals. Requires Python 3, ADB, and an installed debuggable application.
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
from save_migration_smoke_test import SAVE_PATHS, free_tiles, require


TILE = re.compile(r".+ tile, (\d+) of (\d+), (available|blocked|game over|covered|side blocked|hand full)")
LOSS_NOTICE = "No winning moves remain. Restart to try this board again."
LEGACY_NAMES = {"Bamboo", "Flower", "Sun", "Waves", "Leaf", "Star"}


def replay(state):
    """Replay stable IDs without confusing them with the per-deal dense indices."""
    require(state.get("version") == 4, "Expected v4 save")
    positions, faces, picks = (state.get(key) for key in ("positions", "faces", "picks"))
    require(isinstance(positions, list) and 2 <= len(positions) <= 256
            and len(positions) % 2 == 0, "Invalid shape size")
    require(all(isinstance(position, list) and len(position) == 3
                and all(type(value) is int for value in position) for position in positions),
            "Invalid position triples")
    require(isinstance(faces, list) and len(faces) == len(positions)
            and all(type(face) is int and 0 <= face <= 61 for face in faces), "Invalid stable face IDs")
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


def fixture(name, positions, faces, picks=()):
    state = {"version": 4, "layout": name, "positions": positions,
             "faces": faces, "picks": list(picks), "haptics": False}
    replay(state)
    return state


def winnable(state):
    """Exact coordinate/hand oracle, independent of the Android hint implementation."""
    remaining, held = replay(state)
    faces = state["faces"]

    @lru_cache(None)
    def search(board, hand):
        if len(hand) >= 4:
            return False
        if not board:
            return not hand
        for index in free_tiles(state["positions"], set(board)):
            face = faces[index]
            child_hand = set(hand)
            if face in child_hand:
                child_hand.remove(face)
            else:
                child_hand.add(face)
            if search(tuple(tile for tile in board if tile != index), tuple(sorted(child_hand))):
                return True
        return False

    return search(tuple(sorted(remaining)), tuple(sorted(faces[index] for index in held)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--output", default="artifacts/hint-smoke-test")
    parser.add_argument("--new-deals", type=int, default=8,
                        help="Number of real New board actions to sample (0 skips; otherwise 2–40)")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package name")
    require(args.new_deals == 0 or 2 <= args.new_deals <= 40, "Choose 0 or 2–40 new deals")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    prefix = [args.adb, "-s", args.serial]
    require_test_emulator(prefix)
    report = {"status": "running", "serial": args.serial, "checks": [], "original_save_restored": False}
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

    def ui():
        remote = "/data/local/tmp/fairymahjong-hint-ui.xml"
        adb("shell", "rm", "-f", remote)
        adb("shell", "uiautomator", "dump", remote)
        return list(ET.fromstring(adb("shell", "cat", remote)).iter("node"))

    def tap(node):
        require(node.get("enabled") == "true", "Refusing to tap a disabled hint target")
        bounds = [int(value) for value in re.findall(r"\d+", node.get("bounds", ""))]
        require(len(bounds) == 4, "Missing native control bounds")
        left, top, right, bottom = bounds
        require(right > left and bottom > top, "Empty control bounds")
        adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))

    def expect_save(expected):
        actual = saved()
        require(actual == expected, "Hint changed faces, geometry, progress, or preferences")
        return actual

    def inject(state):
        stop()
        clear_saves()
        write_bytes(SAVE_PATHS[0], json.dumps(state).encode("utf-8"))
        launch()
        wait_for(lambda: expect_save(state), "fixture save")

    def hint_nodes(nodes):
        return [node for node in nodes if "Hint:" in node.get("content-desc", "")]

    def board_index(node):
        match = TILE.match(node.get("content-desc", ""))
        require(match, "Hint target lacks physical tile identity")
        return int(match.group(1)) - 1

    def next_node(nodes):
        matches = [node for node in nodes if "Hint: pick next" in node.get("content-desc", "")]
        require(len(matches) == 1, "Expected exactly one next-pick highlight")
        require(matches[0].get("enabled") == "true", "Hint's next pick is not enabled")
        return matches[0]

    def ask_hint(state, allow_more=False):
        nodes = ui()
        buttons = [node for node in nodes if node.get("text") == "Hint" and node.get("clickable") == "true"]
        require(len(buttons) == 1, "Expected one Hint button")
        tap(buttons[0])

        def check():
            nodes = ui()
            if allow_more and any(node.get("text") == "No safe hint found yet. Tap Hint to search further."
                                  for node in nodes):
                expect_save(state)
                tap(control("Hint", nodes))
                raise AssertionError("Requested the next supported hint search budget")
            require(not any(node.get("text") in {LOSS_NOTICE, "Couldn't confirm a safe hint for this position."}
                            for node in nodes), "The actual generated deal has no confirmed hint")
            next_node(nodes)
            expect_save(state)
            return nodes
        return wait_for(check, "certified hint highlights", seconds=90 if allow_more else 30)

    def screenshot(name):
        (output / name).write_bytes(command("exec-out", "screencap", "-p").stdout)

    def record(label):
        report["checks"].append(label)
        print(f"PASS: {label}", flush=True)

    def control(label, nodes=None):
        matches = [node for node in (nodes if nodes is not None else ui())
                   if node.get("text") == label and node.get("clickable") == "true"
                   and node.get("enabled") == "true"]
        require(len(matches) == 1, f"Expected one enabled {label} control")
        return matches[0]

    def confirm_action(label):
        before = saved()
        tap(control(label))
        # Resolve the observed dialog button by its native resource ID so that an
        # identically named activity button cannot be tapped behind the dialog.
        def confirm_button():
            matches = [node for node in ui() if node.get("resource-id") == "android:id/button1"
                       and node.get("text", "").casefold() == label.casefold()]
            if not matches and saved() != before:
                # Fresh/finished boards change immediately without a progress-loss dialog.
                return None
            require(len(matches) == 1, f"Expected {label} confirmation")
            return matches[0]
        button = wait_for(confirm_button, f"{label} dialog or immediate change")
        if button is not None:
            tap(button)

    def check_fairy_ui(state):
        """Check actual accessible tile/hand controls against the persisted identities."""
        nodes = ui()
        remaining, hand = replay(state)
        available = set(free_tiles(state["positions"], remaining)) if len(hand) < 4 else set()
        tiles = {}
        names = {}
        for node in nodes:
            description = node.get("content-desc", "")
            match = TILE.match(description)
            if not match:
                continue
            index = int(match.group(1)) - 1
            require(index in remaining and index not in tiles, "Missing, duplicate or removed board identity")
            require(int(match.group(2)) == len(state["faces"]), "Tile accessibility total differs from save")
            require((node.get("enabled") == "true") == (index in available), "UI pickability differs from geometry")
            name = description.split(" tile, ", 1)[0]
            face = state["faces"][index]
            require(name and name not in LEGACY_NAMES, "A fairy ID displays a legacy or empty tile name")
            require(face not in names or names[face] == name, "Copies of one face have different names")
            names[face] = name
            tiles[index] = node
        require(set(tiles) == remaining, "Native board controls do not match saved remaining tiles")
        require(len(set(names.values())) == len(names), "Distinct stable faces have indistinguishable names")
        score = len(state["picks"]) - len(hand)
        require(any(node.get("content-desc") == f"Score: {score} of {len(state['faces'])}" for node in nodes),
                "UI score differs from matching history")
        for slot, index in enumerate(hand, 1):
            # Every fixture and sampled first pick leaves another copy on board.
            expected = f"Hand slot {slot}: {names[state['faces'][index]]}"
            require(any(node.get("content-desc", "").startswith(expected) for node in nodes),
                    "Held fairy name differs from its matching board copy")
        return nodes, tiles, names

    def fresh_deal(previous):
        state = saved()
        require(not state["picks"], "New board retained progress")
        require(state["haptics"] == previous["haptics"], "New board changed preferences")
        require(state["faces"] != previous["faces"] or state["positions"] != previous["positions"],
                "New board has not replaced the old deal")
        counts = Counter(state["faces"])
        require(all(6 <= face <= 61 for face in counts), "New board uses IDs outside the new fairy catalog")
        minimum, maximum = (12, 16) if len(state["faces"]) >= 64 else (10, 14)
        require(minimum <= len(counts) <= maximum,
                f"New {len(state['faces'])}-tile board must select {minimum}–{maximum} active identities")
        require(max(counts.values()) - min(counts.values()) <= 2, "Per-identity pair counts are not balanced")
        check_fairy_ui(state)
        return state

    def follow_match(state, nodes, expected_length):
        current = dict(state, picks=list(state["picks"]))
        _, initial_hand = replay(current)
        previous_count = len(initial_hand)
        for step in range(expected_length):
            node = next_node(nodes)
            index = board_index(node)
            require(index in free_tiles(current["positions"], replay(current)[0]), "Highlighted next pick is blocked")
            current["picks"].append(index)
            require(winnable(current), "Following the displayed hint enters an unwinnable state")
            tap(node)
            wait_for(lambda: expect_save(current), "picked hint tile")
            _, hand = replay(current)
            if step + 1 < expected_length:
                require(len(hand) == previous_count + 1, "Hint matched before its displayed plan ended")
                nodes = wait_for(lambda: checked_next(), "advanced hint step")
            else:
                require(len(hand) == previous_count - 1, "Hint did not finish with a match")
                wait_for(check_cleared, "highlights cleared after matching")
            previous_count = len(hand)
        return current

    def checked_next():
        nodes = ui()
        next_node(nodes)
        return nodes

    def check_cleared():
        nodes = ui()
        require(not hint_nodes(nodes), "Old hint highlights remain")
        return nodes

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

        square = [[0, 0, 0], [2, 0, 0], [0, 2, 0], [2, 2, 0]]
        held = fixture("hint-held", square, [0, 1, 0, 1], [0])
        inject(held)
        nodes = ask_hint(held)
        require(board_index(next_node(nodes)) == 2, "Immediate held match must select the available A")
        require(any(node.get("content-desc", "").startswith("Hand slot 1: Bamboo")
                    and "Hint: matching tile" in node.get("content-desc", "") for node in nodes),
                "Held matching tile is not highlighted accessibly")
        screenshot("held-match.png")
        follow_match(held, nodes, 1)
        record("Immediate hand/board pair is highlighted; requesting a hint does not pick; match clears highlights")

        pair = fixture("hint-pair", square, [0, 1, 0, 1])
        inject(pair)
        nodes = ask_hint(pair)
        follow_match(pair, nodes, 2)
        record("Two board tiles match through automatically advancing numbered hints")

        towers = fixture("hint-towers", [[(i % 2) * 2, 0, i // 2] for i in range(8)],
                         [0, 0, 1, 2, 3, 3, 2, 1])
        inject(towers)
        nodes = ask_hint(towers)
        highlighted_board = [node for node in hint_nodes(nodes) if TILE.match(node.get("content-desc", ""))]
        require(len(highlighted_board) >= 4, "Unblocking hint omitted planned tiles")
        blocked_targets = [node for node in highlighted_board if board_index(node) < 6]
        require(blocked_targets and all(node.get("enabled") == "false" for node in blocked_targets),
                "Covered hint targets must stay disabled")
        screenshot("layered-unblocking.png")
        follow_match(towers, nodes, 4)
        record("Four-step layered plan highlights covered targets without enabling them; every followed prefix independently wins")

        trap = fixture("hint-trap", [[i * 2, 2, 0] for i in range(8)] + [[7, 0, 0], [7, 4, 0]],
                       [0, 1, 2, 3, 0, 1, 2, 3, 0, 0], [8])
        require(winnable(trap), "Trap fixture must initially win")
        require(not winnable(dict(trap, picks=[8, 9])), "Tempting immediate match must lose")
        inject(trap)
        nodes = ask_hint(trap)
        require(board_index(next_node(nodes)) != 9, "Hint recommends a locally matching but losing tile")
        screenshot("unsafe-immediate-match-avoided.png")
        record("Hint avoids an available hand match when taking it would make the board unwinnable")

        # A different legal pick invalidates the displayed plan immediately.
        inject(pair)
        nodes = ask_hint(pair)
        next_index = board_index(next_node(nodes))
        other = next(node for node in nodes if TILE.match(node.get("content-desc", ""))
                     and node.get("enabled") == "true" and board_index(node) != next_index)
        alternative = board_index(other)
        tap(other)
        progressed = dict(pair, picks=[alternative])
        wait_for(lambda: expect_save(progressed), "alternate player pick")
        wait_for(check_cleared, "stale hint invalidation")
        ask_hint(progressed)
        stop()
        launch()
        wait_for(lambda: expect_save(progressed), "progress after process restart")
        wait_for(check_cleared, "transient hint removed after process restart")
        record("Alternate picks invalidate hints; fresh hint works afterward; process restart preserves progress without stale highlights")

        loss = fixture("hint-loss", [[i * 2, 0, 0] for i in range(8)], [0, 1, 2, 3, 0, 1, 2, 3])
        require(not winnable(loss), "Loss fixture must be impossible")
        inject(loss)
        buttons = [node for node in ui() if node.get("text") == "Hint" and node.get("clickable") == "true"]
        require(len(buttons) == 1, "Expected Hint button on a not-yet-full losing position")
        tap(buttons[0])

        def check_loss():
            nodes = ui()
            require(any(node.get("text") == LOSS_NOTICE for node in nodes), "Missing proven-loss explanation")
            require(not hint_nodes(nodes), "Proven loss must not display unsafe hints")
            expect_save(loss)
        wait_for(check_loss, "proven-loss result")
        record("Unwinnable position reports no winning moves and never highlights unsafe advice")

        # Use sparse catalog values deliberately: an accidental dense-ID lookup
        # can pass every legacy fixture while selecting the wrong new artwork.
        fairy_held = fixture("hint-fairy-held", square, [61, 6, 61, 6], [0])
        inject(fairy_held)
        wait_for(lambda: check_fairy_ui(fairy_held), "new-art held fixture UI")
        nodes = ask_hint(fairy_held)
        require(board_index(next_node(nodes)) == 2, "Stable ID 61 must match its held physical copy")
        require(any(node.get("content-desc", "").startswith("Hand slot 1: Secret Letter")
                    and "Hint: matching tile" in node.get("content-desc", "") for node in nodes),
                "Highest catalog ID has the wrong held name or no matching highlight")
        screenshot("fairy-held-id61.png")
        follow_match(fairy_held, nodes, 1)
        record("Highest stable fairy ID 61 restores with its correct name, matches the held tile and clears hints")

        fairy_towers = fixture("hint-fairy-towers", [[(i % 2) * 2, 0, i // 2] for i in range(8)],
                               [6, 6, 23, 44, 61, 61, 44, 23])
        inject(fairy_towers)
        _, _, names = wait_for(lambda: check_fairy_ui(fairy_towers), "sparse fairy artwork UI")
        require(names == {6: "Teal Spell", 23: "Acorn Scout portrait", 44: "Poppy Kite", 61: "Secret Letter"},
                "Sparse stable IDs resolve to the wrong catalog names")
        nodes = ask_hint(fairy_towers)
        highlighted_board = [node for node in hint_nodes(nodes) if TILE.match(node.get("content-desc", ""))]
        require(len(highlighted_board) >= 4, "Sparse-ID unblocking hint omitted planned tiles")
        blocked_targets = [node for node in highlighted_board if board_index(node) < 6]
        require(blocked_targets and all(node.get("enabled") == "false" for node in blocked_targets),
                "Covered fairy hint targets must remain disabled")
        screenshot("fairy-layered-unblocking.png")
        follow_match(fairy_towers, nodes, 4)
        record("Sparse IDs 6, 23, 44 and 61 retain correct artwork names through a safe four-step blocked-target plan")

        fairy_trap = fixture("hint-fairy-trap", trap["positions"],
                             [61, 6, 23, 44, 61, 6, 23, 44, 61, 61], [8])
        require(winnable(fairy_trap) and not winnable(dict(fairy_trap, picks=[8, 9])),
                "Sparse-ID trap must distinguish the safe and losing immediate matches")
        inject(fairy_trap)
        nodes = ask_hint(fairy_trap)
        require(board_index(next_node(nodes)) != 9, "Sparse-ID hint selects the losing immediate match")
        follow_match(fairy_trap, nodes, 1)
        record("New-art immediate-pair counterexample selects the safe physical copy and completes its match")

        report["new_deals"] = []
        previous = saved()
        for deal_number in range(args.new_deals):
            confirm_action("New board")
            state = wait_for(lambda: fresh_deal(previous), "new fairy deal and native UI")
            counts = Counter(state["faces"])
            report["new_deals"].append({
                "layout": state["layout"], "tiles": len(state["faces"]),
                "active_count": len(counts), "face_counts": dict(sorted(counts.items())),
            })
            if deal_number == 0:
                screenshot("new-fairy-board.png")
                started = time.monotonic()
                hint_ui = ask_hint(state, allow_more=True)
                hint_node = next_node(hint_ui)
                index = board_index(hint_node)
                require(index in free_tiles(state["positions"], replay(state)[0]),
                        "Real-deal hint highlights a blocked next pick")
                report["real_deal_hint"] = {"wall_seconds_including_adb": round(time.monotonic() - started, 3),
                                           "next_index": index, "stable_face_id": state["faces"][index],
                                           "layout": state["layout"], "active_count": len(counts)}
                screenshot("new-fairy-board-hint.png")
                record("A real generated fairy deal returns a certified hint on an enabled tile without changing its save")
                tap(hint_node)
                progressed = dict(state, picks=[index])
                wait_for(lambda: expect_save(progressed), "picked fairy stable ID")
                wait_for(lambda: check_fairy_ui(progressed), "picked fairy hand UI")
                stop()
                launch()
                wait_for(lambda: expect_save(progressed), "fairy progress after process restart")
                wait_for(lambda: check_fairy_ui(progressed), "restored fairy hand UI")
                confirm_action("Restart")
                wait_for(lambda: expect_save(state), "exact fairy deal after Restart")
                wait_for(lambda: check_fairy_ui(state), "restarted fairy native UI")
                record("Real fairy pick survives process restart; Restart preserves exact geometry, stable faces and preferences")
            previous = state
            print(f"DEAL {deal_number + 1}/{args.new_deals}: {state['layout']}, "
                  f"{len(state['faces'])} tiles, {len(counts)} active fairy identities", flush=True)
        if args.new_deals:
            distinct_counts = {deal["active_count"] for deal in report["new_deals"]}
            distinct_subsets = {tuple(deal["face_counts"]) for deal in report["new_deals"]}
            require(len(distinct_counts) > 1,
                    "All sampled deals chose the same active count; this sample does not establish count variation")
            require(len(distinct_subsets) > 1, "Sampled New board actions reused the same fairy subset")
            record(f"{args.new_deals} real deals use balanced new-art IDs 6–61, valid active counts and varying subsets/counts")
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
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Report: {(output / 'report.json').resolve()}", flush=True)


if __name__ == "__main__":
    main()
