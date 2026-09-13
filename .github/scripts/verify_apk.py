#!/usr/bin/env python3
"""Check the actual release manifest before publishing an Android build."""

import argparse
from pathlib import Path
import re
import subprocess
import zipfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aapt", required=True)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    build = (root / "app/build.gradle.kts").read_text(encoding="utf-8")
    version_name = re.search(r'\bversionName\s*=\s*"([^"]+)"', build)
    version_code = re.search(r"\bversionCode\s*=\s*(\d+)", build)
    require(version_name is not None and version_code is not None,
            "Use literal versionName and versionCode values for F-Droid updates")
    expected_name = version_name.group(1)
    expected_code = version_code.group(1)

    badging = subprocess.check_output(
        [args.aapt, "dump", "badging", str(args.apk)], text=True, encoding="utf-8"
    )
    package = re.search(
        r"^package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'",
        badging, re.MULTILINE,
    )
    require(package is not None, "Cannot read the built APK's package identity")
    require(package.groups() == ("com.fairytrick.fairymahjong", expected_code, expected_name),
            f"Unexpected APK identity: {package.groups()}")
    require("application-debuggable" not in badging, "The release APK is debuggable")
    require("android.permission.INTERNET" not in badging,
            "The offline game must not request Internet access")
    require("sdkVersion:'26'" in badging, "Unexpected minimum Android API level")
    require("application-label:'FairyMahjong'" in badging, "Unexpected app name")

    with zipfile.ZipFile(args.apk) as archive:
        for name in ("LICENSE", "NOTICE"):
            bundled = archive.read(f"assets/licenses/{name}").decode("utf-8-sig").splitlines()
            source = (root / name).read_text(encoding="utf-8-sig").splitlines()
            require(bundled == source, f"The APK must contain the current {name}")

    changelog = root / f"fastlane/metadata/android/en-US/changelogs/{expected_code}.txt"
    require(changelog.is_file(), f"Missing version-code changelog: {changelog}")
    require(0 < len(changelog.read_text(encoding="utf-8").strip()) <= 500,
            "The F-Droid changelog must contain 1–500 characters")
    if args.tag:
        require(re.fullmatch(r"v\d+\.\d+\.\d+", args.tag) is not None,
                "Release tags must use stable vMAJOR.MINOR.PATCH format")
        require(args.tag == f"v{expected_name}",
                f"Tag {args.tag} does not match APK version {expected_name}")

    print(f"Verified FairyMahjong {expected_name} ({expected_code}); tag={args.tag or 'untagged'}")


if __name__ == "__main__":
    main()
