# SPDX-License-Identifier: GPL-2.0-only
"""Exercise real composite readiness, query policy and cleanup failure paths."""
import importlib.util,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace

HERE=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
m=load('discovery',HERE/'tools/oneplus13/ram-sensor-discovery.py')
ssc=load('ssc_guard',HERE/'tools/oneplus13/ram-ssc-metadata.py')
fs=load('reverse_guard',HERE/'tools/oneplus13/ram-sensor-fileserver.py')
fixture=load('reverse_fixture',HERE/'tests/test_oneplus13_sensor_fileserver.py')
GOOD='\n'.join('SSC_PHONE_METADATA type='+n+' available=yes completed=yes' for n in sorted(ssc.TYPES))+'\nSSC_PHONE_METADATA_RESULT complete=yes samples_requested=no cleanup_returned=yes\n'

class CompositeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name);self.trace=self.state/'syscalls.log';self.trace.write_text(fixture.TRACE)
        self.fs=SimpleNamespace(STATE=str(self.state),trace_facts=fs.trace_facts)
        self.now=0;self.calls=[]
    def clock(self):return self.now
    def sleep(self,value):self.now+=value
    def runner(self,output=GOOD,query_error=None,stop_error=None,active=False,exitcode=0):
        def run(command,**kw):
            self.calls.append((command,kw['timeout']))
            if command[0]=='/usr/bin/systemd-run':
                if query_error:raise query_error
                return SimpleNamespace(returncode=exitcode,stdout=output,stderr='')
            if command[1]=='stop':
                if stop_error:raise stop_error
                return SimpleNamespace(returncode=0,stdout='',stderr='')
            if command[1]=='is-active':return SimpleNamespace(returncode=0 if active else 3,stdout='active' if active else 'inactive',stderr='')
            if command[1]=='show':return SimpleNamespace(returncode=4,stdout='',stderr='')
            if command[1]=='reset-failed':return SimpleNamespace(returncode=0,stdout='',stderr='')
            raise AssertionError(command)
        return run
    def probe(self,**kw):return m.metadata_callback(self.fs,ssc,run=self.runner(**kw),clock=self.clock,sleep=self.sleep)
    def test_real_trace_and_all_four_types(self):
        facts=self.probe();self.assertEqual(facts['metadata_available'],sorted(ssc.TYPES))
        self.assertTrue(facts['cleanup_returned']);self.assertFalse(facts['sample_data_or_axes_accepted'])
        self.assertEqual([t for c,t in self.calls],[16,3,2])
    def test_query_is_one_only_and_guard_sandbox_is_preserved(self):
        self.probe();query=self.calls[0][0]
        self.assertEqual(sum(c[0]=='/usr/bin/systemd-run' for c,t in self.calls),1)
        for prop in ('PrivateDevices=yes','DevicePolicy=closed','User=65534','RuntimeMaxSec=20s','RestrictAddressFamilies=AF_UNIX AF_QIPCRTR'):
            self.assertIn('--property='+prop,query)
        self.assertNotIn('--verify-lock',query)
    def test_not_ready_never_sends_and_expires(self):
        self.trace.write_text('')
        with self.assertRaises(ValueError):self.probe()
        self.assertEqual(self.calls,[]);self.assertAlmostEqual(self.now,3)
    def test_missing_trace_never_sends(self):
        self.trace.unlink()
        with self.assertRaises(ValueError):self.probe()
        self.assertEqual(self.calls,[])
    def test_trace_partial_then_ready_is_not_retried_query(self):
        self.trace.write_text('')
        def delayed(value):self.now+=value;self.trace.write_text(fixture.TRACE)
        m.metadata_callback(self.fs,ssc,run=self.runner(),clock=self.clock,sleep=delayed)
        self.assertEqual(len(self.calls),3)
    def test_oversized_trace_refuses_before_query(self):
        self.trace.write_bytes(b'x'*(4*1024*1024+1))
        with self.assertRaises(ValueError):self.probe()
        self.assertEqual(self.calls,[])
    def test_duplicate_attach_never_sends(self):
        self.trace.write_text(fixture.TRACE+'\n'+fixture.TRACE)
        with self.assertRaises(ValueError):self.probe()
        self.assertEqual(self.calls,[])
    def test_query_timeout_still_stops_and_checks_state(self):
        with self.assertRaises(subprocess.TimeoutExpired):self.probe(query_error=subprocess.TimeoutExpired('query',16))
        self.assertEqual([c[1] for c,t in self.calls[1:]],['stop','reset-failed','is-active'])
    def test_query_launch_error_still_stops(self):
        with self.assertRaises(OSError):self.probe(query_error=OSError('synthetic launch failure'))
        self.assertEqual(self.calls[-1][0][1],'is-active')
    def test_nonzero_query_still_stops(self):
        with self.assertRaises(ValueError):self.probe(exitcode=1)
        self.assertEqual([t for c,t in self.calls],[16,1,3,1,2])
        self.assertEqual(self.calls[-1][0][1],'is-active')
    def test_stop_timeout_still_checks_and_refuses(self):
        with self.assertRaises(subprocess.TimeoutExpired):self.probe(stop_error=subprocess.TimeoutExpired('stop',3))
        self.assertEqual(self.calls[-1][0][1],'is-active')
    def test_remaining_active_refuses(self):
        with self.assertRaises(ValueError):self.probe(active=True)
    def test_incomplete_type_set_refuses(self):
        with self.assertRaises(ValueError):self.probe(output=GOOD.replace('type=gyro','type=mag'))
    def test_unavailable_type_refuses(self):
        with self.assertRaises(ValueError):self.probe(output=GOOD.replace('available=yes','available=no',1))
    def test_cleanup_not_returned_refuses(self):
        with self.assertRaises(ValueError):self.probe(output=GOOD.replace('cleanup_returned=yes','cleanup_returned=no'))
    def test_oversized_probe_output_refuses(self):
        with self.assertRaises(ValueError):self.probe(output=GOOD+'x'*32768)

if __name__=='__main__':unittest.main()
