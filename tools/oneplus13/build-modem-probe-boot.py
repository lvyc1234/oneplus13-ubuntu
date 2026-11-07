#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a separate manual MPSS/GNSS RAM boot and firmware archive."""

import argparse
import gzip
import hashlib
import importlib
import io
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


def deterministic_tar(firmware, destination):
    names = sorted(path.name for path in firmware.iterdir() if path.is_file())
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name in names:
            path = firmware / name
            info = archive.gettarinfo(str(path), arcname="qcom/sm8750/oneplus/dodge/" + name)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = 0
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    with destination.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as packed:
            packed.write(raw.getvalue())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--firmware", required=True, type=Path)
    parser.add_argument("--firmware-review", required=True, type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    kernel = args.kernel.resolve()
    out = args.image.resolve()
    firmware = args.firmware.resolve()
    review = json.loads(args.firmware_review.read_text())
    if review.get("scope") != "HOST_C93_MODEM_PIL_STRUCTURE_AND_CARVEOUT_REVIEW":
        raise ValueError("Wrong modem firmware review scope")
    expected = review["firmware_sha256"] | {
        "modemr.jsn": review["resource_json_sha256"]
    }
    actual = {
        path.name: sha256(path)
        for path in firmware.iterdir()
        if path.is_file()
        and (
            path.name.startswith("modem.")
            or path.name.startswith("modem_dtb.")
            or path.name == "modemr.jsn"
        )
    }
    if actual != expected:
        raise ValueError("Extracted modem firmware differs from its review")
    original = json.loads((out / "manifest.json").read_text())
    km = json.loads((kernel / "manifest.json").read_text())
    if original["source_commit"] != km["integration_commit"]:
        raise ValueError("Different source components")
    for path, key in (
        ("rootfs.squashfs", "rootfs_sha256"),
        ("boot-desktop-ram-experimental.img", "boot_image_sha256"),
    ):
        if sha256(out / path) != original[key]:
            raise ValueError("Changed complete image")
    sections = boot_sections((out / "boot-desktop-ram-experimental.img").read_bytes())
    if sections["kernel"] != (kernel / "Image.gz").read_bytes():
        raise ValueError("Changed original kernel")
    modem = importlib.import_module("modem-gnss-dtb")
    dtb, dtb_report = modem.derive(
        (REPO / "devices/oneplus13/qualified-data/calibrated.dtb").read_bytes()
    )
    nonce = secrets.token_hex(12)
    cmdline = original["cmdline"].replace(
        "oneplus13.session=" + original["session_identity_nonce"],
        "oneplus13.session=" + nonce,
    )
    for flag in (
        "oneplus13.audio_probe=1",
        "oneplus13.camera_probe=1",
        "oneplus13.video_probe=1",
        "oneplus13.modem_probe=1",
    ):
        if flag not in cmdline.split():
            cmdline += " " + flag
    if len(cmdline.encode()) > 1536:
        raise ValueError("Modem probe bootargs exceed header")
    tree = Fdt(dtb)
    tree.nodes["/chosen"]["bootargs"] = (cmdline + "\0").encode()
    dtb = tree.to_blob()
    names = (
        "boot-modem-gnss-ram-experimental.img",
        "sm8750-oneplus13-modem-gnss-probe.dtb",
        "modem-gnss-probe-manifest.json",
        "modem-gnss-dtb-review.json",
        "modem-firmware.tar.gz",
        "modem-firmware-review.json",
    )
    paths = {name: out / name for name in names}
    if any(path.exists() for path in paths.values()) and not args.replace:
        raise ValueError("Preserve prior modem image candidates")
    dtb_path = paths["sm8750-oneplus13-modem-gnss-probe.dtb"]
    dtb_path.write_bytes(dtb)
    firmware_archive = paths["modem-firmware.tar.gz"]
    deterministic_tar(firmware, firmware_archive)
    image = paths["boot-modem-gnss-ram-experimental.img"]
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
        raise ValueError("Independent modem boot readback failed")
    paths["modem-gnss-dtb-review.json"].write_text(
        json.dumps(dtb_report, indent=2) + "\n"
    )
    paths["modem-firmware-review.json"].write_text(
        json.dumps(review, indent=2) + "\n"
    )
    result = {
        **original,
        "scope": "SEPARATE_MANUAL_MPSS_GNSS_RAM_BOOT_NEVER_FLASH",
        "cmdline": cmdline,
        "session_identity_nonce": nonce,
        "boot_image_sha256": sha256(image),
        "boot_image_bytes": image.stat().st_size,
        "boot_image_file": image.name,
        "dtb_file": dtb_path.name,
        "dtb_sha256": sha256(dtb_path),
        "default_boot_image_sha256": original["boot_image_sha256"],
        "firmware_archive_sha256": sha256(firmware_archive),
        "firmware_archive_bytes": firmware_archive.stat().st_size,
        "firmware_files": actual,
        "firmware_signature_authenticated": False,
        "remote_processor_started": False,
        "modem_service_qualified": False,
        "gnss_service_qualified": False,
        "hardware_functional_acceptance": False,
    }
    paths["modem-gnss-probe-manifest.json"].write_text(
        json.dumps(result, indent=2) + "\n"
    )
    current = {
        **original,
        "modem_probe_artifacts": {
            name: sha256(path) for name, path in paths.items()
        },
        "modem_probe_auto_started": False,
        "modem_hardware_functional_qualified": False,
        "gnss_hardware_functional_qualified": False,
    }
    (out / "manifest.json").write_text(json.dumps(current, indent=2) + "\n")
    print(
        json.dumps(
            {
                "scope": result["scope"],
                "source_commit": result["source_commit"],
                "boot_image_sha256": result["boot_image_sha256"],
                "firmware_archive_sha256": result["firmware_archive_sha256"],
                "default_boot_image_preserved": True,
            }
        )
    )


if __name__ == "__main__":
    main()
