#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Combine the isolated camera capture and MPSS/GNSS RAM graphs."""

import copy
import hashlib
import importlib

from stock import Fdt


capture = importlib.import_module("camera-capture-dtb")
modem = importlib.import_module("modem-gnss-dtb")


def derive(blob):
    capture_blob, capture_report = capture.derive(blob)
    tree = Fdt(capture_blob)
    before = copy.deepcopy(tree.nodes)
    regions = modem.validate_stock_mpss(tree)
    tree.nodes[modem.MPSS]["status"] = capture.text("okay")
    tree.nodes[modem.MPSS]["firmware-name"] = capture.text(*modem.FIRMWARE)
    modem.add_rmtfs_sharedmem(tree)
    changed = {
        path for path in before if before[path] != tree.nodes[path]
    }
    if changed != {modem.MPSS} or set(before) | {modem.RMTFS} != set(tree.nodes):
        raise ValueError("Unexpected combined camera/MPSS graph mutation")
    final = tree.to_blob()
    check = Fdt(final)
    if check.nodes != tree.nodes or check.reservations != tree.reservations:
        raise ValueError("Combined camera/MPSS tree readback mismatch")
    return final, {
        "scope": "ISOLATED_CAMERA_CAPTURE_AND_MPSS_GNSS_GRAPH",
        "base_sha256": hashlib.sha256(blob).hexdigest(),
        "output_sha256": hashlib.sha256(final).hexdigest(),
        "camera": capture_report,
        "mpss": {
            "node": modem.MPSS,
            "firmware_names": list(modem.FIRMWARE),
            "memory_regions": regions,
            "rmtfs_sharedmem_node": modem.RMTFS,
            "auto_boot_enabled": False,
            "manual_start_required": True,
        },
        "capture_pipeline_present": True,
        "camera_capture_qualified": False,
        "gnss_fix_qualified": False,
        "default_boot_tree_changed": False,
    }
