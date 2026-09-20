#!/usr/bin/env python3
"""Compare debug-emulator frame times for a fixed production-board pick sequence.

Uses a dedicated named test AVD only; never operates a physical/user device.
One native hierarchy dump establishes fixed tile centers before each trial.
No screenshots, recording, hierarchy dumps, or save reads run in the timed loop.
This is an emulator comparison, not a physical-device frame-rate guarantee.
"""

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

from smoke_fixtures import TILE
from save_migration_smoke_test import SAVE_PATHS, free_tiles, replay, require
from test_device_guard import require_test_emulator


PRIVATE_PATHS = SAVE_PATHS + ("shared_prefs/instructions.xml", "shared_prefs/instructions.xml.bak")
SEEN = b'<?xml version="1.0" encoding="utf-8"?><map><boolean name="seen" value="true" /></map>\n'


def pick_sequence(state, count):
    """Deterministic legal picks with matches and a nontrivial held hand."""
    remaining, hand = replay(state)
    faces, positions = state["faces"], state["positions"]

    def search(left, held, picks, matches, peak):
        if len(picks) == count:
            return picks if matches >= 3 and peak >= 2 else None
        available = free_tiles(positions, left)
        available.sort(key=lambda index: (not any(faces[h] == faces[index] for h in held), index))
        for tile in available:
            next_hand = list(held)
            partner = next((h for h in held if faces[h] == faces[tile]), None)
            if partner is None:
                if len(held) >= 3:
                    continue
                next_hand.append(tile)
            else:
                next_hand.remove(partner)
            result = search(left - {tile}, next_hand, picks + [tile],
                            matches + (partner is not None), max(peak, len(next_hand)))
            if result is not None:
                return result
        return None

    result = search(remaining, hand, [], 0, len(hand))
    require(result is not None, "No suitable deterministic legal workload")
    return result


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    point = (len(ordered) - 1) * fraction
    lower, upper = math.floor(point), math.ceil(point)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (point - lower), 3)


def parse_frames(text):
    frames = []
    header = None
    for row in csv.reader(io.StringIO(text)):
        if row and row[0] == "Flags":
            header = row
        elif header and row and row[0].isdigit() and len(row) == len(header):
            values = {key: int(value) for key, value in zip(header, row) if key and value}
            start, end = values.get("IntendedVsync", 0), values.get("FrameCompleted", 0)
            if values.get("Flags") == 0 and 0 < end - start < 10_000_000_000:
                frames.append(values)
    return frames


def frame_summary(frames):
    durations = [(f["FrameCompleted"] - f["IntendedVsync"]) / 1_000_000 for f in frames]
    ui = [(f["SyncQueued"] - f["HandleInputStart"]) / 1_000_000 for f in frames
          if f.get("SyncQueued", 0) >= f.get("HandleInputStart", 0) > 0]
    deadline = [f for f in frames if f.get("FrameDeadline", 0) > f["IntendedVsync"]]
    return {"frames": len(frames), "total_ms_p50": percentile(durations, .5),
            "total_ms_p90": percentile(durations, .9), "total_ms_p95": percentile(durations, .95),
            "total_ms_p99": percentile(durations, .99), "total_ms_max": round(max(durations), 3),
            "ui_ms_p50": percentile(ui, .5), "ui_ms_p95": percentile(ui, .95),
            "frames_over_16_667ms": sum(value > 16.667 for value in durations),
            "frames_over_33_333ms": sum(value > 33.333 for value in durations),
            "frames_past_reported_deadline": sum(f["FrameCompleted"] > f["FrameDeadline"] for f in deadline),
            "frames_with_reported_deadline": len(deadline)}


