# SPDX-License-Identifier: GPL-2.0-only
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/oneplus13"))
from stock import Fdt, strings

peripherals = importlib.import_module("camera-gnss-ir-usb-dtb")


class CameraGnssPeripheralsTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stock = (
            ROOT / "devices/oneplus13/qualified-data/calibrated.dtb"
        ).read_bytes()
        cls.blob, cls.report = peripherals.derive(stock)
        cls.tree = Fdt(cls.blob)

    def test_camera_and_mpss_remain_present(self):
        self.assertIn("/soc@0/isp@ad27000", self.tree.nodes)
        self.assertEqual(
            strings(self.tree.nodes["/soc@0/remoteproc@4080000"]["status"]),
            ["okay"],
        )
        self.assertIn(
            "/soc@0/cci@ac7b000/i2c-bus@0/camera@2d",
            self.tree.nodes,
        )

    def test_ir_node_uses_reviewed_spi_and_supply(self):
        ir = self.tree.nodes["/soc@0/geniqup@ac0000/spi@a94000/ir@0"]
        self.assertEqual(
            strings(ir["compatible"]),
            ["oneplus,dodge-ir-experimental"],
        )
        self.assertEqual(
            strings(
                self.tree.nodes["/soc@0/geniqup@ac0000/spi@a94000"][
                    "status"
                ]
            ),
            ["okay"],
        )
        self.assertEqual(self.report["supply_microvolt"], 3056000)
        self.assertEqual(self.report["ir_gpio"], 53)

    def test_usb_role_switch_defaults_to_device(self):
        usb = self.tree.nodes["/soc@0/usb@a600000"]
        self.assertEqual(strings(usb["dr_mode"]), ["otg"])
        self.assertEqual(
            strings(usb["role-switch-default-mode"]), ["device"]
        )
        self.assertTrue(self.report["usb_host_role_control"])
        self.assertFalse(self.report["host_peripheral_hardware_qualified"])

    def test_physical_acceptance_remains_explicitly_false(self):
        for key in (
            "camera_capture_qualified",
            "nfc_tag_qualified",
            "gnss_fix_qualified",
            "optical_output_qualified",
            "host_peripheral_hardware_qualified",
            "default_boot_tree_changed",
        ):
            self.assertIs(self.report[key], False)


if __name__ == "__main__":
    unittest.main()
