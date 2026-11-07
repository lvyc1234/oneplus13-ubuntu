#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Stage hash-reviewed SM8750 CAMSS, S5KJN5 and phone C-PHY sources."""

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_name(root, name):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe source path: {name}")
    return root / path


def apply_patch(kernel, path):
    forward = ["git", "-C", str(kernel), "apply", "--check", str(path)]
    applied = subprocess.run(
        ["git", "-C", str(kernel), "apply", "--reverse", "--check", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if applied.returncode == 0:
        return False
    subprocess.run(forward, check=True, timeout=20)
    subprocess.run(
        ["git", "-C", str(kernel), "apply", str(path)],
        check=True,
        timeout=30,
    )
    return True


def verify_inventory(base, inventory):
    for name, digest in inventory.items():
        path = checked_name(base, name)
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Reviewed source mismatch: {name}")


def integrate(kernel, catalog_output):
    lock = json.loads((HERE / "sources.json").read_text(encoding="utf-8"))
    pin = subprocess.check_output(
        ["git", "-C", str(kernel), "rev-parse", "HEAD"], text=True, timeout=8
    ).strip()
    if pin != lock["kernel_pin"]:
        raise ValueError("Wrong kernel pin for camera integration")

    patch_entries = (
        lock["patches"]
        + lock["local_fixes"]
        + lock["resource_rebases"]
        + lock["sensor_patches"]
        + lock["sensor_compat_fixes"]
    )
    if len(lock["patches"]) != 23 or len(patch_entries) != 30:
        raise ValueError("Unexpected reviewed camera patch chain")
    applied = []
    for item in patch_entries:
        path = checked_name(HERE, item["file"])
        if sha256(path) != item["sha256"]:
            raise ValueError(f"Camera patch hash mismatch: {item['file']}")
        if apply_patch(kernel, path):
            applied.append(item["file"])

    generated = lock["sensor_generated_files"]
    verify_inventory(kernel, generated)
    sensor_config = (kernel / "drivers/media/i2c/Kconfig").read_text(
        encoding="utf-8"
    )
    sensor_make = (kernel / "drivers/media/i2c/Makefile").read_text(
        encoding="utf-8"
    )
    if sensor_config.count("config VIDEO_S5KJN5\n") != 1:
        raise ValueError("S5KJN5 Kconfig entry is missing or duplicated")
    if (
        sensor_make.count("obj-$(CONFIG_VIDEO_S5KJN5) += s5kjn5.o")
        != 1
    ):
        raise ValueError("S5KJN5 Makefile entry is missing or duplicated")

    cphy = HERE / "cphy"
    cphy_lock = json.loads(
        (cphy / "sources.json").read_text(encoding="utf-8")
    )
    if (
        cphy_lock["scope"]
        != "ISOLATED_CPHY_FRAMEWORK_ONLY_NO_PHONE_TABLE_OR_ACTIVATION"
        or cphy_lock["phone_register_table_present"]
        or cphy_lock["hardware_qualified"]
    ):
        raise ValueError("Unreviewed C-PHY framework scope")
    verify_inventory(kernel, cphy_lock["base_files"])
    verify_inventory(
        cphy,
        {
            item["file"]: item["sha256"]
            for item in cphy_lock["upstream_messages"]
        },
    )
    rebased = cphy / "rebased.patch"
    if sha256(rebased) != cphy_lock["rebase_patch_sha256"]:
        raise ValueError("C-PHY rebased patch changed")
    if apply_patch(kernel, rebased):
        applied.append("cphy/rebased.patch")
    verify_inventory(kernel, cphy_lock["generated_files"])

    phone = json.loads(
        (cphy / "phone-adapter-source.json").read_text(encoding="utf-8")
    )
    if phone["scope"] != "EXPERIMENTAL_PHONE_CPHY_SOURCE_ONLY_NO_BOARD_ENABLE":
        raise ValueError("Unreviewed phone C-PHY adapter scope")
    verify_inventory(kernel, phone["base_files"])
    verify_inventory(cphy, phone["input_files"])
    adapter_patch = cphy / "phone-adapter.patch"
    if apply_patch(kernel, adapter_patch):
        applied.append("cphy/phone-adapter.patch")
    verify_inventory(kernel, phone["generated_files"])

    subprocess.run(
        [
            "python3",
            str(cphy / "review-phone-tables.py"),
            "--source",
            str(cphy / "vendor/cam_csiphy_2_3_0_hwreg_dodge_ultrawide.h"),
            "--output",
            str(catalog_output),
            "--emit-only",
        ],
        check=True,
        timeout=60,
    )
    for name in (
        "catalog.h",
        "phone-sequence.h",
        "source-generation.json",
    ):
        if not (catalog_output / name).is_file():
            raise ValueError(f"Generated C-PHY file missing: {name}")

    phy = kernel / "drivers/phy/qualcomm"
    generated_runtime = {
        "drivers/phy/qualcomm/dodge-cphy-mmio.h": cphy
        / "dodge-cphy-mmio.h",
        "drivers/phy/qualcomm/dodge-cphy-catalog.h": catalog_output
        / "catalog.h",
        "drivers/phy/qualcomm/phone-sequence.h": cphy
        / "phone-sequence.h",
    }
    for relative, source in generated_runtime.items():
        destination = kernel / relative
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise ValueError(f"Refuse changed camera runtime: {relative}")
        destination.write_bytes(source.read_bytes())
    if not (phy / "dodge-cphy-mmio.h").is_file():
        raise ValueError("Phone C-PHY runtime headers were not installed")

    intent = sorted(
        set(
            [
                *generated,
                *cphy_lock["generated_files"],
                *phone["generated_files"],
                *generated_runtime,
            ]
        )
    )
    subprocess.run(
        ["git", "-C", str(kernel), "add", "--intent-to-add", *intent],
        check=True,
        timeout=15,
    )
    return {
        "scope": "SOURCE_REVIEWED_CAMSS_S5KJN5_AND_PHONE_CPHY",
        "kernel_pin": pin,
        "patches_applied_this_run": applied,
        "standard_public_patches": lock["patches"],
        "local_fixes": lock["local_fixes"],
        "resource_rebases": lock["resource_rebases"],
        "sensor_patches": lock["sensor_patches"],
        "sensor_compat_fixes": lock["sensor_compat_fixes"],
        "sensor_source": generated,
        "cphy_framework": cphy_lock,
        "phone_adapter": phone,
        "catalog": {
            name: sha256(catalog_output / name)
            for name in (
                "catalog.h",
                "phone-sequence.h",
                "source-generation.json",
            )
        },
        "runtime_headers": {
            name: sha256(path) for name, path in generated_runtime.items()
        },
        "phone_node_enabled": False,
        "capture_pipeline_present": False,
        "camera_capture_qualified": False,
        "private_register_data_included": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--catalog-output", required=True, type=Path)
    args = parser.parse_args()
    report = integrate(
        args.kernel.resolve(), args.catalog_output.resolve()
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print("CAMERA_SOURCE_INTEGRATION_PASS")


if __name__ == "__main__":
    main()