def workload_summary(text):
    """Counters cover all frames since reset, unlike the 120-row frame ring."""
    labels = ("Total frames rendered", "Janky frames", "Number Missed Vsync",
              "Number High input latency", "Number Slow UI thread", "Number Slow bitmap uploads",
              "Number Slow issue draw commands", "Number Frame deadline missed")
    result = {}
    for label in labels:
        match = re.search(r"^" + re.escape(label) + r": (\d+)", text, re.MULTILINE)
        if match:
            result[label] = int(match.group(1))
    for percentile_value in (50, 90, 95, 99):
        match = re.search(r"^" + str(percentile_value) + r"th percentile: (\d+)ms", text, re.MULTILINE)
        if match:
            result[f"native_frame_ms_p{percentile_value}"] = int(match.group(1))
    result["framestats_is_rolling_last_120"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.fairytrick.fairymahjong")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--picks", type=int, default=10)
    parser.add_argument("--spacing", type=float, default=.8)
    args = parser.parse_args()
    require(args.serial == "emulator-5556", "This profiling workload is restricted to emulator-5556")
    require(re.fullmatch(r"[A-Za-z]\w*(?:\.[A-Za-z]\w*)+", args.package), "Invalid package")
    require(1 <= args.trials <= 5 and 8 <= args.picks <= 12 and .65 <= args.spacing <= 2,
            "Use 1–5 trials, 8–12 picks, and 0.65–2 second spacing")
    prefix = [args.adb, "-s", args.serial]
    avd = require_test_emulator(prefix)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    state = json.loads(args.fixture.read_text(encoding="utf-8"))
    state.update(version=5, orientation="portrait", picks=[], haptics=False)
    state.setdefault("difficulty", "NORMAL")
    replay(state)
    picks = pick_sequence(state, args.picks)
    expected = dict(state, picks=picks)
    replay(expected)
    payload = json.dumps(state, sort_keys=True).encode("utf-8")
    report = {"status": "running", "serial": args.serial, "avd": avd,
              "fixture": str(args.fixture), "fixture_sha256": hashlib.sha256(payload).hexdigest(),
              "picks": picks, "tile_count": len(state["faces"]), "spacing_seconds": args.spacing,
              "trials": [], "emulator_comparison_only": True}
    backup, ready, original_scale, originally_running = {}, False, None, False
    all_frames = []

    def command(*parts, data=None, check=True, timeout=30):
        result = subprocess.run(prefix + list(parts), input=data, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout).decode(errors="replace"))
        return result

    def adb(*parts, check=True):
        return command(*parts, check=check).stdout.decode(errors="replace").strip()

    def stop():
        adb("shell", "am", "force-stop", args.package)

    def read(path):
        require(path in PRIVATE_PATHS, "Unexpected private path")
        return command("exec-out", "run-as", args.package, "cat", path).stdout

    def write(path, data):
        require(path in PRIVATE_PATHS, "Unexpected private path")
        command("shell", "run-as", args.package, "sh", "-c", f"'cat > {path}'", data=data)

    try:
        require(adb("shell", "getprop", "sys.boot_completed") == "1", "Test emulator has not booted")
        report["density"] = adb("shell", "wm", "density")
        report["display_size"] = adb("shell", "wm", "size")
        report["build_fingerprint"] = adb("shell", "getprop", "ro.build.fingerprint")
        renderer = adb("shell", "dumpsys", "SurfaceFlinger")
        report["renderer"] = [line.strip() for line in renderer.splitlines()
                              if any(key in line for key in ("GLES:", "RenderEngine", "GL_VENDOR", "GL_RENDERER"))]
        (output / "surfaceflinger.txt").write_text(renderer, encoding="utf-8")
        originally_running = bool(adb("shell", "pidof", args.package, check=False))
        original_scale = adb("shell", "settings", "get", "global", "animator_duration_scale")
        stop()
        adb("shell", "run-as", args.package, "mkdir", "-p", "files", "shared_prefs")
        for path in PRIVATE_PATHS:
            exists = command("shell", "run-as", args.package, "test", "-f", path, check=False)
            require(exists.returncode in (0, 1), "Cannot inspect private save")
            if exists.returncode == 0:
                backup[path] = read(path)
                (output / (Path(path).name + ".original")).write_bytes(backup[path])
        ready = True
        adb("shell", "settings", "put", "global", "animator_duration_scale", "1")
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")
        write(PRIVATE_PATHS[3], SEEN)

        for trial in range(args.trials):
            stop()
            adb("shell", "run-as", args.package, "rm", "-f", *SAVE_PATHS)
            write(SAVE_PATHS[0], payload)
            adb("shell", "am", "start", "-W", "-n", args.package + "/.MainActivity")
            time.sleep(1)
            remote = "/data/local/tmp/fairymahjong-profile-ui.xml"
            adb("shell", "uiautomator", "dump", remote)
            xml = adb("shell", "cat", remote)
            (output / f"trial-{trial + 1}-before-ui.xml").write_text(xml, encoding="utf-8")
            tiles = {int(match.group(1)) - 1: node for node in ET.fromstring(xml).iter("node")
                     if (match := TILE.match(node.get("content-desc", "")))}
            require(len(tiles) == len(state["faces"]), "Production board is not fully available to hierarchy")
            points = []
            for tile in picks:
                bounds = [int(number) for number in re.findall(r"-?\d+", tiles[tile].get("bounds", ""))]
                require(len(bounds) == 4 and bounds[0] < bounds[2] and bounds[1] < bounds[3],
                        "Missing native tile bounds")
                points.append(((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2))
            require(json.loads(read(SAVE_PATHS[0])) == state, "Fixture changed before profiling")
            time.sleep(.8)
            adb("shell", "dumpsys", "gfxinfo", args.package, "reset")
            started = time.monotonic()
            input_times = []
            for index, (x, y) in enumerate(points):
                target = started + index * args.spacing
                time.sleep(max(0, target - time.monotonic()))
                began = time.monotonic()
                adb("shell", "input", "tap", str(x), str(y))
                input_times.append({"start_seconds": round(began - started, 4),
                                    "adb_ms": round((time.monotonic() - began) * 1000, 2)})
            time.sleep(.8)
            raw = adb("shell", "dumpsys", "gfxinfo", args.package, "framestats")
            (output / f"trial-{trial + 1}-gfxinfo.txt").write_text(raw, encoding="utf-8")
            require(json.loads(read(SAVE_PATHS[0])) == expected, "Timed picks differ from predetermined legal workload")
            frames = parse_frames(raw)
            require(len(frames) >= 20, "Too few valid frames for a meaningful workload comparison")
            all_frames.extend(frames)
            result = {"trial": trial + 1, "coordinates": points, "input_times": input_times,
                      "summary": frame_summary(frames), "whole_workload": workload_summary(raw)}
            report["trials"].append(result)
            print(json.dumps(result["summary"]), flush=True)
        report["pooled"] = frame_summary(all_frames)
        report["status"] = "passed"
    except Exception as error:
        report["status"], report["error"] = "failed", str(error)
        raise
    finally:
        try:
            if original_scale is not None:
                operation = "delete" if original_scale == "null" else "put"
                values = () if original_scale == "null" else (original_scale,)
                adb("shell", "settings", operation, "global", "animator_duration_scale", *values)
                require(adb("shell", "settings", "get", "global", "animator_duration_scale") == original_scale,
                        "Animation setting restoration failed")
                report["animation_setting_restored"] = True
            if ready:
                stop()
                adb("shell", "run-as", args.package, "rm", "-f", *PRIVATE_PATHS)
                for path, data in backup.items():
                    write(path, data)
                    require(read(path) == data, "Private file restoration failed")
                for path in set(PRIVATE_PATHS) - set(backup):
                    require(command("shell", "run-as", args.package, "test", "-f", path, check=False).returncode == 1,
                            "Private file unexpectedly remains")
                report["private_files_restored"] = True
                if originally_running:
                    adb("shell", "am", "start", "-W", "-n", args.package + "/.MainActivity")
        except Exception as error:
            report["status"], report["restore_error"] = "failed", str(error)
            raise
        finally:
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Report: {(output / 'report.json').resolve()}", flush=True)


if __name__ == "__main__":
    main()
