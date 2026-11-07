#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build an isolated real-sensor C-PHY RAM capture candidate; never flash."""

import argparse
import hashlib
import importlib
import json
import re
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
    if (
        original.get("camera_probe_auto_started") is not False
        or original.get("camera_capture_qualified") is not False
    ):
        raise ValueError("Camera image must remain manual and unqualified")

    camera_runtime = original.get("camera_probe_runtime_files", {})
    if set(camera_runtime) != {
        "usr/libexec/oneplus13-ram-camera-chip-id.py"
    }:
        raise ValueError("Missing source-built camera identity helper")
    capture_runtime = original.get("camera_capture_runtime_files", {})
    if (
        set(capture_runtime) != {"usr/libexec/oneplus13-ram-camera-capture.py"}
        or original.get("camera_capture_auto_started") is not False
    ):
        raise ValueError("Missing manually gated camera capture runtime")
    for name, digest in capture_runtime.items():
        raw = subprocess.check_output(
            ["unsquashfs", "-cat", str(out / "rootfs.squashfs"), name]
        )
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Camera capture runtime changed")
    policy = subprocess.check_output(
        [
            "unsquashfs",
            "-cat",
            str(out / "rootfs.squashfs"),
            "etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf",
        ],
        text=True,
    )
    for module in ("s5kjn5", "i2c_qcom_cci"):
        if f"install {module} /bin/false\n" not in policy:
            raise ValueError(f"Manual camera module policy missing: {module}")
    for executable in ("usr/bin/media-ctl", "usr/bin/v4l2-ctl"):
        raw = subprocess.check_output(
            ["unsquashfs", "-cat", str(out / "rootfs.squashfs"), executable]
        )
        if (
            raw[:6] != b"\x7fELF\x02\x01"
            or int.from_bytes(raw[18:20], "little") != 183
        ):
            raise ValueError(f"Native camera userspace tool missing: {executable}")

    required_modules = (
        "/kernel/drivers/media/i2c/s5kjn5.ko",
        "/kernel/drivers/i2c/busses/i2c-qcom-cci.ko",
        "/kernel/drivers/media/platform/qcom/camss/qcom-camss.ko",
        "/kernel/drivers/phy/qualcomm/phy-qcom-mipi-csi2.ko",
        "/kernel/drivers/clk/qcom/camcc-sm8750.ko",
        "/kernel/drivers/clk/qcom/cambistmclkcc-sm8750.ko",
    )
    with tarfile.open(kernel / "modules.tar.gz") as archive:
        names = archive.getnames()
        for suffix in required_modules:
            if sum(name.endswith(suffix) for name in names) != 1:
                raise ValueError(f"Required module missing: {suffix}")

    kernel_manifest = json.loads(
        (kernel / "manifest.json").read_text(encoding="utf-8")
    )
    if original["source_commit"] != kernel_manifest["integration_commit"]:
        raise ValueError("Different source components")
    for path, key in (
        ("rootfs.squashfs", "rootfs_sha256"),
        ("boot-desktop-ram-experimental.img", "boot_image_sha256"),
    ):
        if sha256(out / path) != original[key]:
            raise ValueError("Changed complete image")

    sections = boot_sections(
        (out / "boot-desktop-ram-experimental.img").read_bytes()
    )
    if sections["kernel"] != (kernel / "Image.gz").read_bytes():
        raise ValueError("Changed original kernel")

    capture = importlib.import_module("camera-capture-dtb")
    dtb, report = capture.derive(
        (
            REPO / "devices/oneplus13/qualified-data/calibrated.dtb"
        ).read_bytes()
    )
    nonce = secrets.token_hex(12)
    cmdline = original["cmdline"].replace(
        "oneplus13.session=" + original["session_identity_nonce"],
        "oneplus13.session=" + nonce,
    )
    cmdline += " oneplus13.camera_capture=1"
    if len(cmdline.encode()) > 1536:
        raise ValueError("Capture bootargs exceed header")
    tree = Fdt(dtb)
    tree.nodes["/chosen"]["bootargs"] = (cmdline + "\0").encode()
    dtb = tree.to_blob()

    paths = {
        name: out / name
        for name in (
            "boot-camera-capture-ram-experimental.img",
            "sm8750-oneplus13-camera-capture.dtb",
            "camera-capture-manifest.json",
            "camera-capture-dtb-review.json",
        )
    }
    if any(path.exists() for path in paths.values()):
        raise ValueError("Preserve previous camera capture candidate")

    dpath = paths["sm8750-oneplus13-camera-capture.dtb"]
    dpath.write_bytes(dtb)
    image = paths["boot-camera-capture-ram-experimental.img"]
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
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive) as package:
            package.extractall(tmp, filter="data")
        mkbootimg = Path(tmp) / "mkbootimg.py"
        if sha256(mkbootimg) != lock["sha256"]:
            raise ValueError("Boot packer changed")
        ramdisk = Path(tmp) / "ramdisk.cpio.gz"
        ramdisk.write_bytes(sections["ramdisk"])
        subprocess.run(
            [
                sys.executable,
                str(mkbootimg),
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
                str(dpath),
                "--cmdline",
                cmdline,
                "--output",
                str(image),
            ],
            check=True,
        )
    if boot_sections(image.read_bytes()) != {**sections, "dtb": dtb}:
        raise ValueError("Camera capture boot readback failed")

    result = {
        **original,
        "scope": "SEPARATE_MANUAL_CAMERA_CPHY_CAPTURE_RAM_BOOT_NEVER_FLASH",
        "cmdline": cmdline,
        "session_identity_nonce": nonce,
        "boot_image_sha256": sha256(image),
        "boot_image_bytes": image.stat().st_size,
        "boot_image_file": image.name,
        "dtb_file": dpath.name,
        "dtb_sha256": sha256(dpath),
        "camera_capture_pipeline_present": True,
        "camera_capture_qualified": False,
        "camera_capture_auto_started": False,
        "camera_capture_runtime_external": True,
        "default_boot_image_sha256": original["boot_image_sha256"],
        "default_desktop_preserved": True,
    }
    paths["camera-capture-dtb-review.json"].write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    paths["camera-capture-manifest.json"].write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    current = {
        **original,
        "camera_capture_artifacts": {
            name: sha256(path) for name, path in paths.items()
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(current, indent=2) + "\n", encoding="utf-8"
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
