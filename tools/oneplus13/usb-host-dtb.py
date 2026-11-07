#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Expose the DWC3 role-control debugfs path while defaulting to USB device."""

import copy
import hashlib
import importlib

from stock import Fdt, strings


ir = importlib.import_module("ir-dtb")
USB = "/soc@0/usb@a600000"


def derive(blob):
    base, ir_report = ir.derive(blob)
    tree = Fdt(base)
    before = copy.deepcopy(tree.nodes)
    node = tree.nodes[USB]
    if strings(node.get("dr_mode", b"")) != ["peripheral"]:
        raise ValueError("Unexpected USB dr_mode")
    if "usb-role-switch" not in node:
        raise ValueError("USB role-switch property is absent")
    node["dr_mode"] = ir.modem.video.text("otg")
    node["role-switch-default-mode"] = ir.modem.video.text("device")
    changed = {path for path in before if before[path] != tree.nodes[path]}
    if changed != {USB} or set(before) != set(tree.nodes):
        raise ValueError("Unexpected USB tree mutation")
    final = tree.to_blob()
    check = Fdt(final)
    if check.nodes != tree.nodes or check.reservations != tree.reservations:
        raise ValueError("USB tree readback mismatch")
    return final, {
        "scope": "MANUAL_ONEPLUS13_USB_OTG_ROLE_RAM_TREE",
        "base_sha256": hashlib.sha256(blob).hexdigest(),
        "output_sha256": hashlib.sha256(final).hexdigest(),
        "ir_derivation": ir_report,
        "changed_nodes": sorted(changed),
        "usb_node": USB,
        "dr_mode": "otg",
        "default_role": "device",
        "debugfs_role_control_available": True,
        "host_peripheral_hardware_qualified": False,
        "default_boot_tree_changed": False,
    }
