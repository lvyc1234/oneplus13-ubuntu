#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Load, transmit through and unload the reviewed OnePlus 13 IR driver."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time


POLICY = Path("/etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf")
RAM_MARKER = Path("/run/oneplus13/ram-session")
MODULE = "ir_dodge_spi"
AUDIO_HELPER = Path("/usr/libexec/oneplus13-ram-audio-check.py")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args, timeout=8):
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


def module_closure(release):
    spec = importlib.util.spec_from_file_location(
        "ir_module_order", AUDIO_HELPER
    )
    audio = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audio)
    dependency = Path("/usr/lib/modules") / release / "modules.dep"
    ordered = audio.module_order(dependency.read_text(), [MODULE])
    if (
        MODULE not in ordered
        or len(ordered) > 64
        or any(
            not re.fullmatch(r"[A-Za-z0-9_+-]+", name)
            for name in ordered
        )
    ):
        raise ValueError("Unsafe IR module dependency closure")
    return ordered


def allow_modules(module_names):
    if not RAM_MARKER.is_file():
        raise RuntimeError("Protected phone RAM session required")
    text = POLICY.read_text(encoding="utf-8")
    denied = {
        line
        for name in module_names
        for line in (
            "blacklist " + name,
            "install " + name + " /bin/false",
        )
    }
    lines = text.splitlines()
    root_denies = {
        "blacklist " + MODULE,
        "install " + MODULE + " /bin/false",
    }
    if any(lines.count(line) != 1 for line in root_denies):
        raise ValueError("Exact IR module deny policy is absent")
    lines = [line for line in lines if line not in denied]
    POLICY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sorted(denied)


def devices():
    result = []
    lirc = sorted(Path("/sys/class/lirc").glob("lirc[0-9]*"))
    for path in sorted(Path("/sys/class/rc").glob("rc[0-9]*")):
        index = path.name.removeprefix("rc")
        lirc_path = Path("/sys/class/lirc/lirc" + index)
        dev = lirc_path / "dev"
        result.append(
            {
                "name": path.name,
                "driver": (path / "device/driver").resolve(strict=False).name
                if (path / "device/driver").exists()
                else None,
                "device": str(path.resolve(strict=False)),
                "lirc_class_present": lirc_path.is_dir(),
                "lirc_device": Path("/dev/lirc" + index).is_char_device(),
                "lirc_dev": dev.read_text().strip() if dev.is_file() else None,
                "protocols": (path / "protocols").read_text().strip()
                if (path / "protocols").is_file()
                else None,
            }
        )
    return result, [path.name for path in lirc]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--transmit", action="store_true")
    args = parser.parse_args()
    before_policy = sha256(POLICY)
    if before_policy != args.policy_sha256:
        raise ValueError("Module policy hash mismatch")
    ordered = module_closure(os.uname().release)
    original_policy = POLICY.read_bytes()
    removed = allow_modules(ordered)
    result = {
        "scope": "ONEPLUS13_RAM_SPI_INFRARED_CONTROL",
        "policy_sha256_before": before_policy,
        "allowed_policy_entries_removed": removed,
        "module_closure": ordered,
        "driver_loaded": False,
        "rc_device_present": False,
        "transmit_syscall_pass": False,
        "optical_output_qualified": False,
        "cleanup_complete": False,
    }
    loaded = []
    pass_ = False
    try:
        for name in ordered:
            if Path("/sys/module", name.replace("-", "_")).is_dir():
                continue
            load = run(["modprobe", "--ignore-install", name], timeout=20)
            if load.returncode:
                result["load"] = {
                    "module": name,
                    "returncode": load.returncode,
                    "stdout": load.stdout[-2048:],
                    "stderr": load.stderr[-2048:],
                }
                break
            loaded.append(name)
        result["loaded_modules"] = loaded
        result["driver_loaded"] = Path("/sys/module", MODULE).is_dir()
        deadline = time.monotonic() + 8
        rows = []
        selected = []
        while time.monotonic() < deadline:
            rows, lirc_entries = devices()
            selected = [
                row for row in rows
                if row["driver"] == "ir-dodge-spi" and row["lirc_device"]
            ]
            if selected:
                break
            time.sleep(0.25)
        result["devices"] = rows
        result["lirc_class_entries"] = lirc_entries
        result["selected"] = selected
        result["rc_device_present"] = bool(selected)
        pass_ = result["driver_loaded"] and result["rc_device_present"]
        if args.transmit and selected:
            pattern = Path("/run/oneplus13-ir-check.lirc")
            pattern.parent.mkdir(parents=True, exist_ok=True)
            pattern.write_text(
                "carrier 38000\n"
                "pulse 560\n"
                "space 560\n"
                "pulse 560\n"
                "space 1690\n"
                "pulse 560\n",
                encoding="ascii",
            )
            transmit = run(
                [
                    "ir-ctl",
                    "-d",
                    "/dev/lirc0",
                    "-c",
                    "38000",
                    "-D",
                    "33",
                    "-s",
                    str(pattern),
                ],
                timeout=15,
            )
            result["transmit"] = {
                "returncode": transmit.returncode,
                "stdout": transmit.stdout[-2048:],
                "stderr": transmit.stderr[-2048:],
            }
            result["transmit_syscall_pass"] = transmit.returncode == 0
            pass_ = pass_ and result["transmit_syscall_pass"]
    finally:
        unloads = []
        for name in reversed(loaded):
            unload = run(["modprobe", "-r", name], timeout=15)
            unloads.append(
                {"module": name, "returncode": unload.returncode}
            )
        POLICY.write_bytes(original_policy)
        os.chmod(POLICY, 0o644)
        result["unloads"] = unloads
        result["policy_sha256_after"] = sha256(POLICY)
        result["policy_restored"] = (
            result["policy_sha256_after"] == before_policy
        )
        cleanup_rows, _ = devices()
        cleanup = not any(
            Path("/sys/module", name.replace("-", "_")).exists() for name in loaded
        ) and not any(
            row["driver"] == "ir-dodge-spi" for row in cleanup_rows
        )
        result["cleanup_complete"] = (
            result["policy_restored"]
            and cleanup
            and all(row["returncode"] == 0 for row in unloads)
        )
        result["pass"] = pass_ and result["cleanup_complete"]
    print("RAM_IR_RESULT " + json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
