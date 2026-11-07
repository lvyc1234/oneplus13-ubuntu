#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a RAM boot with reviewed DWC3 OTG role control."""

import argparse
import importlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import tempfile

from common import boot_sections, sha256
from stock import Fdt


REPO = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    args = parser.parse_args()
    kernel = args.kernel.resolve()
    out = args.image.resolve()
    original = json.loads((out / "modem-gnss-probe-manifest.json").read_text())
    km = json.loads((kernel / "manifest.json").read_text())
    if original["source_commit"] != km["integration_commit"]:
        raise ValueError("Different source components")
    sections = boot_sections((out / original["boot_image_file"]).read_bytes())
    if sections["kernel"] != (kernel / "Image.gz").read_bytes():
        raise ValueError("Changed modem-probe kernel")
    usb = importlib.import_module("usb-host-dtb")
    dtb, report = usb.derive(
        (REPO / "devices/oneplus13/qualified-data/calibrated.dtb").read_bytes()
    )
    nonce = secrets.token_hex(12)
    cmdline = original["cmdline"].replace(
        "oneplus13.session=" + original["session_identity_nonce"],
        "oneplus13.session=" + nonce,
    )
    if "oneplus13.usb_host_probe=1" not in cmdline.split():
        cmdline += " oneplus13.usb_host_probe=1"
    tree = Fdt(dtb)
    tree.nodes["/chosen"]["bootargs"] = (cmdline + "\0").encode()
    dtb = tree.to_blob()
    names = (
        "boot-usb-host-ram-experimental.img",
        "sm8750-oneplus13-usb-host-probe.dtb",
        "usb-host-probe-manifest.json",
        "usb-host-dtb-review.json",
    )
    paths = {name: out / name for name in names}
    if any(path.exists() for path in paths.values()):
        raise ValueError("Preserve prior USB host image candidates")
    dtb_path = paths["sm8750-oneplus13-usb-host-probe.dtb"]
    dtb_path.write_bytes(dtb)
    image = paths["boot-usb-host-ram-experimental.img"]
    lock = json.loads((REPO / "kernel/source.json").read_text())["tool_inputs"][
        "mkbootimg.py"
    ]
    archive = REPO / lock["bundled_archive"]
    if sha256(archive) != lock["archive_sha256"]:
        raise ValueError("Boot packer archive changed")
    geometry = json.loads(
        (REPO / "devices/oneplus13/qualified-data/boot-geometry.json").read_text()
    )
    with tempfile.TemporaryDirectory() as temporary:
        with tarfile.open(archive) as source:
            source.extractall(temporary, filter="data")
        mkboot = Path(temporary) / "mkbootimg.py"
        if sha256(mkboot) != lock["sha256"]:
            raise ValueError("Boot packer changed")
        ramdisk = Path(temporary) / "ramdisk.cpio.gz"
        ramdisk.write_bytes(sections["ramdisk"])
        subprocess.run(
            [
                sys.executable,
                str(mkboot),
                "--header_version",
                "2",
                "--pagesize",
                "4096",
                "--base",
                "0",
                "--kernel_offset",
                str(geometry["kernel_addr"]),
                "--ramdisk_offset",
                str(geometry["ramdisk_addr"]),
                "--tags_offset",
                str(geometry["tags_addr"]),
                "--dtb_offset",
                str(geometry["dtb_addr"]),
                "--kernel",
                str(kernel / "Image.gz"),
                "--ramdisk",
                str(ramdisk),
                "--dtb",
                str(dtb_path),
                "--cmdline",
                cmdline,
                "--output",
                str(image),
            ],
            check=True,
        )
    if boot_sections(image.read_bytes()) != {**sections, "dtb": dtb}:
        raise ValueError("Independent USB host boot readback failed")
    paths["usb-host-dtb-review.json"].write_text(
        json.dumps(report, indent=2) + "\n"
    )
    result = {
        **original,
        "scope": "SEPARATE_MANUAL_DWC3_USB_HOST_RAM_BOOT_NEVER_FLASH",
        "cmdline": cmdline,
        "session_identity_nonce": nonce,
        "boot_image_sha256": sha256(image),
        "boot_image_bytes": image.stat().st_size,
        "boot_image_file": image.name,
        "dtb_file": dtb_path.name,
        "dtb_sha256": sha256(dtb_path),
        "usb_host_hardware_qualified": False,
        "usb_host_peripheral_receipt": False,
        "default_boot_image_preserved": original["boot_image_sha256"],
    }
    paths["usb-host-probe-manifest.json"].write_text(
        json.dumps(result, indent=2) + "\n"
    )
    current = {
        **json.loads((out / "manifest.json").read_text()),
        "usb_host_probe_artifacts": {
            name: sha256(path) for name, path in paths.items()
        },
        "usb_host_probe_auto_started": False,
        "usb_host_hardware_qualified": False,
    }
    (out / "manifest.json").write_text(json.dumps(current, indent=2) + "\n")
    print(
        json.dumps(
            {
                "scope": result["scope"],
                "source_commit": result["source_commit"],
                "boot_image_sha256": result["boot_image_sha256"],
                "default_boot_image_preserved": True,
            }
        )
    )


if __name__ == "__main__":
    main()
