#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/oneplus13"))
modem = importlib.import_module("modem-gnss-dtb")
from stock import Fdt, strings


class ModemGnssTree(unittest.TestCase):
    def setUp(self):
        self.stock = (
            ROOT / "devices/oneplus13/qualified-data/calibrated.dtb"
        ).read_bytes()
        self.before, _ = modem.video.derive(self.stock)
        self.result, self.report = modem.derive(self.stock)
        self.before_tree = Fdt(self.before)
        self.tree = Fdt(self.result)

    def test_only_mpss_and_rmtfs_nodes_change(self):
        self.assertEqual(self.report["changed_nodes"], sorted([modem.MPSS, modem.RMTFS]))
        for path, properties in self.before_tree.nodes.items():
            if path != modem.MPSS:
                self.assertEqual(properties, self.tree.nodes[path], path)
        self.assertEqual(set(self.before_tree.nodes) | {modem.RMTFS}, set(self.tree.nodes))

    def test_exact_mpss_firmware_and_resources(self):
        node = self.tree.nodes[modem.MPSS]
        self.assertEqual(strings(node["status"]), ["okay"])
        self.assertEqual(strings(node["firmware-name"]), list(modem.FIRMWARE))
        self.assertEqual(
            self.report["memory_regions"][:2],
            [
                "/reserved-memory/mpss@8ba00000",
                "/reserved-memory/q6-mpss-dtb@9b000000",
            ],
        )
        self.assertTrue(self.report["manual_remoteproc_start_required"])
        self.assertFalse(self.report["auto_boot_enabled"])

    def test_exact_rmtfs_sharedmem_node(self):
        node = self.tree.nodes[modem.RMTFS]
        self.assertEqual(strings(node["compatible"]), ["qcom,sharedmem-uio"])
        self.assertEqual(strings(node["reg-names"]), ["rmtfs"])
        self.assertEqual(
            node["reg"],
            bytes([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x40, 0, 0]),
        )
        self.assertEqual(len(node["reg"]), 16)
        self.assertEqual(
            self.tree.nodes["/soc@0"]["#address-cells"],
            bytes([0, 0, 0, 2]),
        )
        self.assertEqual(
            self.tree.nodes["/soc@0"]["#size-cells"],
            bytes([0, 0, 0, 2]),
        )
        self.assertEqual(node["qcom,client-id"], bytes([0, 0, 0, 1]))
        self.assertEqual(self.report["rmtfs_sharedmem_node"], modem.RMTFS)

    def test_functional_acceptance_remains_false(self):
        self.assertFalse(self.report["firmware_authentication_qualified"])
        self.assertFalse(self.report["modem_service_qualified"])
        self.assertFalse(self.report["gnss_service_qualified"])

    def test_changed_base_is_rejected(self):
        tree = Fdt(self.stock)
        tree.nodes["/"]["model"] = b"other board\0"
        with self.assertRaises(ValueError):
            modem.derive(tree.to_blob())


if __name__ == "__main__":
    unittest.main()
