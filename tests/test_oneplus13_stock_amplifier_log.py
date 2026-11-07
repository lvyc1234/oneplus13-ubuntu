# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('amp_log',Path(__file__).resolve().parents[1]/'tools/oneplus13/review-stock-amplifier-log.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class SavedAmplifierLog(unittest.TestCase):
    def setUp(self):
        self.header='enum { PID_2308_ID = 0x2308, };'
        self.init='''int aw882xx_init(struct aw882xx *aw882xx) {
switch (id) { case PID_2308_ID: return aw_pid_2308_dev_init(aw882xx); } }
static int aw_pid_2308_dev_init(struct aw882xx *aw882xx) {
aw_pa->ops.aw_get_voltage_offset = aw_pid_2308_get_voltage_offset; }
static int aw_pid_2308_get_voltage_offset(struct aw_device *aw_dev, int *v) { return 0; }
'''
        self.log='\n'.join('[Awinic][4-00'+address+']aw_pid_2308_get_voltage_offset: get vol offset val -1' for address in ['34','35'])
    def test_both_saved_callbacks_do_not_open_stream_gate(self):
        r=m.review(self.log,self.init,self.header)
        self.assertTrue(r['two_stock_amplifier_pid2308_callbacks_observed'])
        self.assertFalse(r['raw_chip_id_register_readback_present'])
        self.assertFalse(r['driver_loading_or_stream_eligible'])
    def test_one_device_rejected(self):
        with self.assertRaises(ValueError):m.review(self.log.splitlines()[0],self.init,self.header)
    def test_different_buses_rejected(self):
        with self.assertRaises(ValueError):m.review(self.log.replace('4-0035','5-0035'),self.init,self.header)
    def test_other_pid_or_callback_rejected(self):
        for log,init,header in [(self.log.replace('2308','2113'),self.init,self.header),
                               (self.log,self.init.replace('= aw_pid_2308_get_voltage_offset;','= another_callback;'),self.header),
                               (self.log,self.init,self.header.replace('0x2308','0x2113'))]:
            with self.assertRaises(ValueError):m.review(log,init,header)
    def test_oversize_saved_log_rejected(self):
        with self.assertRaises(ValueError):m.review('x'*262145,self.init,self.header)
if __name__=='__main__':unittest.main()

