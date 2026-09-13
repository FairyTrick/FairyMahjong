"""Keep native smoke tests away from the emulator used for normal play."""

import re
import subprocess


def require_test_emulator(prefix):
    """Verify the selected AVD before any test or cleanup can modify the device.

    Ports are not identities: an emulator on 5556 can still be the user's AVD.
    Only explicitly named test AVDs may receive temporary saves or UI actions.
    """
    instruction = "Use a dedicated AVD named Rotation_Test or starting with Fairy_Test."
    if (len(prefix) != 3 or prefix[1] != "-s"
            or not re.fullmatch(r"emulator-\d+", prefix[2])):
        raise RuntimeError("Native smoke tests require an explicit test emulator; "
                           "physical devices are not allowed. " + instruction)
    serial = prefix[2]
    try:
        result = subprocess.run(list(prefix) + ["emu", "avd", "name"],
                                capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Cannot verify test AVD on {serial}; no device changes made. "
                           + instruction) from error
    if result.returncode:
        detail = (result.stderr or result.stdout).decode(errors="replace").strip()
        raise RuntimeError(f"Cannot verify test AVD on {serial}: {detail}. "
                           "No device changes made. " + instruction)
    # Windows ADB can emit CR CR LF; splitlines then includes empty entries.
    lines = [line.strip() for line in result.stdout.decode(errors="replace").splitlines()
             if line.strip()]
    if len(lines) != 2 or lines[1] != "OK":
        raise RuntimeError(f"Unexpected AVD identity response from {serial}; "
                           "no device changes made. " + instruction)
    name = lines[0]
    if name != "Rotation_Test" and not re.fullmatch(r"Fairy_Test[A-Za-z0-9_.-]*", name):
        raise RuntimeError(f"Refusing native smoke test on AVD {name!r} ({serial}); "
                           "no device changes made. " + instruction)
    return name
