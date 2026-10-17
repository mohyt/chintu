"""Read-only setup inventory; never logs credentials or launches provider inference."""

import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess


def version(command):
    executable = shutil.which(command[0])
    if executable is None:
        return {"installed": False}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=8)
        match = re.search(r"\b\d+\.\d+(?:\.\d+)?\b", result.stdout + result.stderr)
        return {"installed": True, "responding": result.returncode == 0 and match is not None,
                "version": match.group() if match and result.returncode == 0 else None}
    except (OSError, subprocess.TimeoutExpired):
        return {"installed": True, "responding": False, "version": None}


def main():
    root = Path(__file__).resolve().parents[1]
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
               or root / "apps/android/.tooling/sdk")
    tools = {name: version(command) for name, command in {
        "uv": ["uv", "--version"], "codex": ["codex", "--version"],
        "claude": ["claude", "--version"], "tailscale": ["tailscale", "version"],
    }.items()}
    pinned = re.search(r'^GRADLE_VERSION = "([^"]+)"$', (root / "apps/android/tools/android.py").read_text(), re.M)
    local_gradle = root / "apps/android/.tooling/gradle-{}/bin/gradle".format(pinned.group(1) if pinned else "")
    report = {
        "host": {"system": platform.system(), "release": platform.release(),
                 "architecture": platform.machine(), "python": platform.python_version()},
        "tools": tools,
        "android": {"sdk_platform_35": (sdk / "platforms/android-35/android.jar").is_file(),
                    "build_tools_36": (sdk / "build-tools/36.0.0").is_dir(),
                    "sdk_license_record_present": (sdk / "licenses/android-sdk-license").is_file(),
                    "gradle_command_present": local_gradle.is_file() or bool(shutil.which("gradle")),
                    "adb_present": bool(shutil.which("adb")) or (sdk / "platform-tools/adb").is_file()},
        "acceptance": {"phone_model": None, "phone_android_version": None,
                       "speech_language": None, "ubuntu_host": None,
                       "chosen_provider": None, "reviewed_runtime_evidence": None},
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
