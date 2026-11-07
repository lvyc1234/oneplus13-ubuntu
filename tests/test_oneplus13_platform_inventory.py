# SPDX-License-Identifier: GPL-2.0-only
"""Saved-filesystem checks of passive observations, without device access."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('inventory',Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-platform-inventory.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
    def put(self,path,value):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value);return p
    def test_missing_data_does_not_claim_readonly_blocks(self):
        facts=m.collect(self.root)
        self.assertFalse(facts['all_observed_block_devices_read_only'])
        self.assertFalse(facts['hardware_functional_acceptance'])
        self.assertEqual(facts['cpu_frequency_policies'],[])
    def test_writable_block_is_visible(self):
        self.put('sys/class/block/sda/ro','0')
        self.put('sys/class/block/loop0/ro','0')
        facts=m.collect(self.root)
        self.assertEqual(facts['hardware_block_devices_observed'],1)
        self.assertEqual(facts['writable_block_devices_observed'],1)
        self.assertFalse(facts['all_observed_block_devices_read_only'])
    def test_bound_and_unbound_devices_are_distinguished(self):
        self.put('sys/bus/scmi_protocol/devices/scmi_dev.1/modalias','scmi:13:cpufreq')
        self.put('sys/bus/scmi_protocol/devices/scmi_dev.2/modalias','scmi:13:genpd')
        driver=self.root/'sys/bus/scmi_protocol/drivers/scmi-cpufreq';driver.mkdir(parents=True)
        (self.root/'sys/bus/scmi_protocol/devices/scmi_dev.1/driver').symlink_to(driver,target_is_directory=True)
        facts=m.collect(self.root)['existing_driver_bindings']['scmi']
        self.assertEqual(facts[0]['driver'],'scmi-cpufreq')
        self.assertIsNone(facts[1]['driver'])
    def test_reading_keeps_saved_values_unchanged(self):
        p=self.put('sys/class/rtc/rtc0/since_epoch','62587177')
        self.put('sys/power/state','freeze mem disk')
        self.put('sys/class/thermal/thermal_zone0/temp','999999')
        facts=m.collect(self.root)
        self.assertEqual(p.read_text(),'62587177')
        self.assertFalse(facts['rtc_write_requested']);self.assertFalse(facts['suspend_executed'])
        self.assertFalse(facts['thermal_zones'][0]['plausible_range'])

if __name__=='__main__':unittest.main()
