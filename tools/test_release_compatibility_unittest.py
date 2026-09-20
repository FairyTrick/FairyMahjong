"""Exercise layout synchronization and failure capture without contacting ADB."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import release_compatibility_test as suite


GAME_FOCUS = f"  mCurrentFocus=Window{{123 u0 {suite.PACKAGE}/{suite.PACKAGE}.MainActivity}}"


def focused_device():
    device = suite.Device.__new__(suite.Device)
    device.adb = Mock(return_value=GAME_FOCUS)
    return device


def board(bounds="[10,10][50,50]", description="Fern tile, 1 of 48, available", difficulty="Normal"):
    button = ET.Element("node", {"content-desc": "How to play", "clickable": "true",
                                  "enabled": "true", "bounds": bounds})
    tile = ET.Element("node", {"content-desc": description, "clickable": "true",
                                "enabled": "true", "bounds": "[100,100][150,180]"})
    difficulty_button = ET.Element("node", {
        "resource-id": suite.PACKAGE + ":id/difficulty_button", "class": "android.widget.Button",
        "clickable": "true", "enabled": "true", "bounds": "[60,10][100,50]",
        "content-desc": f"Difficulty: {difficulty}. Switch to {suite.NEXT_DIFFICULTY[difficulty]} and start a new board",
    })
    return [button, tile, difficulty_button], {1: tile}


def guide(panel_bounds="[0,0][200,400]"):
    nodes = [ET.Element("node", {"package": suite.PACKAGE,
                                "resource-id": suite.PACKAGE + ":id/" + name,
                                "bounds": panel_bounds}) for name in
             ("instructions_screen", "instructions_pick_panel", "instructions_pairs_panel")]
    nodes.append(ET.Element("node", {"package": suite.PACKAGE, "content-desc": "Back to game",
                                      "clickable": "true", "enabled": "true",
                                      "bounds": "[160,0][200,40]"}))
    return nodes


class LayoutSynchronization(unittest.TestCase):
    def test_unchanged_board_does_not_make_moving_control_coordinates_safe(self):
        device = focused_device()
        first, moved, settled = board(), board("[10,100][50,140]"), board("[10,100][50,140]")
        device.game = Mock(side_effect=[first, moved, moved, settled])
        self.assertEqual(device.fingerprint(first), device.fingerprint(moved))
        with self.assertRaisesRegex(AssertionError, "layout is still changing"):
            device.settled_game()
        self.assertIs(device.settled_game(), settled)

    def test_stable_bounds_do_not_hide_a_changing_board(self):
        device = focused_device()
        device.game = Mock(side_effect=[board(), board(description="Teal tile, 1 of 48, available")])
        with self.assertRaisesRegex(AssertionError, "layout is still changing"):
            device.settled_game()

    def test_persistence_fingerprint_detects_reset_difficulty_with_identical_board(self):
        device = focused_device()
        hard, normal = board(difficulty="Hard"), board(difficulty="Normal")
        self.assertEqual(device.fingerprint(hard)["tiles"], device.fingerprint(normal)["tiles"])
        self.assertNotEqual(device.fingerprint(hard), device.fingerprint(normal))
        device.game = Mock(side_effect=[hard, normal])
        with self.assertRaisesRegex(AssertionError, "layout is still changing"):
            device.settled_game()

    def test_difficulty_requires_one_enabled_button_with_the_correct_next_action(self):
        device = focused_device()
        for mode in ("Easy", "Normal", "Hard"):
            nodes, _ = board(difficulty=mode)
            control = device.difficulty(nodes, mode)
            with self.assertRaisesRegex(AssertionError, "Expected difficulty"):
                device.difficulty(nodes, suite.NEXT_DIFFICULTY[mode])
            with self.assertRaisesRegex(AssertionError, "unique difficulty"):
                device.difficulty(nodes + [control])
            for attribute, bad_value in (("enabled", "false"), ("clickable", "false"),
                                         ("class", "android.view.View")):
                original = control.get(attribute)
                control.set(attribute, bad_value)
                with self.assertRaisesRegex(AssertionError, "unavailable"):
                    device.difficulty(nodes)
                control.set(attribute, original)
            control.set("content-desc", f"Difficulty: {mode}. Switch to {mode} and start a new board")
            with self.assertRaisesRegex(AssertionError, "incorrect state or next action"):
                device.difficulty(nodes)
        with self.assertRaisesRegex(AssertionError, "unique difficulty"):
            device.difficulty([])

    def test_guide_waits_for_panel_bounds_even_when_close_control_does_not_move(self):
        device = focused_device()
        first, moved, settled = guide(), guide("[0,0][400,200]"), guide("[0,0][400,200]")
        device.guide = Mock(side_effect=[first, moved, moved, settled])
        with self.assertRaisesRegex(AssertionError, "Guide layout is still changing"):
            device.settled_guide()
        self.assertIs(device.settled_guide(), settled)

    def test_visible_game_is_not_ready_when_another_window_or_no_window_has_focus(self):
        for screen in ("game", "guide"):
            for focus in ("mCurrentFocus=Window{123 u0 NotificationShade}", "mCurrentFocus=null", ""):
                with self.subTest(screen=screen, focus=focus):
                    device = focused_device()
                    getattr_device_screen = Mock(return_value=board() if screen == "game" else guide())
                    setattr(device, screen, getattr_device_screen)
                    # Losing focus between snapshots must also invalidate readiness.
                    device.adb.side_effect = [GAME_FOCUS, focus]
                    with self.assertRaisesRegex(AssertionError, "does not have input focus"):
                        getattr(device, "settled_" + screen)()

    def test_platform_lesson_can_close_before_requiring_game_focus(self):
        device = focused_device()
        lesson = '<hierarchy><node package="android" resource-id="android:id/ok" ' \
                 'clickable="true" enabled="true" text="Got it" bounds="[0,0][20,20]"/></hierarchy>'
        hierarchy = ET.Element("hierarchy")
        hierarchy.extend(guide())
        ready = ET.tostring(hierarchy, encoding="unicode")
        device.ui_markup = Mock(side_effect=[lesson, ready, ready])
        device.tap = Mock()
        with self.assertRaisesRegex(AssertionError, "full-screen lesson to close"):
            device.settled_guide()
        device.adb.assert_not_called()
        device.settled_guide()
        device.tap.assert_called_once()
        self.assertEqual(device.tap.call_args.args[0].get("text"), "Got it")
        self.assertEqual(device.adb.call_count, 2)


class FailureCapture(unittest.TestCase):
    def test_failed_screenshot_does_not_hide_raw_ui_window_or_process_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            device = suite.Device.__new__(suite.Device)
            device.output = Path(directory)
            device.capture = Mock(side_effect=RuntimeError("screencap unavailable"))
            # Diagnostics must preserve an obstructing platform dialog, not dismiss it.
            markup = '<hierarchy><node package="android" resource-id="android:id/ok" ' \
                     'clickable="true" text="Got it" bounds="[0,0][20,20]"/></hierarchy>'
            device.ui_markup = Mock(return_value=markup)
            device.tap = Mock(side_effect=AssertionError("Diagnostics must not tap"))
            responses = {
                ("shell", "dumpsys", "window"): "mCurrentFocus=Window{android/dialog}",
                ("shell", "dumpsys", "activity", "activities"): "mResumedActivity=MainActivity",
                ("shell", "dumpsys", "activity", "exit-info", suite.PACKAGE): "No exits",
                ("logcat", "-b", "events", "-d", "-T", "since"):
                    f"unrelated event\nam_resume_activity: {suite.PACKAGE}/.MainActivity",
                ("logcat", "-b", "crash", "-d", "-T", "since"): "No crashes",
            }
            device.adb = Mock(side_effect=lambda *args, **kwargs: responses[args])
            output = io.StringIO()
            with redirect_stdout(output):
                device.capture_failure("since")
            device.tap.assert_not_called()
            self.assertEqual(device.adb.call_count, len(responses))
            self.assertEqual((device.output / "failure-ui.xml").read_text(), markup)
            self.assertEqual((device.output / "failure-events.txt").read_text(),
                             responses[("logcat", "-b", "events", "-d", "-T", "since")])
            self.assertIn("mCurrentFocus=Window{android/dialog}", output.getvalue())
            self.assertIn("Got it", output.getvalue())
            self.assertIn("screencap unavailable", output.getvalue())
            self.assertIn(f"am_resume_activity: {suite.PACKAGE}/.MainActivity", output.getvalue())

    def test_main_captures_failure_before_restoration_and_force_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apk = root / "release.apk"
            apk.write_bytes(b"unused mocked APK")
            device = Mock(name="device")
            device.name = "Fairy_Test_Compat_Fixture"
            events = []

            def adb(*args, **kwargs):
                events.append(args)
                if args == ("shell", "pm", "path", suite.PACKAGE):
                    return ""
                return "1"

            device.adb.side_effect = adb
            device.command.return_value = subprocess.CompletedProcess([], 1)
            device.launch.side_effect = RuntimeError("fixture launch failure")
            device.capture_failure.side_effect = lambda since: events.append(("capture_failure",))
            argv = ["release_compatibility_test", "--serial", "emulator-5556",
                    "--apk", str(apk), "--output", str(root)]
            with patch.object(suite, "Device", return_value=device), patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(RuntimeError, "fixture launch failure"):
                    suite.main()
            capture = events.index(("capture_failure",))
            restore = [index for index, args in enumerate(events)
                       if args[:3] == ("shell", "settings", "put") or args[:2] == ("shell", "wm")
                       and len(args) > 2 and args[2] in ("size", "density") and len(args) > 3]
            self.assertTrue(restore)
            self.assertTrue(all(index > capture for index in restore))
            self.assertGreater(events.index(("shell", "am", "force-stop", suite.PACKAGE)), capture)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["error"], "fixture launch failure")


if __name__ == "__main__":
    unittest.main()
