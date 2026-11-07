# SPDX-License-Identifier: GPL-2.0-only
import importlib.util,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('watchdog_recovery',ROOT/'tools/oneplus13/ram-watchdog-recovery.py')
wdt=importlib.util.module_from_spec(spec);spec.loader.exec_module(wdt)


class WatchdogInventory(unittest.TestCase):
    def test_video_guard_deadline_does_not_extend_with_keepalives(self):
        now=[0];pings=[];records=[]
        def monotonic():now[0]+=1;return now[0]
        report={}
        with patch.object(wdt.time,'monotonic',side_effect=monotonic), \
             patch.object(wdt,'record',side_effect=lambda r:records.append(dict(r))), \
             patch.object(wdt,'busy_wait') as wait:
            with self.assertRaisesRegex(RuntimeError,'did not reset'):
                wdt.video_guard_window(lambda:False,lambda:pings.append(now[0]),report)
        self.assertTrue(report['guard_deadline_expired'])
        self.assertTrue(report['reset_failed'])
        self.assertEqual(report['guard_deadline_monotonic'],91)
        self.assertTrue(pings and max(pings)<91)
        self.assertEqual({r['guard_deadline_monotonic'] for r in records},{91})
        wait.assert_called_once_with(45)

    def test_video_guard_clean_stop_requires_no_expiry_wait(self):
        report={}
        with patch.object(wdt.time,'monotonic',side_effect=[0,1]), \
             patch.object(wdt,'record'),patch.object(wdt,'busy_wait') as wait, \
             patch.object(wdt,'ioctl_int') as ioctl:
            wdt.video_guard_window(lambda:True,lambda:ioctl(),report)
        self.assertTrue(report['guard_completed']);self.assertFalse(report['guard_running'])
        wait.assert_not_called();ioctl.assert_not_called()

    def test_video_guard_ping_error_preserves_awake_expiry_window(self):
        report={}
        with patch.object(wdt.time,'monotonic',side_effect=[0,3]), \
             patch.object(wdt,'record'),patch.object(wdt,'busy_wait') as wait:
            with self.assertRaisesRegex(RuntimeError,'guard error did not reset'):
                wdt.video_guard_window(lambda:False,lambda:(_ for _ in ()).throw(OSError('ping')),report)
        self.assertEqual(report['guard_failure_category'],'OSError')
        self.assertTrue(report['reset_failed']);wait.assert_called_once_with(45)

    def test_video_stop_refuses_bad_token_active_module_and_incomplete_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);marker=base/'stop';result=base/'result';module=base/'module'
            token='a'*32;marker.write_text(token+'\n');marker.chmod(0o600)
            receipt={'cleanup_pass':True,'module_unloaded':True,'video_nodes_removed':True}
            result.write_text(json.dumps(receipt))
            original=Path.lstat
            def root_owned(path,*args,**kwargs):
                values=list(original(path,*args,**kwargs));values[4]=0
                return os.stat_result(values)
            with patch.object(Path,'lstat',root_owned):
                self.assertTrue(wdt.video_stop_requested(token,marker,result,module))
                self.assertFalse(wdt.video_stop_requested('b'*32,marker,result,module))
                module.mkdir();self.assertFalse(wdt.video_stop_requested(token,marker,result,module));module.rmdir()
                for key in receipt:
                    bad=dict(receipt);bad[key]=False;result.write_text(json.dumps(bad))
                    self.assertFalse(wdt.video_stop_requested(token,marker,result,module))
                result.write_text(json.dumps(receipt));marker.chmod(0o644)
                self.assertFalse(wdt.video_stop_requested(token,marker,result,module))
                marker.chmod(0o600);marker.unlink();marker.symlink_to(result)
                self.assertFalse(wdt.video_stop_requested(token,marker,result,module))

    def test_busy_wait_has_no_sleep_or_keepalive(self):
        with patch.object(wdt.time,'monotonic',side_effect=[10,10.5,11,12]), \
             patch.object(wdt.time,'sleep') as sleep, \
             patch.object(wdt,'ioctl_int') as ioctl:
            wdt.busy_wait(2)
        sleep.assert_not_called();ioctl.assert_not_called()

    def test_clock_probe_has_no_ping_during_measurement_and_restores_affinity(self):
        class Clock:
            now=0
            def monotonic(self):
                self.now+=0.25
                return self.now
            def sleep(self,seconds):self.now+=seconds
        clock=Clock();events=[];report={}
        with patch.object(wdt.os,'sched_getaffinity',return_value={2,3},create=True), \
             patch.object(wdt.os,'sched_setaffinity',side_effect=lambda pid,cpus:events.append(('affinity',cpus)),create=True), \
             patch.object(wdt.time,'monotonic',side_effect=clock.monotonic), \
             patch.object(wdt.time,'sleep',side_effect=clock.sleep):
            wdt.clock_probe(lambda label:events.append(('sample',label)),report,
                            lambda:events.append(('ping',None)))
        self.assertEqual(events,[('affinity',{2}),('sample','clock-probe-start'),
            ('sample','clock-probe-idle-1'),('sample','clock-probe-busy-2'),
            ('sample','clock-probe-idle-3'),('ping',None),
            ('sample','clock-probe-after-keepalive'),('affinity',{2,3})])
        self.assertEqual([x['mode'] for x in report['clock_intervals']],['idle','busy','idle'])
        self.assertNotIn('countdown_verified',report)
        self.assertNotIn('reset_observed',report)

    def test_clock_probe_restores_affinity_on_status_failure_without_ping(self):
        with patch.object(wdt.os,'sched_getaffinity',return_value={0,1},create=True), \
             patch.object(wdt.os,'sched_setaffinity',create=True) as affinity, \
             patch.object(wdt.time,'sleep') as sleep:
            with self.assertRaisesRegex(ValueError,'status'):
                wdt.clock_probe(lambda label:(_ for _ in ()).throw(ValueError('status')),{},
                                lambda:self.fail('Unexpected ping'))
        self.assertEqual(affinity.call_args_list[-1].args,(0,{0,1}))
        sleep.assert_not_called()

    def test_refuse_foreign_active_nowayout_and_invalid_device_before_open(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            original={'identity':'Gunyah Watchdog','state':'inactive','timeout':'32','nowayout':'0','dev':'241:1'}
            for key,value in original.items():(base/key).write_text(value)
            self.assertEqual(wdt.validate_inventory(base),(241,1))
            for key,value in [('identity','other'),('state','active'),('timeout','60'),
                              ('nowayout','1'),('dev','241:1:2'),('dev','../other')]:
                with self.subTest(key=key,value=value):
                    (base/key).write_text(value)
                    with self.assertRaises(ValueError):wdt.validate_inventory(base)
                    (base/key).write_text(original[key])

    def test_read_only_status_samples_and_reject_bad_hypervisor_values(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);(base/'device').mkdir();path=base/'device/hypervisor_status'
            self.assertFalse(wdt.status_sample(base,'legacy')['available'])
            with self.assertRaises(ValueError):wdt.status_sample(base,'required',True)
            path.write_text('result=0 enabled=1 expired=0 elapsed_raw=2011\n')
            result=wdt.status_sample(base,'countdown',True)
            self.assertEqual(result['elapsed_raw'],2011)
            self.assertEqual(result['enabled'],1)
            self.assertEqual(result['label'],'countdown')
            self.assertGreater(result['monotonic_seconds'],0)
            for text in ('result=1 enabled=1 expired=0 elapsed_raw=0',
                         'result=0 enabled=2 expired=0 elapsed_raw=0',
                         'result=0 enabled=1 expired=2 elapsed_raw=0',
                         'result=0 enabled=1 expired=0 elapsed_raw=-1',
                         'result=0 enabled=1 expired=0 elapsed_raw=4294967296',
                         'result=0 enabled=1 enabled=0 elapsed_raw=1',
                         'result=0 enabled=1 expired=0 elapsed_raw=1 unknown=0',
                         'result=0 enabled=1 expired=0 elapsed_raw=abc',
                         'x'*257):
                with self.subTest(text=text):
                    path.write_text(text)
                    with self.assertRaises(ValueError):wdt.status_sample(base,'bad',True)


if __name__=='__main__':unittest.main()
