#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Enable the stock SM8750 MPSS remoteproc in a separate RAM test tree."""

import copy
import hashlib
import importlib
from pathlib import Path
import struct

from stock import Fdt, cells, strings


video = importlib.import_module("video-probe-dtb")
MPSS = "/soc@0/remoteproc@4080000"
RMTFS = "/soc@0/qcom,rmtfs_sharedmem@0"
RMTFS_SHAREDMEM_SIZE = 0x400000
FIRMWARE = (
    "qcom/sm8750/oneplus/dodge/modem.mdt",
    "qcom/sm8750/oneplus/dodge/modem_dtb.mdt",
)


def referenced_paths(tree, value):
    if not value or len(value) % 4:
        raise ValueError("Malformed MPSS memory-region")
    result = []
    for ref in struct.unpack(">" + "I" * (len(value) // 4), value):
        rows = [
            path
            for path, node in tree.nodes.items()
            if node.get("phandle") == struct.pack(">I", ref)
        ]
        if len(rows) != 1:
            raise ValueError("Ambiguous MPSS memory-region")
        result.append(rows[0])
    return result


def validate_stock_mpss(tree):
    node = tree.nodes[MPSS]
    if strings(node["compatible"]) != ["qcom,sm8750-mpss-pas"]:
        raise ValueError("Wrong MPSS compatible")
    if strings(node.get("status", b"okay\0")) != ["disabled"]:
        raise ValueError("MPSS candidate must begin disabled")
    if cells(node["reg"]) != [0, 0x4080000, 0, 0x10000]:
        raise ValueError("Wrong MPSS register window")
    if strings(node["clock-names"]) != ["xo"]:
        raise ValueError("Wrong MPSS clock contract")
    if strings(node["power-domain-names"]) != ["cx", "mss"]:
        raise ValueError("Wrong MPSS power-domain contract")
    if strings(node["interrupt-names"]) != [
        "wdog",
        "fatal",
        "ready",
        "handover",
        "stop-ack",
        "shutdown-ack",
    ]:
        raise ValueError("Wrong MPSS interrupt contract")
    regions = referenced_paths(tree, node["memory-region"])
    if regions[:2] != [
        "/reserved-memory/mpss@8ba00000",
        "/reserved-memory/q6-mpss-dtb@9b000000",
    ]:
        raise ValueError("Wrong MPSS firmware carveouts")
    if len(regions) != 4 or any(
        not path.startswith("/reserved-memory/dsm-partition-") for path in regions[2:]
    ):
        raise ValueError("Wrong MPSS DSM carveouts")
    if "qcom,qmp" not in node or "qcom,smem-states" not in node:
        raise ValueError("Missing MPSS QMP or SMEM contract")
    if "firmware-name" in node:
        raise ValueError("Unexpected stock firmware-name")
    return regions


def add_rmtfs_sharedmem(tree):
    if RMTFS in tree.nodes:
        raise ValueError("RMTFS shared-memory node already exists")
    tree.nodes[RMTFS] = {
        "compatible": video.text("qcom,sharedmem-uio"),
        # The stock DTSI uses a one-cell address/size parent. The mainline
        # /soc@0 parent uses two cells for each field, so preserve the stock
        # semantics (address zero, 4 MiB size) with the local cell layout.
        "reg": struct.pack(">IIII", 0, 0, 0, RMTFS_SHAREDMEM_SIZE),
        "reg-names": video.text("rmtfs"),
        "qcom,client-id": struct.pack(">I", 1),
    }


def derive(blob):
    video_blob, video_report = video.derive(blob)
    tree = Fdt(video_blob)
    before = copy.deepcopy(tree.nodes)
    regions = validate_stock_mpss(tree)
    tree.nodes[MPSS]["status"] = video.text("okay")
    tree.nodes[MPSS]["firmware-name"] = video.text(*FIRMWARE)
    if RMTFS in tree.nodes:
        raise ValueError("RMTFS shared-memory node already exists")
    tree.nodes[RMTFS] = {
        "compatible": video.text("qcom,sharedmem-uio"),
        "reg": struct.pack(">IIII", 0, 0, 0, RMTFS_SHAREDMEM_SIZE),
        "reg-names": video.text("rmtfs"),
        "qcom,client-id": struct.pack(">I", 1),
    }
    changed = {path for path in before if before[path] != tree.nodes[path]}
    if changed != {MPSS} or set(before) | {RMTFS} != set(tree.nodes):
        raise ValueError("Unexpected MPSS/RMTFS tree mutation")
    report_nodes = sorted({MPSS, RMTFS})
    final = tree.to_blob()
    check = Fdt(final)
    if check.nodes != tree.nodes or check.reservations != tree.reservations:
        raise ValueError("MPSS tree readback mismatch")
    return final, {
        "scope": "SEPARATE_MANUAL_SM8750_MPSS_GNSS_RAM_TREE",
        "base_sha256": hashlib.sha256(blob).hexdigest(),
        "output_sha256": hashlib.sha256(final).hexdigest(),
        "video_derivation": video_report,
        "changed_nodes": report_nodes,
        "mpss_node": MPSS,
        "firmware_names": list(FIRMWARE),
        "memory_regions": regions,
        "rmtfs_sharedmem_node": RMTFS,
        "auto_boot_enabled": False,
        "manual_remoteproc_start_required": True,
        "firmware_authentication_qualified": False,
        "modem_service_qualified": False,
        "gnss_service_qualified": False,
        "default_boot_tree_changed": False,
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    blob, report = derive(args.stock.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(blob)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
