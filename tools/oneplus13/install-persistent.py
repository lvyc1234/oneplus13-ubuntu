#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install a verified clean bundle from the temporary OnePlus 13 RAM system."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(*args):
    return subprocess.check_output([str(arg) for arg in args], text=True,
                                   stderr=subprocess.STDOUT).strip()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_bundle(bundle):
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest["scope"] != "ONEPLUS13_CLEAN_PERSISTENT_AB_INSTALL":
        raise ValueError("Expected a persistent A/B installation bundle")
    required = {"boot.img", "rootfs.squashfs", "persistent-overlay.tar",
                "tools/op13-prepare-dualboot.py", "tools/package-switch.py"}
    if not required.issubset(manifest["files"]) or manifest["normal_users"] != 0:
        raise ValueError("Installation bundle is incomplete or has a precreated account")
    for name, digest in manifest["files"].items():
        target = (bundle / name).resolve()
        if not target.is_relative_to(bundle) or sha(target) != digest:
            raise ValueError("Bundle checksum mismatch: " + name)
    return manifest


def enter_installation_mode(rules=Path("/run/udev/rules.d"), runner=run):
    rules.mkdir(parents=True, exist_ok=True)
    names = ("09-oneplus13-ram-readonly.rules", "10-oneplus13-ram-storage.rules")
    for name in names:
        override = rules / name
        if (override.exists() or override.is_symlink()) and not (
            override.is_symlink() and override.readlink() == Path("/dev/null")
        ):
            raise ValueError("An unexpected installation rule override exists")
    runner("systemctl", "stop", "oneplus13-firstboot-hardware.service", "gdm.service",
           "oneplus13-ram-deadline.timer", "oneplus13-ram-guard.service")
    for name in names:
        override = rules / name
        if not override.is_symlink():
            override.symlink_to("/dev/null")
    runner("udevadm", "control", "--reload-rules")
    runner("udevadm", "settle", "--timeout=10")
    runner("blockdev", "--setrw", "/dev/sda")
    if runner("blockdev", "--getro", "/dev/sda") != "0":
        raise ValueError("The installation data disk remains read-only")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--mode", choices=("fresh", "replace-rootfs"), required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--erase-android", action="store_true")
    parser.add_argument("--erase-ubuntu", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root inside the temporary RAM system")
    os.umask(0o077)
    if args.apply and not (args.erase_android if args.mode == "fresh" else args.erase_ubuntu):
        parser.error("--apply requires the erase flag for the selected mode")
    lock = Path("/run/op13-dualboot.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    cmdline = Path("/proc/cmdline").read_text().split()
    if "oneplus13.persistent=1" in cmdline or not any(
        arg.startswith("oneplus13.rootfs.sha256=") for arg in cmdline
    ):
        raise ValueError("Boot boot-ram.img before installing")
    bundle = args.bundle.resolve()
    check_bundle(bundle)
    backup = args.backup.resolve()
    if not backup.is_dir() or any(backup.iterdir()):
        raise ValueError("Use an existing empty backup directory on the host or RAM disk")
    if run("findmnt", "-n", "-o", "FSTYPE", "-T", backup) not in ("tmpfs", "nfs", "nfs4", "9p", "virtiofs"):
        raise ValueError("Backups must be outside the phone's UFS storage")
    spec = importlib.util.spec_from_file_location("layout", bundle / "tools/op13-prepare-dualboot.py")
    layout = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(layout)
    if int(run("blockdev", "--getss", "/dev/sda")) != layout.SECTOR or int(
        run("blockdev", "--getsize64", "/dev/sda")
    ) != layout.EXPECTED_DISK_SECTORS * layout.SECTOR:
        raise ValueError("This installer supports the accepted 1 TB layout only")
    mounts = run("findmnt", "-rn", "-o", "MAJ:MIN").splitlines()
    for device in Path("/sys/class/block").glob("sda*"):
        if (device / "dev").read_text().strip() in mounts:
            raise ValueError("A data-disk partition is mounted")
    if args.mode == "fresh":
        table, command, sizes = layout.inspect()
    else:
        table = run("sgdisk", "--print", "/dev/sda")
        for number, label, size in ((15, "userdata", 448), (16, "linux_rootfs", 64)):
            dev = Path(f"/sys/class/block/sda{number}")
            if f"PARTNAME={label}" not in (dev / "uevent").read_text().splitlines():
                raise ValueError("Existing partition layout differs")
            if int(run("blockdev", "--getsize64", f"/dev/sda{number}")) != size * 1024 ** 3:
                raise ValueError("Existing partition size differs")
        if "PARTNAME=shared_media" not in Path("/sys/class/block/sda17/uevent").read_text().splitlines():
            raise ValueError("Existing shared partition missing")
        if run("blkid", "-p", "-s", "TYPE", "-o", "value", "/dev/sda17") != "ext4":
            raise ValueError("Existing shared filesystem is not ext4")
    run("sgdisk", "--backup=" + str(backup / "sda-gpt.bin"), "/dev/sda")
    run("sgdisk", "--backup=" + str(backup / "sde-gpt.bin"), "/dev/sde")
    for name in ("boot_a", "boot_b"):
        run("dd", "if=/dev/disk/by-partlabel/" + name, "of=" + str(backup / (name + ".img")), "bs=1M")
    (backup / "plan.json").write_text(json.dumps({"mode": args.mode, "apply": args.apply,
        "source_commit": check_bundle(bundle)["source_commit"], "gpt": table,
        "backup_sha256": {p.name: sha(p) for p in backup.iterdir() if p.is_file()}}, indent=2) + "\n")
    if not args.apply:
        print("PREFLIGHT_ONLY. Copy the backup directory to the host before installation.")
        return
    # The normal RAM session continually restores read-only block flags.
    # Enter installation mode only after checks and backups have succeeded.
    enter_installation_mode()
    if args.mode == "fresh":
        run(*command)
        run("partprobe", "/dev/sda")
        run("udevadm", "settle", "--timeout=10")
    rootdev, shareddev = "/dev/sda16", "/dev/sda17"
    for device in (rootdev, shareddev):
        run("blockdev", "--setrw", device)
    run("mkfs.ext4", "-q", "-F", "-L", "OP13_ROOT", rootdev)
    if args.mode == "fresh":
        run("mkfs.ext4", "-q", "-F", "-L", "OP13_SHARED", shareddev)
    root = Path("/mnt/op13-install-root")
    shared = Path("/mnt/op13-install-shared")
    root.mkdir(exist_ok=True)
    shared.mkdir(exist_ok=True)
    run("mount", rootdev, root)
    try:
        run("unsquashfs", "-f", "-no-progress", "-processors", "4", "-d", root, bundle / "rootfs.squashfs")
        for name in ("Documents", "Downloads", "Pictures", "Music", "Videos", "Shared"):
            path = root / "etc/skel" / name
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
        run("tar", "-xf", bundle / "persistent-overlay.tar", "-C", root)
        run("mount", shareddev, shared)
        try:
            for name in ("0/Android", "obb", "0/Download", "0/Documents", "0/Pictures", "0/Music", "0/Movies"):
                path = shared / name
                path.mkdir(parents=True, exist_ok=True)
            if args.mode == "fresh":
                run("chown", "-R", "1023:1023", shared)
                run("chmod", "0770", shared)
                for path in shared.rglob("*"):
                    path.chmod(0o2770)
            uuid = run("blkid", "-p", "-s", "UUID", "-o", "value", shareddev)
            run(sys.executable, bundle / "tools/package-switch.py", "--source", bundle / "switch/oneplus13",
                "--boot-b-sha256", sha(bundle / "boot.img"), "--shared-uuid", uuid,
                "--output", backup / "OnePlus13-DualBoot.zip")
            shutil.copyfile(backup / "OnePlus13-DualBoot.zip", shared / "0/Download/OnePlus13-DualBoot.zip")
        finally:
            run("sync")
            run("umount", shared)
    finally:
        run("sync")
        run("umount", root)
    # Leave Android A selected. The host flashes the verified B boot image
    # only after the filesystem installation and backup transfer succeed.
    print("FILESYSTEMS_INSTALLED. Transfer OnePlus13-DualBoot.zip and backups to the host.")
    print("Use Fastboot to flash boot_b, enable off-mode-charge, and select B as documented.")


if __name__ == "__main__":
    main()
