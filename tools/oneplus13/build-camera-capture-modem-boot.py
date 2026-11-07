#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build one RAM boot combining real camera capture, MPSS and GNSS probes."""

import argparse
import hashlib
import importlib
import json
import secrets
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

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
    original = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    kernel_manifest = json.loads(
        (kernel / "manifest.json").read_text(encoding="utf-8")
    )
    if original["source_commit"] != kernel_manifest["integration_commit"]:
        raise ValueError("Different source components")
    capture_files = original.get("camera_capture_artifacts", {})
    modem_files = original.get("modem_probe_artifacts", {})
    if len(capture_files) != 4 or len(modem_files) != 6:
        raise ValueError("Missing capture or modem alternatives")
    for name, digest in {**capture_files, **modem_files}.items():
        if sha256(out / name) != digest:
            raise ValueError(f"Alternative artifact changed: {name}")

    capture_runtime = original.get("camera_capture_runtime_files", {})
    if set(capture_runtime) != {
        "usr/libexec/oneplus13-ram-camera-capture.py"
    }:
        raise ValueError("Missing camera capture runtime")
    for name, digest in capture_runtime.items():
        raw = subprocess.check_output(
            ["unsquashfs", "-cat", str(out / "rootfs.squashfs"), name]
        )
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Camera capture runtime changed")
    for name in ("usr/bin/qmicli", "usr/bin/media-ctl", "usr/bin/v4l2-ctl"):
        raw = subprocess.check_output(
            ["unsquashfs", "-cat", str(out / "rootfs.squashfs"), name]
        )
        if (
            raw[:6] != b"\x7fELF\x02\x01"
            or int.from_bytes(raw[18:20], "little") != 183
        ):
            raise ValueError(f"Required native tool missing: {name}")

    combined = importlib.import_module("camera-capture-modem-dtb")
    dtb, combined_report = combined.derive(
        (
            REPO / "devices/oneplus13/qualified-data/calibrated.dtb"
        ).read_bytes()
    )
    tree = Fdt(dtb)
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
        "oneplus13.camera_capture=1",
    ):
        if flag not in cmdline.split():
            cmdline += " " + flag
    if len(cmdline.encode()) > 1536:
        raise ValueError("Combined bootargs exceed header")
    tree.nodes["/chosen"]["bootargs"] = (cmdline + "\0").encode()
    dtb = tree.to_blob()
    if Fdt(dtb).nodes != tree.nodes:
        raise ValueError("Combined DTB readback mismatch")

    paths = {
        name: out / name
        for name in (
            "boot-camera-gnss-ram-experimental.img",
            "sm8750-oneplus13-camera-gnss.dtb",
            "camera-gnss-manifest.json",
            "camera-gnss-dtb-review.json",
        )
    }
    if any(path.exists() for path in paths.values()):
        raise ValueError("Preserve previous combined alternatives")
    dtb_path = paths["sm8750-oneplus13-camera-gnss.dtb"]
    dtb_path.write_bytes(dtb)

    sections = boot_sections(
        (out / "boot-desktop-ram-experimental.img").read_bytes()
    )
    if sections["kernel"] != (kernel / "Image.gz").read_bytes():
        raise ValueError("Changed source kernel")
    lock = json.loads(
        (REPO / "kernel/source.json").read_text(encoding="utf-8")
    )["tool_inputs"]["mkbootimg.py"]
    archive = REPO / lock["bundled_archive"]
    if sha256(archive) != lock["archive_sha256"]:
        raise ValueError("Boot packer archive changed")
    geometry = json.loads(
        (
            REPO / "devices/oneplus13/qualified-data/boot-geometry.json"
        ).read_text(encoding="utf-8")
    )
    image = paths["boot-camera-gnss-ram-experimental.img"]
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
        raise ValueError("Combined boot readback failed")

    result = {
        **original,
        "scope": "SEPARATE_MANUAL_CAMERA_CAPTURE_AND_MPSS_GNSS_RAM_BOOT",
        "cmdline": cmdline,
        "session_identity_nonce": nonce,
        "boot_image_sha256": sha256(image),
        "boot_image_file": image.name,
        "dtb_file": dtb_path.name,
        "dtb_sha256": sha256(dtb_path),
        "camera_capture_pipeline_present": True,
        "camera_capture_qualified": False,
        "modem_hardware_functional_qualified": False,
        "gnss_hardware_functional_qualified": False,
        "default_boot_image_sha256": original["boot_image_sha256"],
        "default_desktop_preserved": True,
    }
    paths["camera-gnss-dtb-review.json"].write_text(
        json.dumps(combined_report, indent=2) + "\n", encoding="utf-8"
    )
    paths["camera-gnss-manifest.json"].write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    (out / "manifest.json").write_text(
        json.dumps(
            {
                **original,
                "camera_gnss_artifacts": {
                    name: sha256(path) for name, path in paths.items()
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "scope": result["scope"],
                "source_commit": result["source_commit"],
                "boot_image_sha256": result["boot_image_sha256"],
                "default_desktop_preserved": True,
            }
        )
    )


if __name__ == "__main__":
    main()
