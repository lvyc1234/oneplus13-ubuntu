# SPDX-License-Identifier: GPL-2.0-only
import importlib.util,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
m=load('samples',ROOT/'tools/oneplus13/ram-ssc-samples.py')
d=load('sample_discovery',ROOT/'tools/oneplus13/ram-sensor-discovery.py')
fixture=load('sample_reverse_fixture',ROOT/'tests/test_oneplus13_sensor_fileserver.py')
fs=load('sample_reverse',ROOT/'tools/oneplus13/ram-sensor-fileserver.py')
GOOD='\n'.join('SSC_PHONE_SAMPLE type='+name+' enabled=1 disabled=1 count=15 invalid=0 norm_min=1 norm_max=10' for name in ['accel','gyro','mag'])+'\nSSC_PHONE_SAMPLE_RESULT complete=yes rate_hz=5 collection_ms=3000 cleanup_returned=yes\n'
class Samples(unittest.TestCase):
    def empty_result(self):
        output='\n'.join('SSC_PHONE_SAMPLE type='+name+' enabled=0 disabled=0 count=0 invalid=0 norm_min=0 norm_max=0' for name in ['accel','gyro','mag'])+'\nSSC_PHONE_SAMPLE_RESULT complete=no rate_hz=5 collection_ms=3000 cleanup_returned=yes\n'
        return SimpleNamespace(returncode=1,stdout=output,stderr='SSC_PHONE_LOOKUP type=accel default=1 count=0\n')
    def test_only_clean_empty_lookup_with_no_enables_can_retry(self):
        result=self.empty_result();self.assertEqual(m.empty_default_lookups(result),['accel'])
        for change in [('enabled=0','enabled=1'),('disabled=0','disabled=1'),('count=0','count=1'),('cleanup_returned=yes','cleanup_returned=no')]:
            bad=SimpleNamespace(returncode=1,stdout=result.stdout.replace(*change,1),stderr=result.stderr)
            self.assertEqual(m.empty_default_lookups(bad),[])
        self.assertEqual(m.empty_default_lookups(SimpleNamespace(returncode=-11,stdout=result.stdout,stderr=result.stderr)),[])
        self.assertEqual(m.empty_default_lookups(SimpleNamespace(returncode=1,stdout=result.stdout,stderr='unrelated error')),[])
    def test_one_empty_lookup_retry_keeps_original_process_deadline_and_cleanup(self):
        calls=[];executions=[0]
        def run(command,**kwargs):
            calls.append((command,kwargs['timeout']))
            if command[0]=='/usr/bin/systemd-run':
                executions[0]+=1
                return self.empty_result() if executions[0]==1 else SimpleNamespace(returncode=0,stdout=GOOD,stderr='')
            if command[1]=='is-active':return SimpleNamespace(returncode=3,stdout='failed' if executions[0]==1 else 'inactive',stderr='')
            if command[1] in ('stop','reset-failed'):return SimpleNamespace(returncode=0,stdout='',stderr='')
            self.fail(command)
        with tempfile.TemporaryDirectory() as folder,patch.object(m.time,'monotonic',side_effect=[0,1,3]),patch.object(m.time,'sleep') as sleep,patch.object(m.metadata,'save_private_failure',return_value={'saved':True}) as save:
            result=m.run_probe('sample',state=Path(folder),run=run)
        self.assertEqual(result['empty_default_lookup_retries'],1);self.assertEqual(executions[0],2)
        self.assertEqual([timeout for cmd,timeout in calls if cmd[0]=='/usr/bin/systemd-run'],[16,13])
        self.assertEqual(sum(cmd[1]=='reset-failed' for cmd,_ in calls),1)
        self.assertEqual(sum(cmd[1]=='stop' for cmd,_ in calls),2)
        sleep.assert_called_once_with(1);save.assert_called_once()
    def test_receipt_requires_three_finite_streams_and_stop(self):
        self.assertEqual(set(m.interpret(GOOD)['samples']),m.TYPES)
        for old,new in [('type=mag','type=gyro'),('disabled=1','disabled=0'),('count=15','count=2'),
                        ('invalid=0','invalid=1'),('norm_min=1','norm_min=nan'),('cleanup_returned=yes','cleanup_returned=no')]:
            with self.subTest(new=new),self.assertRaises(ValueError):m.interpret(GOOD.replace(old,new,1))
    def test_isolated_verify_cannot_query_qrtr(self):
        unit,args=m.command('verify')
        self.assertIn('samples-verify',unit)
        self.assertIn('--property=PrivateNetwork=yes',args)
        self.assertIn('--property=RestrictAddressFamilies=AF_UNIX',args)
        self.assertTrue(args[-1].endswith('/tests/phone-sample-tests'))
        unit,args=m.command('sample')
        for prop in ['User=65534','PrivateDevices=yes','DevicePolicy=closed','RuntimeMaxSec=20s','RestrictAddressFamilies=AF_UNIX AF_QIPCRTR']:
            self.assertIn('--property='+prop,args)
        self.assertNotIn('--verify-lock',args)
        self.assertIn('--setenv=SSC_SUSPEND_MODE=nowakeup',args)
        self.assertFalse(any('SSC_BATCH_PERIOD_US' in x or 'SSC_FLUSH_PERIOD_US' in x for x in args))
        self.assertTrue(args[-1].endswith('/probe/phone-ssc-sample-probe'))
    def test_composite_uses_one_bounded_query_and_accepts_collected_transient(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);(state/'syscalls.log').write_text(fixture.TRACE);calls=[]
            def run(command,**kw):
                calls.append((command,kw['timeout']))
                if command[0]=='/usr/bin/systemd-run':return SimpleNamespace(returncode=0,stdout=GOOD,stderr='')
                if command[1]=='stop':return SimpleNamespace(returncode=5,stdout='',stderr='not loaded')
                if command[1]=='is-active':return SimpleNamespace(returncode=4,stdout='unknown',stderr='')
                raise AssertionError(command)
            answer=d.metadata_callback(SimpleNamespace(STATE=tmp,trace_facts=fs.trace_facts),m,run=run,sample_mode=True)
            self.assertTrue(answer['cleanup_returned']);self.assertEqual([t for c,t in calls],[16,3,2])
    def test_stop_timeout_still_checks_inactive_and_refuses(self):
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            if command[0]=='/usr/bin/systemd-run':raise subprocess.TimeoutExpired(command,20)
            if command[1]=='stop':raise subprocess.TimeoutExpired(command,3)
            return SimpleNamespace(returncode=3,stdout='inactive',stderr='')
        with self.assertRaises(subprocess.TimeoutExpired):m.run_probe('sample',run=run)
        self.assertEqual([c[1] for c in calls[1:]],['stop','is-active'])
if __name__=='__main__':unittest.main()
