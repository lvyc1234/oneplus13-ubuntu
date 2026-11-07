#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Review stock modem/GNSS resources without claiming firmware or RF support."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct

from stock import Fdt, strings


spec = importlib.util.spec_from_file_location(
    "audio_topology", Path(__file__).with_name("review-audio-topology.py")
)
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)

PATTERN = re.compile(
    r"qcom,.*(?:mpss|modem|gnss|remoteproc|glink|qrtr|qmi|rproc)|"
    r"qcom,smem|qcom,smd|qcom,rpmh|qcom,qseecom",
    re.IGNORECASE,
)
MAX_PROPERTY_BYTES = 4096


def compatible(properties):
    value = properties.get("compatible")
    if not value or not value.endswith(b"\0"):
        return []
    return strings(value)


def property_value(value):
    if len(value) > MAX_PROPERTY_BYTES:
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if value.endswith(b"\0"):
        try:
            rendered = strings(value)
        except ValueError:
            rendered = None
        if rendered is not None:
            return rendered
    if len(value) % 4 == 0 and len(value) <= 64:
        return list(struct.unpack(">" + "I" * (len(value) // 4), value))
    return {"bytes": len(value), "hex": value[:256].hex()}


def referenced_nodes(tree, properties):
    value = properties.get("memory-region")
    if not value or len(value) % 4:
        return []
    result = []
    for ref in struct.unpack(">" + "I" * (len(value) // 4), value):
        paths = [
            path
            for path, node in tree.nodes.items()
            if node.get("phandle") == struct.pack(">I", ref)
        ]
        result.append(paths[0] if len(paths) == 1 else None)
    return result


def review(blob):
    tree = Fdt(blob)
    rows = []
    for path, properties in sorted(tree.nodes.items()):
        values = compatible(properties)
        haystack = " ".join(values) + " " + path
        if not PATTERN.search(haystack):
            continue
        row = {
            "path": path,
            "compatible": values,
            "enabled": audio.enabled(tree.nodes, path),
            "properties": {},
            "memory_regions": referenced_nodes(tree, properties),
        }
        for name, value in sorted(properties.items()):
            if name in ("phandle", "linux,phandle"):
                continue
            if name == "compatible":
                continue
            if name in {
                "reg",
                "interrupts",
                "interrupts-extended",
                "memory-region",
                "firmware-name",
                "clocks",
                "clock-names",
                "resets",
                "reset-names",
                "power-domains",
                "iommus",
                "mboxes",
                "mbox-names",
                "status",
                "qcom,smem-states",
                "qcom,smem-state-names",
            }:
                row["properties"][name] = property_value(value)
        rows.append(row)
    reserved = []
    for path, properties in sorted(tree.nodes.items()):
        if not path.startswith("/reserved-memory/"):
            continue
        if not re.search(
            r"modem|mpss|adsp|cdsp|q6|smem|glink|qsee|hyp|tz|pdp|gps",
            path,
            re.IGNORECASE,
        ):
            continue
        reserved.append(
            {
                "path": path,
                "reg": property_value(properties.get("reg", b"")),
                "no_map": "no-map" in properties,
                "reusable": "reusable" in properties,
            }
        )
    return {
        "scope": "HOST_ONLY_PHONE_MODEM_GNSS_RESOURCE_REVIEW",
        "phone_operations": 0,
        "dtb_sha256": hashlib.sha256(blob).hexdigest(),
        "nodes": rows,
        "reserved_memory": reserved,
        "firmware_identity_verified": False,
        "firmware_signature_authenticated": False,
        "rf_or_gnss_service_verified": False,
        "hardware_support_inferred": False,
        "limitations": [
            "Enabled DT nodes do not establish firmware compatibility.",
            "A remote processor can be present while its board-specific ABI, memory protection or RF calibration is absent.",
            "GNSS functionality requires modem services and a userspace transport in addition to the resource graph.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.stock.read_bytes())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "scope",
        "phone_operations",
        "firmware_identity_verified",
        "rf_or_gnss_service_verified",
        "hardware_support_inferred",
    )}, indent=2))


if __name__ == "__main__":
    main()
