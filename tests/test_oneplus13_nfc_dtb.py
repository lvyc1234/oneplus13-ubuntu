#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/oneplus13"))
nfc = importlib.import_module("nfc-dtb")
from stock import Fdt, cells, strings


class NfcDefaultTree(unittest.TestCase):
    def setUp(self):
        self.stock = (
            ROOT / "devices/oneplus13/qualified-data/calibrated.dtb"
        ).read_bytes()
        self.before, _ = nfc.audio.derive(self.stock)
        self.result, self.report = nfc.derive(self.stock)
        self.before_tree = Fdt(self.before)
        self.tree = Fdt(self.result)

    def test_changes_only_reviewed_nfc_nodes(self):
        self.assertEqual(self.before_tree.reservations, self.tree.reservations)
        self.assertEqual(
            set(self.report["changed_nodes"]),
            {nfc.NFC_I2C},
        )
        for path, properties in self.before_tree.nodes.items():
            if path != nfc.NFC_I2C:
                self.assertEqual(properties, self.tree.nodes[path], path)
        self.assertEqual(strings(self.tree.nodes[nfc.NFC_I2C]["status"]), ["okay"])
        self.assertEqual(cells(self.tree.nodes[nfc.NFC_I2C]["clock-frequency"]), [400000])

    def test_exact_sn220_binding(self):
        node = self.tree.nodes[nfc.NFC]
        self.assertEqual(strings(node["compatible"]), ["nxp,nxp-nci-i2c"])
        self.assertEqual(cells(node["reg"]), [0x28])
        self.assertEqual(cells(node["interrupts"]), [75, 0])
        self.assertEqual(cells(node["enable-gpios"])[1:], [34, 0])
        self.assertEqual(strings(node["pinctrl-names"]), ["default", "sleep"])
        self.assertTrue(node["wakeup-source"] == b"")

    def test_clock_request_stays_with_firmware(self):
        node = self.tree.nodes[nfc.NFC]
        self.assertNotIn("clocks", node)
        self.assertNotIn("clock-names", node)
        self.assertTrue(self.report["clock_request_is_firmware_managed"])
        active = self.tree.nodes[
            nfc.TLMM + "/nfc-enable-active-state/enable-pins"
        ]
        sleep = self.tree.nodes[nfc.TLMM + "/nfc-sleep-state/sleep-pins"]
        irq = self.tree.nodes[nfc.TLMM + "/nfc-irq-active-state/irq-pins"]
        self.assertEqual(strings(active["pins"]), ["gpio34", "gpio35"])
        self.assertEqual(strings(sleep["pins"]), ["gpio34", "gpio35"])
        self.assertIn("bias-disable", active)
        self.assertIn("bias-disable", sleep)
        self.assertIn("bias-pull-down", irq)
        self.assertNotIn("bias-pull-up", irq)
        self.assertFalse(self.report["controller_bind_hardware_qualified"])
        self.assertFalse(self.report["tag_poll_hardware_qualified"])

    def test_mutated_base_is_rejected(self):
        tree = Fdt(self.stock)
        tree.nodes["/"]["model"] = b"other board\0"
        with self.assertRaises(ValueError):
            nfc.derive(tree.to_blob())


if __name__ == "__main__":
    unittest.main()
