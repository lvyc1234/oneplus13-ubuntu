#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Inventory the stock infrared controller, supply and pin resources."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from stock import Fdt, cells, strings


def render(value):
    if not value:
        return True
    if value.endswith(b"\0"):
        try:
            return strings(value)
        except ValueError:
            pass
    if len(value) % 4 == 0 and len(value) <= 128:
        return cells(value)
    return {"bytes": len(value), "hex": value[:256].hex()}


def matching(tree, pattern):
    rows = []
    compiled = re.compile(pattern, re.IGNORECASE)
    for path, properties in sorted(tree.nodes.items()):
        values = (
            strings(properties["compatible"])
            if properties.get("compatible", b"").endswith(b"\0")
            else []
        )
        if compiled.search(path + " " + " ".join(values)):
            rows.append(
                {
                    "path": path,
                    "compatible": values,
                    "properties": {
                        name: render(value)
                        for name, value in sorted(properties.items())
                        if name not in ("phandle", "linux,phandle")
                    },
                }
            )
    return rows


def review(blob):
    tree = Fdt(blob)
    supply_paths = [
        path
        for path, properties in tree.nodes.items()
        if properties.get("regulator-name", b"").rstrip(b"\0") == b"pm_humu_l9"
    ]
    consumers = []
    if len(supply_paths) == 1:
        supply = supply_paths[0]
        phandle = tree.nodes[supply].get("phandle")
        if phandle:
            for path, properties in tree.nodes.items():
                if path == supply:
                    continue
                for name, value in properties.items():
                    if value and len(value) % 4 == 0 and phandle in [
                        value[index : index + 4]
                        for index in range(0, len(value), 4)
                    ]:
                        consumers.append({"path": path, "property": name})
    return {
        "scope": "HOST_STOCK_INFRARED_RESOURCE_REVIEW",
        "dtb_sha256": hashlib.sha256(blob).hexdigest(),
        "phone_operations": 0,
        "ir_controller": matching(tree, r"oplus,kookong_ir_spi|consumer_ir"),
        "spi_controller": matching(tree, r"spi@a94000|a94000"),
        "supply": matching(tree, r"ldob9|l9b|consumer_ir_core"),
        "pins": matching(tree, r"ir_.*spi|kookong|ir_active|ir_suspend"),
        "l9_supply_path": supply_paths[0] if len(supply_paths) == 1 else None,
        "l9_consumers": consumers,
        "transmission_qualified": False,
        "optical_output_qualified": False,
        "shared_supply_safety_verified": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.stock.read_bytes())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "controller_nodes": len(result["ir_controller"]),
                "spi_nodes": len(result["spi_controller"]),
                "supply_nodes": len(result["supply"]),
                "pin_nodes": len(result["pins"]),
            }
        )
    )


if __name__ == "__main__":
    main()
