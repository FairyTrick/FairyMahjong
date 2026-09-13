"""Verify fixture isolation without connecting to ADB or starting an emulator."""

import importlib
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_device_guard import require_test_emulator


PREFIX = ["adb", "-s", "emulator-5556"]


def response(stdout=b"Rotation_Test\r\nOK\r\n", returncode=0, stderr=b""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestDeviceGuard(unittest.TestCase):
    def test_accepts_only_dedicated_names_after_reading_actual_avd(self):
        for name in ("Rotation_Test", "Fairy_Test", "Fairy_Test_portrait", "Fairy_Test-2"):
            with self.subTest(name=name), patch("subprocess.run", return_value=response(
                    (name + "\r\nOK\r\n").encode())) as run:
                self.assertEqual(require_test_emulator(PREFIX), name)
                run.assert_called_once_with(PREFIX + ["emu", "avd", "name"],
                                            capture_output=True, timeout=15)

    def test_port_number_does_not_authorize_regular_avd(self):
        for serial in ("emulator-5554", "emulator-5556", "emulator-5580"):
            with self.subTest(serial=serial), patch("subprocess.run", return_value=response(
                    b"Pixel_10\r\nOK\r\n")) as run:
                with self.assertRaisesRegex(RuntimeError, "Refusing.*Pixel_10"):
                    require_test_emulator(["adb", "-s", serial])
                self.assertEqual(run.call_count, 1)

    def test_windows_adb_extra_carriage_returns_keep_identity_check(self):
        for name, allowed in (("Rotation_Test", True), ("Fairy_Test_landscape", True), ("Pixel_10", False)):
            with self.subTest(name=name), patch("subprocess.run", return_value=response(
                    (name + "\r\r\nOK\r\r\n").encode())) as run:
                if allowed:
                    self.assertEqual(require_test_emulator(PREFIX), name)
                else:
                    with self.assertRaisesRegex(RuntimeError, "Refusing.*Pixel_10"):
                        require_test_emulator(PREFIX)
                self.assertEqual(run.call_count, 1)

    def test_similar_names_are_not_test_identities(self):
        for name in ("Rotation_Test_Copy", "rotation_test", "MyFairy_Test", "Fairy_Test other"):
            with self.subTest(name=name), patch("subprocess.run", return_value=response(
                    (name + "\nOK\n").encode())):
                with self.assertRaisesRegex(RuntimeError, "Refusing"):
                    require_test_emulator(PREFIX)

    def test_physical_devices_and_implicit_selection_never_contact_adb(self):
        for prefix in (["adb", "-s", "R5CT123456"], ["adb", "-s", "192.168.1.5:5555"],
                       ["adb"], ["adb", "-e", "emulator-5556"], ["adb", "-s", "emulator-"]):
            with self.subTest(prefix=prefix), patch("subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, "explicit test emulator"):
                    require_test_emulator(prefix)
                run.assert_not_called()

    def test_command_failure_does_not_trust_stdout_identity(self):
        with patch("subprocess.run", return_value=response(returncode=1, stderr=b"device offline")) as run:
            with self.assertRaisesRegex(RuntimeError, "Cannot verify.*device offline"):
                require_test_emulator(PREFIX)
            self.assertEqual(run.call_count, 1)

    def test_timeout_and_missing_adb_fail_closed(self):
        for error in (subprocess.TimeoutExpired("adb", 15), FileNotFoundError("adb")):
            with self.subTest(error=type(error).__name__), patch("subprocess.run", side_effect=error) as run:
                with self.assertRaisesRegex(RuntimeError, "Cannot verify test AVD"):
                    require_test_emulator(PREFIX)
                self.assertEqual(run.call_count, 1)

    def test_incomplete_or_ambiguous_identity_responses_fail_closed(self):
        for stdout in (b"", b"Rotation_Test\n", b"Rotation_Test\nKO\n",
                       b"Rotation_Test\nPixel_10\nOK\n", b"\nOK\n"):
            with self.subTest(stdout=stdout), patch("subprocess.run", return_value=response(stdout)) as run:
                with self.assertRaises(RuntimeError):
                    require_test_emulator(PREFIX)
                self.assertEqual(run.call_count, 1)

    def test_every_native_suite_rejects_before_test_or_cleanup_commands(self):
        # Exercise actual entry points, so a misplaced guard inside a cleanup
        # try/finally would be caught if rejection still force-stopped/relaunched.
        modules = sorted(path.stem for path in Path(__file__).parent.glob("*smoke_test.py"))
        self.assertGreaterEqual(len(modules), 8)
        with patch.object(Path, "mkdir"):
            for name in modules:
                module = importlib.import_module(name)
                for serial in ("emulator-5556", "R5CT123456"):
                    with self.subTest(module=name, serial=serial), patch.object(sys, "argv", [
                            name, "--serial", serial, "--output", "unused-native-test-output"]), patch("subprocess.run",
                            return_value=response(b"Pixel_10\r\nOK\r\n")) as run:
                        with self.assertRaises(RuntimeError):
                            module.main()
                        if serial.startswith("emulator-"):
                            run.assert_called_once_with(["adb", "-s", serial, "emu", "avd", "name"],
                                                        capture_output=True, timeout=15)
                        else:
                            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
