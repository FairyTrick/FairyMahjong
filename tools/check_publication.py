#!/usr/bin/env python3
"""Check staged source files and scan reachable Git history for secrets."""

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RASTER = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp"}
PRIVATE_PARTS = {
    ".nocommit", ".local", "artifacts", ".codex", ".agents", ".ssh", "build",
    ".codex-remote-attachments", ".gradle", ".kotlin", ".idea", "__pycache__",
    "pixietrick-sec", "character-style-references", "reference-art",
}
PRIVATE_NAMES = {
    "gh.txt", "ghorg.txt", "token.txt", "github-token.txt", "credentials.json",
    "credentials", "local.properties", "keystore.properties", "signing.properties",
    "id_rsa", "id_rsa.pub", "id_ed25519", "id_ed25519.pub",
    "id_ecdsa", "id_ecdsa.pub", "id_dsa", "id_dsa.pub",
}
PRIVATE_SUFFIXES = {".jks", ".keystore", ".p12", ".pfx", ".pem", ".key"}
BINARY_SUFFIXES = {
    ".apk", ".aab", ".apks", ".aar", ".jar", ".class", ".dex", ".so",
    ".dll", ".exe", ".zip", ".7z", ".tar", ".gz", ".pdf", ".mp4", ".mov",
    ".sqlite", ".db", ".ttf", ".otf", ".woff", ".woff2", ".pyc", ".pyo",
}
WRAPPER = "gradle/wrapper/gradle-wrapper.jar"
# https://gradle.org/release-checksums/ (Gradle 9.6.0)
WRAPPER_SHA256 = "497c8c2a7e5031f6aa847f88104aa80a93532ec32ee17bdb8d1d2f67a194a9c7"
SECRET_RULES = {
    "private key marker": rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    "GitHub token marker": rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "OpenAI key marker": rb"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}",
    "AWS access key marker": rb"(?:AKIA|ASIA)[A-Z0-9]{16}",
}


class CheckError(Exception):
    pass


def git(*args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise CheckError("Git inspection failed; check repository access and full history.")
    return result.stdout


def path_problem(name: str, current: bool) -> str | None:
    path = PurePosixPath(name)
    parts = tuple(part.lower() for part in path.parts)
    if not parts or path.is_absolute() or ".." in parts or "\\" in name:
        return "unsafe repository path"
    if any(part in PRIVATE_PARTS for part in parts) or parts[-1] in PRIVATE_NAMES:
        return "private or generated file"
    if parts[-1] == ".env" or parts[-1].startswith(".env."):
        return "environment secrets file"
    if any(part.startswith("codex-clipboard-") for part in parts):
        return "clipboard file"
    suffix = path.suffix.lower()
    if suffix in PRIVATE_SUFFIXES:
        return "signing or private key file"
    if current and suffix in BINARY_SUFFIXES and name != WRAPPER:
        return "unexpected binary file"
    if current and suffix in RASTER:
        allowed = name.startswith(("app/src/main/assets/", "app/src/main/res/"))
        allowed |= name.startswith("fastlane/metadata/") and "images" in path.parts[2:-1]
        if not allowed:
            return "image outside app resources or listing images"
    return None


@dataclass(frozen=True)
class BlobInfo:
    binary: bool
    digest: str
    header: bytes
    secrets: tuple[str, ...]


def describe(data: bytes) -> BlobInfo:
    try:
        data.decode("utf-8")
        binary = b"\x00" in data
    except UnicodeDecodeError:
        binary = True
    return BlobInfo(binary, hashlib.sha256(data).hexdigest(), data[:16],
                    tuple(label for label, rule in SECRET_RULES.items() if re.search(rule, data)))


def inspect_blob(name: str, info: BlobInfo, current: bool) -> list[str]:
    problems = list(info.secrets)
    if not current:
        return problems
    suffix = PurePosixPath(name).suffix.lower()
    if name == WRAPPER:
        if info.digest != WRAPPER_SHA256:
            problems.append("Gradle wrapper differs from verified official checksum")
    elif suffix in RASTER:
        h = info.header
        signatures = {
            ".png": h.startswith(b"\x89PNG\r\n\x1a\n"),
            ".jpg": h.startswith(b"\xff\xd8\xff"), ".jpeg": h.startswith(b"\xff\xd8\xff"),
            ".gif": h.startswith((b"GIF87a", b"GIF89a")), ".bmp": h.startswith(b"BM"),
            ".webp": h[:4] == b"RIFF" and h[8:12] == b"WEBP",
            ".avif": h[4:8] == b"ftyp" and h[8:12] in (b"avif", b"avis"),
        }
        if not signatures[suffix]:
            problems.append("image extension does not match file content")
    elif info.binary:
        problems.append("unexpected binary content")
    return problems


class GitBlobs:
    def __init__(self):
        self.process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL)
        self.cache: dict[str, BlobInfo] = {}

    def info(self, oid: str) -> BlobInfo:
        if oid not in self.cache:
            self.process.stdin.write((oid + "\n").encode("ascii"))
            self.process.stdin.flush()
            header = self.process.stdout.readline().split()
            if len(header) != 3 or header[1] != b"blob":
                raise CheckError("Cannot read a tracked Git blob.")
            size = int(header[2])
            data = self.process.stdout.read(size)
            if len(data) != size or self.process.stdout.read(1) != b"\n":
                raise CheckError("Incomplete Git blob read.")
            self.cache[oid] = describe(data)
        return self.cache[oid]

    def close(self):
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.wait(timeout=10)


def entries(commit: str | None = None):
    command = ("ls-tree", "-r", "-z", "--full-tree", commit) if commit else ("ls-files", "--stage", "-z")
    for record in git(*command).split(b"\x00"):
        if not record:
            continue
        meta, name = record.split(b"\t", 1)
        fields = meta.decode("ascii").split()
        if not commit and fields[2] != "0":
            raise CheckError("Resolve the Git index conflict before publication.")
        yield name.decode("utf-8"), fields[0], fields[2] if commit else fields[1]


def main() -> int:
    try:
        if git("rev-parse", "--is-shallow-repository").strip() == b"true":
            raise CheckError("Full history is required; configure checkout fetch-depth: 0.")
        problems = set()
        seen_history = set()
        blobs = GitBlobs()
        try:
            commits = git("rev-list", "--all").decode("ascii").splitlines()
            for commit in [None, *commits]:
                current = commit is None
                label = "index" if current else "history " + commit[:12]
                for name, mode, oid in entries(commit):
                    if not current:
                        key = (name, mode, oid)
                        if key in seen_history:
                            continue
                        seen_history.add(key)
                    reason = path_problem(name, current)
                    if reason:
                        problems.add(f"{label}: {name}: {reason}")
                        continue
                    if mode not in {"100644", "100755"}:
                        problems.add(f"{label}: {name}: unsupported symlink or submodule")
                        continue
                    for reason in inspect_blob(name, blobs.info(oid), current):
                        problems.add(f"{label}: {name}: {reason}")
        finally:
            blobs.close()
        if problems:
            print("\n".join(sorted(problems)), file=sys.stderr)
            raise CheckError("No secret values were printed.")
        print("Publication check passed.")
        return 0
    except (CheckError, OSError, UnicodeError) as error:
        print(f"Publication check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
