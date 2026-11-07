#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the camera/GNSS RAM image with manual IR and DWC3 role control."""

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
    parser.add_argument("--automatic-otg", action="store_true")
    args = parser.parse_args()
    kernel = args.kernel.resolve()
    out = args.image.resolve()
    original = json.loads(
        (out / "camera-gnss-manifest.json").read_text(encoding="utf-8")
    )
    current = json.loads(
        (out / "manifest.json").read_text(encoding="utf-8")
    )
    kernel_manifest = json.loads(
        (kernel / "manifest.json").read_text(encoding="utf-8")
    )
    if original["source_commit"] != kernel_manifest["integration_commit"]:
        raise ValueError("Different source components")
    attached = current.get("camera_gnss_artifacts", {})
    if len(attached) != 4:
        raise ValueError("Missing camera/GNSS alternatives")
    for name, digest in attached.items():
        if sha256(out / name) != digest:
            raise ValueError(f"Camera/GNSS artifact changed: {name}")
    runtime = current.get("peripheral_probe_runtime_files", {})
    expected_runtime = {
        "usr/libexec/oneplus13-ram-ir-check.py",
        "usr/libexec/oneplus13-ram-usb-role-check.py",
        "usr/libexec/oneplus13-ram-usb-native-host-check.py",
        "usr/libexec/oneplus13-ram-usb-powered-native-check.py",
        "usr/libexec/oneplus13-ram-usb-supply-check.py",
        "usr/libexec/oneplus13-usb-filesystem-modules.py",
        "usr/libexec/oneplus13-ram-suspend-check.py",
    }
    if set(runtime) != expected_runtime:
        raise ValueError("Missing manual peripheral runtime")
    for name, digest in runtime.items():
        raw = subprocess.check_output(
            ["unsquashfs", "-cat", str(out / "rootfs.squashfs"), name]
        )
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Manual peripheral runtime changed")

    graph = importlib.import_module("camera-gnss-ir-usb-dtb")
    dtb, report = graph.derive(
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
    flags = (("oneplus13.usb_host_probe=1", "qcom_battmgr.oneplus13_otg_auto=1",
              "oneplus13.first_user_audio=1")
             if args.automatic_otg else
             ("oneplus13.ir_probe=1", "oneplus13.usb_host_probe=1",
                 "qcom_battmgr.oneplus13_otg_lease=1",
                 "qcom_battmgr.oneplus13_otg_supply_read=1",
                 "qcom_battmgr.oneplus13_otg_supply_lease=1"))
    for flag in flags:
        if flag not in cmdline.split():
            cmdline += " " + flag
    tree.nodes["/chosen"]["bootargs"] = (cmdline + "\0").encode()
    dtb = tree.to_blob()
    if Fdt(dtb).nodes != tree.nodes:
        raise ValueError("Combined peripheral DTB readback mismatch")

    names = (
        "boot-targets-ir-usb-ram-experimental.img",
        "sm8750-oneplus13-targets-ir-usb.dtb",
        "targets-ir-usb-manifest.json",
        "targets-ir-usb-dtb-review.json",
    )
    paths = {name: out / name for name in names}
    if args.automatic_otg:
        replacements = {
            names[0]: "boot-automatic-usb-ram-experimental.img",
            names[1]: "sm8750-oneplus13-automatic-usb.dtb",
            names[2]: "automatic-usb-manifest.json",
            names[3]: "automatic-usb-dtb-review.json",
        }
        paths = {name: out / replacements[name] for name in names}
    if any(path.exists() for path in paths.values()):
        raise ValueError("Preserve previous target alternatives")
    dtb_path = paths["sm8750-oneplus13-targets-ir-usb.dtb"]
    dtb_path.write_bytes(dtb)

    base_sections = boot_sections(
        (out / original["boot_image_file"]).read_bytes()
    )
    if base_sections["kernel"] != (kernel / "Image.gz").read_bytes():
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
    image = paths["boot-targets-ir-usb-ram-experimental.img"]
    with tempfile.TemporaryDirectory() as temporary:
        with tarfile.open(archive) as source:
            source.extractall(temporary, filter="data")
        mkboot = Path(temporary) / "mkbootimg.py"
        if sha256(mkboot) != lock["sha256"]:
            raise ValueError("Boot packer changed")
        ramdisk = Path(temporary) / "ramdisk.cpio.gz"
        ramdisk.write_bytes(base_sections["ramdisk"])
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
    if boot_sections(image.read_bytes()) != {
        **base_sections,
        "dtb": dtb,
    }:
        raise ValueError("Combined peripheral boot readback failed")

    result = {
        **original,
        "scope": ("AUTOMATIC_OTG_DESKTOP_RAM_CANDIDATE" if args.automatic_otg else
                  "MANUAL_CAMERA_GNSS_IR_USB_ROLE_RAM_BOOT"),
        "cmdline": cmdline,
        "session_identity_nonce": nonce,
        "boot_image_sha256": sha256(image),
        "boot_image_bytes": image.stat().st_size,
        "boot_image_file": image.name,
        "dtb_file": dtb_path.name,
        "dtb_sha256": sha256(dtb_path),
        "ir_probe_auto_started": False,
        "usb_host_probe_auto_started": False,
        "usb_firmware_lease_available": not args.automatic_otg,
        "usb_firmware_lease_auto_started": False,
        "usb_supply_read_available": not args.automatic_otg,
        "usb_supply_read_auto_started": False,
        "usb_supply_setters_available": not args.automatic_otg,
        "usb_supply_setters_auto_started": False,
        "optical_output_qualified": False,
        "usb_host_peripheral_receipt": False,
        "default_boot_image_sha256": original["default_boot_image_sha256"],
    }
    if args.automatic_otg:
        result.update(automatic_usb_service_enabled=True,
                      desktop_audio_first_user_conditional=True,
                      desktop_audio_first_user_gate="oneplus13.first_user_audio=1",
                      desktop_audio_auto_started=True,
                      microphone_auto_started=True,
                      desktop_audio_activation_scope="BOUNDED_RAM_FIRST_USER_NATIVE_PRECHECKS",
                      automatic_usb_hardware_qualified=False,
                      automatic_usb_watchdog_seconds=15,
                      automatic_usb_heartbeat_seconds=3)
        report.update(scope="AUTOMATIC_OTG_DESKTOP_RAM_CANDIDATE",
                      debugfs_role_writes=False, ucsi_owns_data_role=True)
    paths["targets-ir-usb-dtb-review.json"].write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    paths["targets-ir-usb-manifest.json"].write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    if args.automatic_otg:
        current.update(automatic_usb_artifacts={
            path.name: sha256(path) for path in paths.values()},
            automatic_usb_hardware_qualified=False)
    else:
        current.update(
            targets_ir_usb_artifacts={name: sha256(path) for name, path in paths.items()},
            target_ir_usb_auto_started=False,
            target_ir_optical_output_qualified=False,
            target_usb_host_peripheral_qualified=False,
        )
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
