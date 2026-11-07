# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-kernel-health.py'
spec=importlib.util.spec_from_file_location('health',path)
health=importlib.util.module_from_spec(spec);spec.loader.exec_module(health)

class Health(unittest.TestCase):
    def test_range_timeout_and_oom_are_not_clean_health(self):
        result=health.classify('ath12k_wifi7_pci: failed to start next scan range: -110\nOut of memory: Killed process 12 (gnome-shell)\n')
        self.assertFalse(result['pass'])
        self.assertEqual(result['counts']['wifi_driver_failure'],1)
        self.assertEqual(result['counts']['out_of_memory'],2)

    def test_dma_fault_and_recovery_cannot_be_labeled_healthy(self):
        text='''[ 175.3] arm-smmu 15000000.iommu: Unhandled context fault: fsr=0x402
[ 176.2] ieee80211 phy0: Hardware restart was requested
[ 180.5] ath12k_wifi7_pci: invalid pdev_id 1 in mgmt_rx_event
'''
        result=health.classify(text)
        self.assertFalse(result['pass'])
        for key in ['iommu_context_fault','wifi_hardware_restart','wifi_invalid_runtime_id']:
            self.assertEqual(result['counts'][key],1)

    def test_unrelated_telemetry_and_normal_firmware_metadata_are_not_faults(self):
        result=health.classify('ath12k: fw_version 0x10048fe1\nqcom_battmgr: unknown notification: 0x58\n')
        self.assertTrue(result['pass'])

    def test_iommu_events_remain_detected_before_a_long_log_tail(self):
        result=health.classify('Unhandled context fault\n'+'normal event\n'*1000)
        self.assertFalse(result['pass'])
        self.assertNotIn('normal event',str(result))

if __name__=='__main__':unittest.main()
