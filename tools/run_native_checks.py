#!/usr/bin/env python3
"""Run the installed framework reliability tests on an explicit test emulator."""
import argparse
from pathlib import Path
import re
import subprocess

from test_device_guard import require_test_emulator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prefix = [args.adb, "-s", args.serial]
    require_test_emulator(prefix)
    result = subprocess.run(prefix + ["shell", "am", "instrument", "-w", "-r",
        "com.fairytrick.fairymahjong.test/com.fairytrick.fairymahjong.ReliabilityInstrumentation"],
        capture_output=True, timeout=240, text=True, encoding="utf-8", errors="replace")
    log = result.stdout + result.stderr
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(log, encoding="utf-8")
    print(log)
    count = re.search(r"INSTRUMENTATION_RESULT: checks=(\d+)", log)
    if (result.returncode or "INSTRUMENTATION_RESULT: reliability=passed" not in log
            or "INSTRUMENTATION_RESULT: failures=0" not in log
            or not count or int(count.group(1)) == 0):
        raise SystemExit("Native reliability checks failed or did not execute")


if __name__ == "__main__":
    main()
