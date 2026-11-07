# SPDX-License-Identifier: GPL-2.0-only
import importlib.util,json,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'tools/oneplus13/watchdog_receipt.py'
s=importlib.util.spec_from_file_location('receipt',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class Receipt(unittest.TestCase):
    def report(self):
        return dict(active_wait_required=True,armed=True,awake_cpu=0,countdown_verified=True,
                    keepalive_verified=True,scope='ONE_MANUAL_RAM_GUNYAH_AWAKE_RESET',
                    idle_or_suspend_recovery_qualified=False,suspend_requested=False,
                    reset_observed=False,partition_writes=0,first_timeleft=12,second_timeleft=10,
                    third_timeleft=12,set_timeout_readback=12,get_timeout_readback=12,
                    timeout_seconds=12,support_options=0x8180)
    def test_new_first_key_and_reordered_json_with_journal_lines(self):
        report=self.report()
        for sort in (False,True):
            text='RAM_KERNEL_HEALTH_RESULT {}\n'+json.dumps(report,sort_keys=sort)+'\nJOURNAL: WATCHDOG_RECOVERY_RESULT '+json.dumps(report)+'\n'
            parsed=m.parse(text);self.assertEqual(m.validate_armed(parsed,report['scope']),report)
    def test_missing_ambiguous_duplicate_and_oversized_receipts_refused(self):
        valid=json.dumps(self.report())
        for text in ('journal only',valid+'\n'+valid,'{"armed":true,"armed":false}',valid+' '*65536):
            with self.subTest(text=text[:30]),self.assertRaises(ValueError):m.parse(text)
    def test_clock_or_failed_receipt_cannot_be_an_arm(self):
        for key,value in [('scope','ONE_MANUAL_RAM_GUNYAH_CLOCK_DIAGNOSTIC'),('armed',False),
                          ('countdown_verified',False),('keepalive_verified',False),
                          ('second_timeleft',12),('third_timeleft',10),('partition_writes',1),
                          ('get_timeout_readback',32),('support_options',0),('first_timeleft',True),
                          ('active_wait_required',False),('idle_or_suspend_recovery_qualified',True)]:
            bad=self.report();bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                m.validate_armed(bad,self.report()['scope'])
    def test_arm_is_never_observed_reset_acceptance(self):
        result=m.validate_armed(self.report(),self.report()['scope'])
        self.assertFalse(result['reset_observed'])
        self.assertFalse(result['idle_or_suspend_recovery_qualified'])
    def test_video_guard_requires_active_fixed_window(self):
        result=self.report();result.update(scope='ONE_MANUAL_RAM_GUNYAH_AWAKE_VIDEO_GUARD',
                                          guard_running=True,guard_seconds=90)
        m.validate_armed(result,result['scope'])
        for key,value in [('guard_running',False),('guard_seconds',150)]:
            bad=dict(result);bad[key]=value
            with self.assertRaises(ValueError):m.validate_armed(bad,result['scope'])

if __name__=='__main__':unittest.main()
