#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Add reviewed IR and DWC3 role control to the camera/GNSS RAM tree."""

import copy
import hashlib
import importlib

from stock import Fdt, cells, strings


combined = importlib.import_module("camera-capture-modem-dtb")
ir = importlib.import_module("ir-dtb")
usb = importlib.import_module("usb-host-dtb")
text = ir.text
u32 = ir.u32


def derive(blob):
    combined_blob, combined_report = combined.derive(blob)
    tree = Fdt(combined_blob)
    before = copy.deepcopy(tree.nodes)
    ir.validate(tree)
    fresh = max(tree.phandles) + 1
    for path in (ir.L9B, ir.TLMM):
        if "phandle" not in tree.nodes[path]:
            tree.nodes[path]["phandle"] = u32(fresh)
            fresh += 1
    l9_phandle = cells(tree.nodes[ir.L9B]["phandle"])[0]
    tree.nodes[ir.L9B]["regulator-max-microvolt"] = u32(ir.IR_UV)
    active = ir.TLMM + "/dodge-ir-mosi-active-state"
    sleep = ir.TLMM + "/dodge-ir-mosi-sleep-state"
    if active in tree.nodes or sleep in tree.nodes or ir.IR in tree.nodes:
        raise ValueError("IR node already exists")
    active_phandle = fresh
    sleep_phandle = fresh + 1
    tree.nodes[active] = {
        "pins": text("gpio53"),
        "function": text("qup1_se5"),
        "drive-strength": u32(6),
        "bias-disable": b"",
        "phandle": u32(active_phandle),
    }
    tree.nodes[sleep] = {
        "pins": text("gpio53"),
        "function": text("gpio"),
        "drive-strength": u32(2),
        "bias-disable": b"",
        "output-low": b"",
        "phandle": u32(sleep_phandle),
    }
    tree.nodes[ir.SPI]["status"] = text("okay")
    tree.nodes[ir.SPI]["pinctrl-names"] = text("default", "sleep")
    tree.nodes[ir.SPI]["pinctrl-0"] = u32(active_phandle)
    tree.nodes[ir.SPI]["pinctrl-1"] = u32(sleep_phandle)
    tree.nodes[ir.I2C]["status"] = text("disabled")
    tree.nodes[ir.IR] = {
        "compatible": text("oneplus,dodge-ir-experimental"),
        "reg": u32(0),
        "spi-max-frequency": u32(5000000),
        "irda_regulator-supply": u32(l9_phandle),
        "status": text("okay"),
    }

    node = tree.nodes[usb.USB]
    if strings(node.get("dr_mode", b"")) != ["peripheral"]:
        raise ValueError("Unexpected USB dr_mode")
    if "usb-role-switch" not in node:
        raise ValueError("USB role-switch property is absent")
    node["dr_mode"] = text("otg")
    node["role-switch-default-mode"] = text("device")

    changed = {
        path for path in before if before[path] != tree.nodes[path]
    }
    expected_changed = {ir.L9B, ir.SPI, usb.USB}
    if changed != expected_changed:
        raise ValueError(
            "Unexpected combined peripheral mutation: "
            + ",".join(sorted(changed))
        )
    added = set(tree.nodes) - set(before)
    if added != {active, sleep, ir.IR}:
        raise ValueError("Unexpected combined peripheral node")
    final = tree.to_blob()
    check = Fdt(final)
    if check.nodes != tree.nodes or check.reservations != tree.reservations:
        raise ValueError("Combined peripheral tree readback mismatch")
    return final, {
        "scope": "CAMERA_GNSS_IR_USB_ROLE_RAM_TREE",
        "base_sha256": hashlib.sha256(blob).hexdigest(),
        "output_sha256": hashlib.sha256(final).hexdigest(),
        "combined_derivation": combined_report,
        "changed_nodes": sorted(changed),
        "additional_nodes": sorted(added),
        "spi_controller": ir.SPI,
        "ir_node": ir.IR,
        "supply_node": ir.L9B,
        "supply_microvolt": ir.IR_UV,
        "ir_gpio": 53,
        "usb_node": usb.USB,
        "usb_default_role": "device",
        "usb_host_role_control": True,
        "camera_capture_qualified": False,
        "nfc_tag_qualified": False,
        "gnss_fix_qualified": False,
        "optical_output_qualified": False,
        "host_peripheral_hardware_qualified": False,
        "default_boot_tree_changed": False,
    }
