# SPDX-License-Identifier: GPL-2.0-only
import importlib,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/oneplus13'))
probe=importlib.import_module('camera-probe-dtb')
from stock import Fdt,cells,strings


class CameraProbeTree(unittest.TestCase):
    def test_real_qualified_tree_preserves_all_unrelated_properties(self):
        blob=(ROOT/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes()
        mic,_=probe.mic.derive(blob);before=Fdt(mic)
        result,report=probe.derive(blob);after=Fdt(result)
        self.assertEqual(before.reservations,after.reservations)
        allowed=set(report['additional_changed_nodes'])
        for path,properties in before.nodes.items():
            if path not in allowed:self.assertEqual(properties,after.nodes[path],path)
        self.assertEqual(allowed,{probe.CAMCC,probe.MCLK,probe.CORE,probe.NFC_I2C})
        for path in (probe.CAMCC,probe.MCLK):
            self.assertEqual({k:v for k,v in after.nodes[path].items() if k!='phandle'},before.nodes[path])
        self.assertEqual(cells(after.nodes[probe.CORE]['regulator-max-microvolt']),[1000000])
        self.assertFalse(report['physical_power_sequence_qualified'])
        self.assertFalse(report['camera_capture_qualified'])
        self.assertFalse(report['camss_or_phy_enabled'])
        self.assertTrue(report['vddm_shared_core_vote_is_inferred'])

    def test_exact_chip_id_probe_wiring_and_manual_boundary(self):
        blob=(ROOT/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes()
        result,report=probe.derive(blob);tree=Fdt(result)
        sensor=tree.nodes[probe.SENSOR];cci=tree.nodes[probe.CCI]
        self.assertEqual(strings(sensor['compatible']),['samsung,s5kjn5'])
        self.assertEqual(cells(sensor['reg']),[0x2d])
        self.assertEqual(report['cci_i2c_address'],0x5a>>1)
        self.assertEqual(probe.SENSOR.rsplit('/',1)[1],'camera@2d')
        self.assertEqual(cells(sensor['reset-gpios'])[1:],[178,1])
        self.assertEqual(cells(sensor['assigned-clock-rates']),[19200000])
        self.assertEqual(cells(cci['interrupts']),[0,426,1])
        self.assertEqual(cells(cci['reg']),[0,0xac7b000,0,0x1000])
        self.assertEqual(cells(tree.nodes[probe.SENSOR+'/port/endpoint']['data-lanes']),[1,2,3,4])
        self.assertNotIn('vddio1p2-supply',sensor)
        self.assertEqual(sensor['vddm-supply'],sensor['vddd-supply'])
        self.assertTrue(report['requires_manual_sensor_and_cci_module_policy'])
        self.assertEqual(report['nfc_candidate']['i2c'],probe.NFC_I2C)
        self.assertEqual(report['nfc_candidate']['address'],0x28)
        self.assertFalse(report['nfc_candidate']['hardware_qualified'])
        nfc=tree.nodes[probe.NFC]
        self.assertEqual(strings(nfc['compatible']),['nxp,nxp-nci-i2c'])
        self.assertEqual(cells(nfc['reg']),[0x28])
        self.assertEqual(cells(nfc['enable-gpios'])[1:],[34,0])
        self.assertEqual(cells(nfc['interrupts']),[75,0])
        self.assertEqual(strings(tree.nodes[probe.NFC_I2C]['status']),['okay'])
        self.assertEqual(cells(tree.nodes[probe.NFC_I2C]['clock-frequency']),[400000])

    def test_mutated_or_foreign_tree_is_rejected_before_derivation(self):
        blob=(ROOT/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes()
        tree=Fdt(blob);tree.nodes['/']['model']=b'other board\0'
        with self.assertRaises(ValueError):probe.derive(tree.to_blob())


if __name__=='__main__':unittest.main()
