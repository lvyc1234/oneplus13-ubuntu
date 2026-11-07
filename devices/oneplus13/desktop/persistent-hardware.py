#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
"""Start the native panel and touch controller on the installed Linux root."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import time


PAYLOAD = Path(__file__).resolve().parent
ROOT = Path("/")
TOUCH_NAME = "OnePlus 13 Synaptics S3910 LBP"


def validate_root(root):
    cmdline = (root / "proc/cmdline").read_text().split()
    marker = root / "run/evidence/rootfs.txt"
    if (
        "oneplus13.persistent=1" not in cmdline
        or "oneplus13.root=PARTLABEL=linux_rootfs" not in cmdline
        or (root / "run/oneplus13/ram-session").exists()
        or marker.read_text().strip() != "ONEPLUS13_PERSISTENT_ROOT_READY"
    ):
        raise ValueError("Persistent startup handoff is missing")

    allowed = {"/": "linux_rootfs", "/mnt/shared": "shared_media"}
    roots = 0
    for line in (root / "proc/self/mountinfo").read_text().splitlines():
        mount, filesystem = line.split(" - ", 1)
        fields = mount.split()
        target, options = fields[4], fields[5].split(",")
        device = root / "sys/dev/block" / fields[2]
        if target not in allowed:
            if device.exists() and "rw" in options:
                identity = (device / "uevent").read_text().splitlines()
                if filesystem.split()[0] != "ext4" or not any(
                    f"PARTNAME={label}" in identity for label in allowed.values()
                ):
                    raise ValueError(f"Unexpected writable block mount at {target}")
            continue
        uevent = (device / "uevent").read_text().splitlines()
        if (
            filesystem.split()[0] != "ext4"
            or fields[3] != "/"
            or f"PARTNAME={allowed[target]}" not in uevent
            or (target == "/" and "rw" not in options)
        ):
            raise ValueError(f"Unexpected persistent mount at {target}")
        roots += target == "/"
    if roots != 1:
        raise ValueError("Expected one writable linux_rootfs mount")


def run(*args, timeout=15, env=None):
    subprocess.run(args, check=True, timeout=timeout, env=env)


def start_adsp():
    processors = [
        device / "state"
        for device in Path("/sys/class/remoteproc").glob("remoteproc*")
        if "adsp" in (device / "name").read_text().strip().lower()
    ]
    if len(processors) != 1:
        raise ValueError("Expected exactly one audio DSP")
    state = processors[0]
    if state.read_text().strip() == "offline":
        run("/bin/sh", "-c", 'echo start > "$1"', "_", str(state), timeout=25)
    if state.read_text().strip() != "running":
        raise ValueError("Audio DSP failed to start")


def wait_for_display():
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        panel = any(
            status.read_text().strip() == "connected"
            for status in Path("/sys/class/drm").glob("card*-DSI-*/status")
        )
        touch = any(
            name.read_text().strip() == TOUCH_NAME
            for name in Path("/sys/class/input").glob("event*/device/name")
        )
        if panel and touch and list(Path("/dev/dri").glob("renderD*")):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Display devices did not become ready (panel={panel}, touch={touch})")


def main():
    if os.geteuid() != 0:
        raise ValueError("Root is required")
    validate_root(ROOT)
    native = runpy.run_path(str(PAYLOAD / "native-desktop.py"))
    prerequisites = runpy.run_path(str(PAYLOAD / "ram-display-prerequisites.py"))
    release = os.uname().release

    def display_preflight():
        validate_root(ROOT)
        native["validate_payload"](ROOT, release)
        native["check_module_policy"](release)

    display_preflight()
    bindings = json.loads(
        Path("/usr/lib/oneplus13-desktop/runtime-bindings.private.json").read_text()
    )
    if bindings["kernel_release"] != release:
        raise ValueError("Hardware bindings do not match the running kernel")
    for name, expected in bindings["firmware_sha256"].items():
        firmware = Path("/usr/lib/firmware") / name
        if hashlib.sha256(firmware.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Firmware hash mismatch for {name}")
    policy = ROOT / prerequisites["POLICY"]
    if policy.is_symlink():
        raise ValueError("Unexpected symlink for the module policy")
    policy_bytes = policy.read_bytes()
    policy_hash = bindings["MODULE_POLICY_SHA256"]
    if hashlib.sha256(policy_bytes).hexdigest() != policy_hash:
        raise ValueError("Persistent root module policy hash mismatch")

    env = {
        **os.environ,
        "PLATFORM_KERNEL_RELEASE": release,
        "CPUCP_MODULE_SHA256": bindings["CPUCP_MODULE_SHA256"],
        "SOCINFO_MODULE_SHA256": bindings["SOCINFO_MODULE_SHA256"],
    }
    platform_script = 'source "$1"; platform_prepare 30'
    if "oneplus13.persistent.debug=1" in Path("/proc/cmdline").read_text().split():
        platform_script = 'set -x; ' + platform_script
    run(
        "/bin/bash", "-c", platform_script, "_",
        str(PAYLOAD / "ram-platform-prerequisites.sh"), env=env, timeout=90,
    )
    start_adsp()
    run("/usr/sbin/modprobe", "dodge-s3910")

    # The prerequisite loader updates these two install rules after loading
    # their exact modules. Give it a writable copy backed by /run.
    runtime_policy = PAYLOAD / policy.name
    with runtime_policy.open("xb") as stream:
        stream.write(policy_bytes)
    run("/usr/bin/mount", "--bind", str(runtime_policy), str(policy))
    prerequisites["prepare"](
        ROOT, release, bindings["CEC_MODULE_SHA256"], policy_hash,
        bindings["UBWC_MODULE_SHA256"], validate_boot=lambda root, run: validate_root(root),
    )
    native["activate_checked"](ROOT, display_preflight, native["command"])
    wait_for_display()
    print("ONEPLUS13_PERSISTENT_NATIVE_DISPLAY_AND_TOUCH_READY", flush=True)


if __name__ == "__main__":
    main()
