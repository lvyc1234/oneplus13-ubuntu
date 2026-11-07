#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare OnePlus 13 persistent dual boot and one shared media pool."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess

DISK = "/dev/sda"
BOOT_DISK = "/dev/sde"
SECTOR = 4096
GIB = 1024 ** 3
ANDROID_GIB = 448
ROOT_GIB = 64
EXPECTED_DISK_SECTORS = 249145344
EXPECTED_USERDATA_START = 4117956


def run(*args):
    return subprocess.check_output(
        args, text=True, stderr=subprocess.STDOUT, timeout=60,
        env={**os.environ, "LC_ALL": "C"},
    )


def layout(info, last):
    start = int(re.search(r"First sector: (\d+)", info)[1])
    end = int(re.search(r"Last sector: (\d+)", info)[1])
    if start != EXPECTED_USERDATA_START or end != last:
        raise RuntimeError("Unexpected stock OnePlus 13 userdata geometry")
    guid = re.search(r"Partition unique GUID: ([A-Fa-f0-9-]+)", info)[1]
    kind = re.search(r"Partition GUID code: ([A-Fa-f0-9-]+)", info)[1]
    flags = re.search(r"Attribute flags: ([A-Fa-f0-9]+)", info)[1]
    android_end = start + ANDROID_GIB * GIB // SECTOR - 1
    align = 8 * 1024 ** 2 // SECTOR
    root_start = (android_end + 1 + align - 1) // align * align
    root_end = root_start + ROOT_GIB * GIB // SECTOR - 1
    shared_start = root_end + 1
    if last - shared_start < 300 * GIB // SECTOR:
        raise RuntimeError("Shared media partition would be too small")
    command = [
        "sgdisk", "--set-alignment=1", "--delete=15",
        f"--new=15:{start}:{android_end}", f"--typecode=15:{kind}",
        f"--partition-guid=15:{guid}", f"--attributes=15:=:0x{flags}",
        "--change-name=15:userdata",
        f"--new=16:{root_start}:{root_end}", "--typecode=16:8300",
        "--change-name=16:linux_rootfs",
        f"--new=17:{shared_start}:{last}", "--typecode=17:8300",
        "--change-name=17:shared_media", DISK,
    ]
    sizes = {
        "userdata": (android_end - start + 1) * SECTOR,
        "linux_rootfs": (root_end - root_start + 1) * SECTOR,
        "shared_media": (last - shared_start + 1) * SECTOR,
    }
    return command, sizes


def inspect():
    cmdline = Path("/proc/cmdline").read_text().split()
    if not any(x.startswith("oneplus13.rootfs.sha256=") for x in cmdline):
        raise RuntimeError("Boot the temporary OnePlus 13 Linux RAM image first")
    if not (Path("/sys/firmware/devicetree/base/model").exists()):
        raise RuntimeError("Device tree model unavailable")
    if int(run("blockdev", "--getss", DISK)) != SECTOR:
        raise RuntimeError("Expected 4096-byte sectors")
    disk_bytes = int(run("blockdev", "--getsize64", DISK))
    if disk_bytes != EXPECTED_DISK_SECTORS * SECTOR:
        raise RuntimeError("Unexpected OnePlus 13 disk size")
    for slot, number in (("a", 12), ("b", 48)):
        info = run("sgdisk", f"--info={number}", BOOT_DISK)
        attrs = int(re.search(r"Attribute flags: ([0-9A-Fa-f]+)", info)[1], 16)
        active = bool(attrs & (1 << 50))
        unbootable = bool(attrs & (1 << 55))
        if slot == "a" and (not active or unbootable):
            raise RuntimeError("Slot A is not the active usable Android slot")
    table = run("sgdisk", "--print", DISK)
    names = {
        int(number): name
        for number, name in re.findall(
            r"^\s*(\d+)\s+\d+\s+\d+\s+.*?\s+[0-9A-F]{4}\s+(\S+)\s*$",
            table,
            re.M,
        )
    }
    if names.get(15) != "userdata" or max(names, default=0) != 15:
        raise RuntimeError("Expected stock userdata as the last partition")
    if any(name in ("linux_rootfs", "shared_media") for name in names.values()):
        raise RuntimeError("A previous Linux layout already exists")
    if any(
        line.startswith(DISK)
        for line in run("findmnt", "-rn", "-o", "SOURCE").splitlines()
    ):
        raise RuntimeError("A partition on the eMMC is mounted")
    last = int(re.search(r"last usable sector is (\d+)", table)[1])
    info = run("sgdisk", "--info=15", DISK)
    command, sizes = layout(info, last)
    return table, command, sizes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--erase-android", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run as root in the temporary Linux RAM image")
    if args.apply != args.erase_android:
        parser.error("--apply requires --erase-android")
    os.umask(0o077)
    with Path("/run/op13-dualboot.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        table, command, sizes = inspect()
        plan = {
            "scope": "ONEPLUS13_DESTRUCTIVE_DUALBOOT_LAYOUT",
            "android_userdata_erased": bool(args.apply),
            "disk": DISK,
            "sector_bytes": SECTOR,
            "partitions": sizes,
            "command": command,
            "gpt_table": table,
        }
        if not args.apply:
            args.record.write_text(json.dumps(plan, indent=2) + "\n")
            run("sgdisk", "--backup=" + str(args.backup), DISK)
            print(table)
            print(json.dumps({"android_gib": sizes["userdata"] / GIB,
                              "root_gib": sizes["linux_rootfs"] / GIB,
                              "shared_gib": sizes["shared_media"] / GIB}, indent=2))
            return
        saved = json.loads(args.record.read_text())
        if saved["gpt_table"] != table or saved["command"] != command:
            raise RuntimeError("Saved GPT plan no longer matches this device")
        print("Destroying Android userdata and applying the dual-boot GPT", flush=True)
        run(*command)
        verify = run("sgdisk", "--verify", DISK)
        if "No problems found" not in verify:
            raise RuntimeError("GPT verification failed; restore the saved GPT")
        print(verify)


if __name__ == "__main__":
    main()
