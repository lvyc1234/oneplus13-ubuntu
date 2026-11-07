#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Review C.93 modem PIL splits against the saved SM8750 MPSS carveouts."""

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import struct

from stock import Fdt, strings


pil = importlib.import_module("review-pil-firmware")


def sha256(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def memory_regions(tree, properties):
    value = properties.get("memory-region")
    if not value or len(value) % 4:
        raise ValueError("MPSS memory-region is absent or malformed")
    result = []
    for ref in struct.unpack(">" + "I" * (len(value) // 4), value):
        rows = [
            (path, item)
            for path, item in tree.nodes.items()
            if item.get("phandle") == struct.pack(">I", ref)
        ]
        if len(rows) != 1:
            raise ValueError("Ambiguous MPSS memory-region phandle")
        path, item = rows[0]
        if not path.startswith("/reserved-memory/") or len(item.get("reg", b"")) != 16:
            raise ValueError("MPSS memory-region is not a fixed carveout")
        base, size = struct.unpack(">QQ", item["reg"])
        result.append(
            {
                "path": path,
                "base": base,
                "size": size,
                "no_map": "no-map" in item,
                "sha256": hashlib.sha256(item["reg"]).hexdigest(),
            }
        )
    return result


def review(firmware, stock):
    tree = Fdt(stock)
    node = tree.nodes["/soc@0/remoteproc@4080000"]
    if "qcom,sm8750-mpss-pas" not in strings(node["compatible"]):
        raise ValueError("Wrong MPSS remoteproc compatible")
    regions = memory_regions(tree, node)
    if len(regions) < 2 or not all(item["no_map"] for item in regions[:2]):
        raise ValueError("MPSS firmware carveouts are not fixed no-map regions")
    expected = {
        "modem",
        "modem_dtb",
        *{
            path.name
            for path in firmware.iterdir()
            if path.name.startswith("modem.")
            or path.name.startswith("modem_dtb.")
        },
    }
    actual = {
        path.name
        for path in firmware.iterdir()
        if path.is_file()
        and (
            path.name.startswith("modem.")
            or path.name.startswith("modem_dtb.")
            or path.name == "modemr.jsn"
        )
    }
    if "modemr.jsn" not in actual:
        raise ValueError("Missing modem resource JSON")
    roles = {}
    for stem, region in zip(("modem", "modem_dtb"), regions[:2]):
        roles[stem] = pil.inspect_split(
            firmware, stem, region["base"], region["size"]
        )
    firmware_files = {
        name: digest
        for role in roles.values()
        for name, digest in role.pop("files").items()
    }
    return {
        "scope": "HOST_C93_MODEM_PIL_STRUCTURE_AND_CARVEOUT_REVIEW",
        "phone_operations": 0,
        "firmware_directory_files": len(actual),
        "firmware_split_and_json_files": len(actual | expected),
        "firmware_sha256": firmware_files,
        "resource_json_sha256": sha256(firmware / "modemr.jsn"),
        "dtb_sha256": hashlib.sha256(stock).hexdigest(),
        "mpss_compatible": strings(node["compatible"]),
        "stock_status": strings(node.get("status", b"okay\0")),
        "memory_regions": regions,
        "roles": roles,
        "signature_authenticated": False,
        "remote_processor_started": False,
        "gnss_service_verified": False,
        "rf_calibration_verified": False,
        "hardware_functional_acceptance": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--firmware", required=True, type=Path)
    parser.add_argument("--stock", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = review(args.firmware.resolve(), args.stock.read_bytes())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "scope": result["scope"],
                "firmware_directory_files": result["firmware_directory_files"],
                "signature_authenticated": result["signature_authenticated"],
                "remote_processor_started": result["remote_processor_started"],
                "gnss_service_verified": result["gnss_service_verified"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
