#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the permanent OnePlus 13 boot image for an ext4 Linux root."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

from common import boot_sections, make_cpio, read_cpio, sha256
from stock import boot_image

REPO = Path(__file__).resolve().parents[2]
PARTITION_BYTES = 96 * 1024 ** 2
DESKTOP = REPO / "devices/oneplus13/desktop"
PERSISTENT_PAYLOAD = (
    DESKTOP / "persistent-hardware.py",
    DESKTOP / "oneplus13-persistent-hardware.service",
    DESKTOP / "persistent-peripherals.py",
    DESKTOP / "oneplus13-persistent-peripherals.service",
    DESKTOP / "persistent-charger.py",
    DESKTOP / "oneplus13-charger.service",
    DESKTOP / "oneplus13-charger.target",
    DESKTOP.parent / "native-desktop.py",
    REPO / "tools/oneplus13/ram-display-prerequisites.py",
    REPO / "tools/oneplus13/ram-platform-prerequisites.sh",
    REPO / "devices/oneplus13/sensors/10-oneplus13-fastrpc.rules",
    REPO / "devices/oneplus13/sensors/49-oneplus13-sensor-proxy.rules",
    REPO / "switch/oneplus13/op13-switch",
    REPO / "switch/oneplus13/op13-reboot-android.desktop",
)


