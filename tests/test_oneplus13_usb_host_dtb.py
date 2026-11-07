#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/oneplus13"))
usb = importlib.import_module("usb-host-dtb")
from stock import Fdt, strings


class UsbHostTree(unittest.TestCase):
    def setUp(self):
        self.stock = (
            ROOT / "devices/oneplus13/qualified-data/calibrated.dtb"
        ).read_bytes()
        self.before, _ = usb.ir.derive(self.stock)
        self.result, self.report = usb.derive(self.stock)
        self.before_tree = Fdt(self.before)
        self.tree = Fdt(self.result)

    def test_only_usb_role_properties_change(self):
        self.assertEqual(self.report["changed_nodes"], [usb.USB])
        for path, properties in self.before_tree.nodes.items():
            if path != usb.USB:
                self.assertEqual(properties, self.tree.nodes[path], path)
        node = self.tree.nodes[usb.USB]
        self.assertEqual(strings(node["dr_mode"]), ["otg"])
        self.assertEqual(strings(node["role-switch-default-mode"]), ["device"])
        self.assertIn("usb-role-switch", node)

    def test_host_acceptance_is_not_inferred(self):
        self.assertTrue(self.report["debugfs_role_control_available"])
        self.assertFalse(self.report["host_peripheral_hardware_qualified"])

    def test_wrong_stock_mode_is_rejected(self):
        tree = Fdt(self.before)
        tree.nodes[usb.USB]["dr_mode"] = b"host\0"
        with self.assertRaises(ValueError):
            usb.derive(tree.to_blob())


if __name__ == "__main__":
    unittest.main()
