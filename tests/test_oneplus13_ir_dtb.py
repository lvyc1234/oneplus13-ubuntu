#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/oneplus13"))
ir = importlib.import_module("ir-dtb")
from stock import Fdt, cells, strings


class InfraredTree(unittest.TestCase):
    def setUp(self):
        stock = (
            ROOT / "devices/oneplus13/qualified-data/calibrated.dtb"
        ).read_bytes()
        self.before, _ = ir.modem.derive(stock)
        self.result, self.report = ir.derive(stock)
        self.before_tree = Fdt(self.before)
        self.tree = Fdt(self.result)

    def test_only_reviewed_existing_nodes_change(self):
        self.assertEqual(
            set(self.report["changed_nodes"]), {ir.L9B, ir.SPI}
        )
        for path, properties in self.before_tree.nodes.items():
            if path not in {ir.L9B, ir.SPI}:
                self.assertEqual(properties, self.tree.nodes[path], path)
        self.assertEqual(
            set(self.report["additional_nodes"]),
            {
                ir.TLMM + "/dodge-ir-mosi-active-state",
                ir.TLMM + "/dodge-ir-mosi-sleep-state",
                ir.IR,
            },
        )

    def test_exact_supply_pin_and_spi(self):
        self.assertEqual(
            cells(self.tree.nodes[ir.L9B]["regulator-max-microvolt"]),
            [3056000],
        )
        node = self.tree.nodes[ir.IR]
        self.assertEqual(strings(node["compatible"]), ["oneplus,dodge-ir-experimental"])
        self.assertEqual(cells(node["spi-max-frequency"]), [5000000])
        self.assertEqual(cells(node["irda_regulator-supply"]), [
            cells(self.tree.nodes[ir.L9B]["phandle"])[0]
        ])
        self.assertEqual(strings(self.tree.nodes[ir.SPI]["status"]), ["okay"])
        self.assertEqual(strings(self.tree.nodes[ir.I2C]["status"]), ["disabled"])
        self.assertNotIn("pinctrl-names", node)
        self.assertNotIn("pinctrl-0", node)
        self.assertNotIn("pinctrl-1", node)

    def test_optical_acceptance_stays_false(self):
        self.assertFalse(self.report["optical_output_qualified"])
        self.assertTrue(
            self.report[
                "shared_supply_consumers_not_in_qualified_dtb_require_runtime_audit"
            ]
        )

    def test_mutated_base_is_rejected(self):
        tree = Fdt(self.before)
        tree.nodes[ir.L9B]["regulator-max-microvolt"] = cells(b"")
        with self.assertRaises(ValueError):
            ir.derive(tree.to_blob())


if __name__ == "__main__":
    unittest.main()
