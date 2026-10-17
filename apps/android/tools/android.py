#!/usr/bin/env python3
"""Workspace-local Android build tools. Never accepts SDK licenses automatically."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT / ".tooling"
GRADLE_VERSION = "9.8.1"
GRADLE_SHA256 = "dce76f55f8e251a3a1f130eb120f30b3d271de2b76c9b0729d316b5a1b6dc01f"
# Archived Google repository metadata supplies SHA-1 for command-line tools 12.0.
# https://dl.google.com/android/repository/repository2-1.xml (remotePackage cmdline-tools;12.0)
ANDROID_ARCHIVES = {
    "darwin": ("mac", "37fb7dd41005b3b4ca6ea48ac27074b6fc4e3236"),
    "linux": ("linux", "d313adb7aedccf6cf0cfca51ec180f0059f5f8f8"),
}
PACKAGES = ["platforms;android-35", "build-tools;36.0.0", "platform-tools"]


def environment() -> dict[str, str]:
    env = os.environ.copy()
    if not env.get("JAVA_HOME"):
        homebrew_jdk = Path("/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")
        if homebrew_jdk.exists():
            env["JAVA_HOME"] = str(homebrew_jdk)
    if "JAVA_HOME" not in env:
        raise RuntimeError("Set JAVA_HOME to an installed JDK 17 or 21; this script does not install Java.")
    java = Path(env["JAVA_HOME"]) / "bin/java"
    if not java.is_file():
        raise RuntimeError("JAVA_HOME must contain bin/java.")
    # Tool caches/configuration remain inside this repository; HOME is untouched.
    env["GRADLE_USER_HOME"] = str(TOOLS / "gradle-home")
    env["ANDROID_USER_HOME"] = str(TOOLS / "android-user-home")
    env["ANDROID_HOME"] = str(Path(env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT") or str(TOOLS / "sdk")).resolve())
    env["ANDROID_SDK_ROOT"] = env["ANDROID_HOME"]
    return env


def download(url: str, target: Path, algorithm: str, expected: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and digest(target, algorithm) == expected:
        print(f"Verified cached {target.name}", flush=True)
        return
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        subprocess.run([
            "curl", "-L", "--fail", "--silent", "--show-error", "--retry", "2",
            "--retry-all-errors", "--connect-timeout", "15", "--max-time", "300",
            url, "-o", str(temporary),
        ], check=True)
        if digest(temporary, algorithm) != expected:
            raise RuntimeError(f"Checksum mismatch for {target.name}; archive not installed.")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def digest(path: Path, algorithm: str) -> str:
    result = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def unpack(archive: Path, destination: Path, archive_root: str) -> None:
    if destination.is_dir():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="unpack-", dir=TOOLS) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = (root / member.filename).resolve()
                if not path.is_relative_to(root.resolve()):
                    raise RuntimeError("Archive contains an unsafe member path.")
            bundle.extractall(root)
        source = root / archive_root
        if not source.is_dir():
            raise RuntimeError("Archive root does not match pinned tool layout.")
        shutil.move(source, destination)
    # Python ZIP extraction does not restore executable permissions.
    for file in (destination / "bin").iterdir():
        if file.is_file() and not file.name.endswith(".bat"):
            file.chmod(file.stat().st_mode | 0o111)


def fetch(env: dict[str, str]) -> None:
    if sys.platform not in ANDROID_ARCHIVES:
        raise RuntimeError("Bootstrap supports macOS/Linux. Use installed tools on other platforms.")
    downloads = TOOLS / "downloads"
    gradle = downloads / f"gradle-{GRADLE_VERSION}-bin.zip"
    download(f"https://services.gradle.org/distributions/{gradle.name}", gradle, "sha256", GRADLE_SHA256)
    unpack(gradle, TOOLS / f"gradle-{GRADLE_VERSION}", f"gradle-{GRADLE_VERSION}")
    host, checksum = ANDROID_ARCHIVES[sys.platform]
    android = downloads / f"commandlinetools-{host}-11076708_latest.zip"
    download(f"https://dl.google.com/android/repository/{android.name}", android, "sha1", checksum)
    unpack(android, Path(env["ANDROID_HOME"]) / "cmdline-tools/12.0", "cmdline-tools")
    print("Tool archives verified and installed. SDK packages are not installed or licensed.")
    print("Review licenses interactively: python3 tools/android.py licenses")


def sdkmanager(env: dict[str, str]) -> list[str]:
    tool = Path(env["ANDROID_HOME"]) / "cmdline-tools/12.0/bin/sdkmanager"
    if not tool.is_file():
        raise RuntimeError("Run python3 tools/android.py fetch first.")
    return [str(tool), f"--sdk_root={env['ANDROID_HOME']}"]


def install_sdk(env: dict[str, str]) -> None:
    license_file = Path(env["ANDROID_HOME"]) / "licenses/android-sdk-license"
    if not license_file.is_file() or not license_file.read_text().strip():
        raise RuntimeError("SDK license acceptance is missing. Review/answer licenses yourself using the licenses command.")
    # No yes-pipe or automated legal acceptance: any new license requirement fails on EOF.
    subprocess.run(sdkmanager(env) + PACKAGES, env=env, stdin=subprocess.DEVNULL, check=True)


def build(env: dict[str, str], extra: list[str]) -> None:
    gradle = TOOLS / f"gradle-{GRADLE_VERSION}/bin/gradle"
    if not gradle.is_file():
        installed = shutil.which("gradle")
        if not installed:
            raise RuntimeError(f"Run python3 tools/android.py fetch first, or provision Gradle {GRADLE_VERSION} on PATH.")
        version = subprocess.run([installed, "--version"], env=env, text=True,
                                 capture_output=True, check=True).stdout
        if not re.search(r"^Gradle " + re.escape(GRADLE_VERSION) + r"$", version, re.MULTILINE):
            raise RuntimeError(f"Installed Gradle must be version {GRADLE_VERSION}.")
        gradle = Path(installed)
    sdk = Path(env["ANDROID_HOME"])
    if not (sdk / "platforms/android-35/android.jar").is_file() or not (sdk / "build-tools/36.0.0").is_dir():
        raise RuntimeError("SDK Platform 35 / Build Tools 36.0.0 missing. Review licenses, then run the sdk command.")
    subprocess.run([str(gradle), "--no-daemon", "--console=plain", "-p", str(PROJECT)] +
                   (extra or [":app:assembleDebug", ":app:lintDebug"]), env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "licenses", "sdk", "build", "check"])
    parser.add_argument("gradle_tasks", nargs="*")
    args = parser.parse_args()
    try:
        env = environment()
        if args.command == "fetch":
            fetch(env)
        elif args.command == "licenses":
            if not sys.stdin.isatty():
                raise RuntimeError("License review requires an interactive terminal; no automatic acceptance is supported.")
            subprocess.run(sdkmanager(env) + ["--licenses"], env=env, check=True)
        elif args.command == "sdk":
            install_sdk(env)
        elif args.command == "build":
            build(env, args.gradle_tasks)
        else:
            print(f"JAVA_HOME={env['JAVA_HOME']}")
            print(f"ANDROID_HOME={env['ANDROID_HOME']}")
            print(f"GRADLE_USER_HOME={env['GRADLE_USER_HOME']}")
            for package in ["platforms/android-35/android.jar", "build-tools/36.0.0", "licenses/android-sdk-license"]:
                print(f"{package}: {'present' if (Path(env['ANDROID_HOME']) / package).exists() else 'missing'}")
        return 0
    except (RuntimeError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"Android tools: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
