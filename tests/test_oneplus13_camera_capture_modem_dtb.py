# SPDX-License-Identifier: GPL-2.0-only
import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/oneplus13"))
combined = importlib.import_module("camera-capture-modem-dtb")
from stock import Fdt, strings


class CameraCaptureModemTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        blob = (
            ROOT / "devices/oneplus13/qualified-data/calibrated.dtb"
        ).read_bytes()
        cls.dtb, cls.report = combined.derive(blob)
        cls.tree = Fdt(cls.dtb)

    def test_only_mpss_and_rmtfs_nodes_change_after_camera_graph(self):
        capture, _ = combined.capture.derive(
            (
                ROOT / "devices/oneplus13/qualified-data/calibrated.dtb"
            ).read_bytes()
        )
        before = Fdt(capture)
        after = self.tree
        changed = {
            path
            for path in before.nodes
            if before.nodes[path] != after.nodes[path]
        }
        self.assertEqual(changed, {combined.modem.MPSS})
        for path, properties in before.nodes.items():
            if path != combined.modem.MPSS:
                self.assertEqual(properties, after.nodes[path])
        self.assertEqual(set(before.nodes) | {combined.modem.RMTFS}, set(after.nodes))

    def test_mpss_remains_manual(self):
        node = self.tree.nodes[combined.modem.MPSS]
        self.assertEqual(strings(node["status"]), ["okay"])
        self.assertEqual(
            strings(node["firmware-name"]),
            list(combined.modem.FIRMWARE),
        )
        self.assertFalse(self.report["mpss"]["auto_boot_enabled"])
        self.assertTrue(self.report["mpss"]["manual_start_required"])
        self.assertFalse(self.report["camera_capture_qualified"])
        self.assertFalse(self.report["gnss_fix_qualified"])

    def test_capture_route_is_retained(self):
        self.assertTrue(self.report["capture_pipeline_present"])
        self.assertEqual(self.report["camera"]["csiphy_index"], 1)
        self.assertEqual(self.report["camera"]["csid_index"], 1)
        self.assertEqual(self.report["camera"]["vfe_index"], 1)

    def test_csiphy_graph_endpoints_have_ids(self):
        for phy in combined.capture.PHYS:
            for port in (0, 1):
                port_path = phy["path"] + f"/ports/port@{port}"
                endpoint = port_path + "/endpoint@0"
                self.assertIn(endpoint, self.tree.nodes)
                self.assertEqual(
                    self.tree.nodes[port_path]["#address-cells"],
                    b"\0\0\0\1",
                )
                self.assertEqual(
                    self.tree.nodes[port_path]["#size-cells"],
                    b"\0\0\0\0",
                )
                self.assertEqual(
                    self.tree.nodes[endpoint]["reg"], b"\0\0\0\0"
                )
                if port == 0:
                    from stock import cells
                    self.assertEqual(
                        cells(self.tree.nodes[endpoint]["bus-type"]),
                        [combined.capture.V4L2_FWNODE_BUS_TYPE_CSI2_CPHY],
                    )
        selected = (
            combined.capture.PHYS[1]["path"]
            + "/ports/port@0/endpoint@0"
        )
        self.assertIn("remote-endpoint", self.tree.nodes[selected])
        sensor_endpoint = (
            combined.capture.probe.SENSOR + "/port/endpoint"
        )
        from stock import cells
        self.assertEqual(
            cells(self.tree.nodes[sensor_endpoint]["bus-type"]),
            [combined.capture.V4L2_FWNODE_BUS_TYPE_CSI2_CPHY],
        )
        camss_endpoint = (
            combined.capture.CAMSS + "/ports/port@1/endpoint@0"
        )
        phy_output = (
            combined.capture.PHYS[1]["path"]
            + "/ports/port@1/endpoint@0"
        )
        self.assertEqual(
            cells(self.tree.nodes[camss_endpoint]["remote-endpoint"]),
            [cells(self.tree.nodes[phy_output]["phandle"])[0]],
        )
        self.assertEqual(
            cells(self.tree.nodes[camss_endpoint]["bus-type"]),
            [combined.capture.V4L2_FWNODE_BUS_TYPE_CSI2_CPHY],
        )
        for index in (0, 2, 3, 4, 5):
            port = combined.capture.CAMSS + f"/ports/port@{index}"
            self.assertNotIn(port, self.tree.nodes)
        selected_port = combined.capture.CAMSS + "/ports/port@1"
        self.assertNotIn("status", self.tree.nodes[selected_port])


if __name__ == "__main__":
    unittest.main()