def derive(initramfs):
    entries = read_cpio(gzip.decompress(initramfs))
    text = entries["init"]["data"].decode()
    anchor = "# This runs continuously: later-arriving UFS disks are covered as well.\n"
    if text.count(anchor) != 1:
        raise ValueError("Unexpected OnePlus 13 init anchor")
    usb_start = text.index("# Composite USB NCM + serial console.")
    usb_end = text.index('\ncase " $(cat /proc/cmdline) " in\n', usb_start)
    usb_setup = "setup_usb() {\n" + text[usb_start:usb_end] + "\n}\n"
    text = text[:usb_start] + "setup_usb\n" + text[usb_end:]
    launch = (
        usb_setup + ". /bin/oneplus13-persistent-launch\n"
        "case \" $(cat /proc/cmdline) \" in\n"
        "  *' oneplus13.persistent=1 '*) persistent_main ;;\n"
        "esac\n"
    )
    text = text.replace(anchor, launch + anchor)
    ram_launch = "  *' oneplus13.desktop=1 '*) [ -n \"$udc\" ] && desktop_main ;;\n"
    if text.count(ram_launch) != 1:
        raise ValueError("Unexpected RAM desktop launch anchor")
    text = text.replace(ram_launch, "  *' oneplus13.persistent=1 '*) ;;\n" + ram_launch)
    entries["init"]["data"] = text.encode()
    helpers = ("persistent-prepare", "persistent-launch", "native-handoff")
    payload = [(DESKTOP / helper, "bin/oneplus13-" + helper) for helper in helpers]
    payload.extend((path, "op13-persistent/" + path.name) for path in PERSISTENT_PAYLOAD)
    entries["op13-persistent"] = {"mode": stat.S_IFDIR | 0o755, "major": 0, "minor": 0, "data": b""}
    for source, name in payload:
        if name in entries and source.name != "native-handoff":
            raise ValueError("Persistent helper already exists: " + name)
        body = source.read_bytes().replace(b"\r\n", b"\n")
        entries[name] = {
            "mode": stat.S_IFREG | 0o755,
            "major": 0,
            "minor": 0,
            "data": body,
        }
    packed = gzip.compress(
        make_cpio(
            [
                (name, entry["mode"], entry["data"], entry["major"], entry["minor"])
                for name, entry in sorted(entries.items())
            ]
        ),
        mtime=0,
    )
    check = read_cpio(gzip.decompress(packed))
    assert check["init"]["data"].decode().count("oneplus13.persistent=1") == 2
    for _, name in payload:
        assert check[name] == entries[name]
    return packed


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(
            "command failed: "
            + " ".join(map(str, argv))
            + "\n"
            + result.stdout[-4000:]
            + "\n"
            + result.stderr[-4000:]
        )
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-boot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mkbootimg", type=Path, required=True)
    parser.add_argument("--avbtool", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--partition-bytes", type=int, default=PARTITION_BYTES)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    if bool(args.avbtool) != bool(args.key):
        parser.error("--avbtool and --key must be supplied together")
    base = args.base_boot.resolve()
    image = base.read_bytes()
    geometry = boot_image(image)
    sections = boot_sections(image)
    if geometry["header_version"] != 2:
        raise ValueError("Persistent builder requires the verified v2 RAM boot")
    old_cmdline = image[64:576] + image[608:1632]
    old_cmdline = old_cmdline.split(b"\0", 1)[0].decode()
    ramdisk = derive(sections["ramdisk"])
    cmdline = (
        "rdinit=/init panic=15 oneplus13.desktop=1 oneplus13.persistent=1 "
        + ("oneplus13.persistent.debug=1 " if args.debug else "")
        +
        "oneplus13.root=PARTLABEL=linux_rootfs "
        "oneplus13.shared=PARTLABEL=shared_media "
        "qcom_battmgr.oneplus13_otg_auto=1 oneplus13.first_user_audio=1"
    )
    if len(cmdline) > 256:
        raise ValueError("Persistent cmdline exceeds boot header capacity")
    out = args.output.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="op13-persistent-") as temp:
        root = Path(temp)
        (root / "Image.gz").write_bytes(sections["kernel"])
        (root / "initramfs.cpio.gz").write_bytes(ramdisk)
        dtb = root / "dtb"
        dtb.write_bytes(sections["dtb"])
        # The base DTB carries the hardware-specific calibration. Replace only
        # its /chosen command line before packaging.
        sys.path.insert(0, str(REPO / "tools/oneplus13"))
        from stock import Fdt

        tree = Fdt(dtb.read_bytes())
        tree.nodes["/chosen"]["bootargs"] = cmdline.encode() + b"\0"
        dtb_payload = tree.to_blob()
        dtb.write_bytes(dtb_payload)
        command = [
            sys.executable,
            str(args.mkbootimg),
            "--header_version",
            "2",
            "--pagesize",
            "4096",
            "--base",
            "0",
            "--kernel_offset",
            str(struct.unpack_from("<I", image, 12)[0]),
            "--ramdisk_offset",
            str(struct.unpack_from("<I", image, 20)[0]),
            "--tags_offset",
            str(struct.unpack_from("<I", image, 32)[0]),
            "--dtb_offset",
            str(struct.unpack_from("<Q", image, 1652)[0]),
            "--kernel",
            str(root / "Image.gz"),
            "--ramdisk",
            str(root / "initramfs.cpio.gz"),
            "--dtb",
            str(dtb),
            "--cmdline",
            cmdline,
            "--output",
            str(out),
        ]
        run(command)
    unsigned_bytes = out.stat().st_size
    unsigned_sha = sha256(out)
    signed = False
    if args.avbtool:
        salt = hashlib.sha256(b"oneplus13-persistent-avb\0" + out.read_bytes()).hexdigest()
        run(
            [
                sys.executable,
                str(args.avbtool),
                "add_hash_footer",
                "--image",
                str(out),
                "--partition_name",
                "boot",
                "--partition_size",
                str(args.partition_bytes),
                "--rollback_index",
                "0",
                "--salt",
                salt,
                "--algorithm",
                "SHA256_RSA4096",
                "--key",
                str(args.key),
            ]
        )
        info = run([sys.executable, str(args.avbtool), "info_image", "--image", str(out)])
        if "Partition Name:        boot" not in info or "SHA256_RSA4096" not in info:
            raise ValueError("Signed boot identity readback failed")
        signed = True
    if out.stat().st_size != (args.partition_bytes if signed else unsigned_bytes):
        raise ValueError("Output boot image size differs from its declared geometry")
    manifest = {
        "scope": "PERSISTENT_OP13_BOOT_IMAGE",
        "base_boot_sha256": sha256(base),
        "original_cmdline": old_cmdline,
        "cmdline": cmdline,
        "kernel_sha256": hashlib.sha256(sections["kernel"]).hexdigest(),
        "initramfs_sha256": hashlib.sha256(ramdisk).hexdigest(),
        "base_dtb_sha256": hashlib.sha256(sections["dtb"]).hexdigest(),
        "dtb_sha256": hashlib.sha256(dtb_payload).hexdigest(),
        "unsigned_bytes": unsigned_bytes,
        "unsigned_sha256": unsigned_sha,
        "signed": signed,
        "output_bytes": out.stat().st_size,
        "output_sha256": sha256(out),
    }
    out.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
