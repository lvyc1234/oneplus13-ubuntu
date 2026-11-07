# SPDX-License-Identifier: GPL-2.0-only
import hashlib,importlib.util,io,json,subprocess,tarfile,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace

path=Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-ssc-metadata.py'
spec=importlib.util.spec_from_file_location('ssc_metadata',path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
GOOD='\n'.join('SSC_PHONE_METADATA type='+t+' available=yes completed=yes' for t in sorted(m.TYPES))+'\nSSC_PHONE_METADATA_RESULT complete=yes samples_requested=no cleanup_returned=yes\n'
VERIFY='SSC_PHONE_METADATA_LOCK_VERIFIED_NO_QRTR\nok 6 /phone/06-one-way-lock\nPHONE_METADATA_SEND_GUARD_BLOCKED_ALL_UNREVIEWED_REQUESTS_BEFORE_QMI\n'

class ControlTests(unittest.TestCase):
    def test_discovery_stages_distinguish_empty_lookup_and_unavailable_attributes(self):
        text=('SSC_PHONE_LOOKUP type=ambient_light default=1 count=0\n'
              'SSC_PHONE_LOOKUP type=ambient_light default=0 count=2\n'
              'SSC_PHONE_ATTRIBUTES type=ambient_light candidate=1 available=0 matrix=0\n'
              'SSC_PHONE_ATTRIBUTES type=ambient_light candidate=2 available=1 matrix=0\n'
              'private UID and calibration text\n')
        facts=m.discovery_stages(text)
        self.assertEqual([r['uid_count'] for r in facts['lookups']],[0,2])
        self.assertEqual([r['available'] for r in facts['attributes']],[False,True])
        self.assertNotIn('private',json.dumps(facts))
        self.assertEqual(m.discovery_stages('unrecognized private output')['lookups'],[])
        with self.assertRaises(ValueError):m.discovery_stages(text*9)
    def test_partial_sensor_inventory_is_explicit_and_raw_text_is_withheld(self):
        output=GOOD.replace('type=ambient_light available=yes','type=ambient_light available=no')+'private device text\n'
        facts=m.partial_inventory(output)
        self.assertEqual(facts['availability'],'observed')
        self.assertFalse(facts['types']['ambient_light']['available'])
        self.assertTrue(facts['types']['accel']['available'])
        self.assertFalse(facts['all_types_accepted'])
        self.assertNotIn('private',json.dumps(facts))
        self.assertNotEqual(m.partial_inventory(GOOD.replace('type=gyro','type=mag'))['availability'],'observed')

    def runner(self,output=GOOD,fail=None,active=False):
        self.calls=[]
        def run(command,**kw):
            self.calls.append(command)
            if command[0].endswith('systemd-run'):
                if fail:raise fail
                return SimpleNamespace(returncode=0,stdout=output,stderr='')
            if command[1]=='stop':return SimpleNamespace(returncode=0,stdout='',stderr='')
            if command[1]=='reset-failed':return SimpleNamespace(returncode=0,stdout='',stderr='')
            return SimpleNamespace(returncode=0 if active else 3,stdout='active' if active else 'inactive')
        return run
    def test_discovery_requires_all_types_and_cleanup(self):
        facts=m.run_probe('discover',run=self.runner())
        self.assertEqual(facts['metadata_available'],sorted(m.TYPES));self.assertTrue(facts['unit_inactive'])
        self.assertEqual([c[1] for c in self.calls[1:]],['stop','is-active'])
    def test_discovery_timeout_still_stops(self):
        with self.assertRaises(subprocess.TimeoutExpired):m.run_probe('discover',run=self.runner(fail=subprocess.TimeoutExpired('probe',26)))
        self.assertEqual([c[1] for c in self.calls[1:]],['stop','reset-failed','is-active'])
    def test_failed_launch_still_stops(self):
        with self.assertRaises(OSError):m.run_probe('discover',run=self.runner(fail=OSError('synthetic launch failure')))
        self.assertEqual([c[1] for c in self.calls[1:]],['stop','reset-failed','is-active'])
    def test_unit_still_active_refused(self):
        with self.assertRaises(ValueError):m.run_probe('discover',run=self.runner(active=True))
    def test_missing_or_duplicate_type_refused(self):
        for output in (GOOD.replace('type=gyro','type=mag'),GOOD.replace('available=yes','available=no',1),GOOD.replace('cleanup_returned=yes','cleanup_returned=no')):
            with self.subTest(output=output),self.assertRaises(ValueError):m.interpret('discover',output)
    def test_verify_is_network_isolated(self):
        _,command=m.command('verify');self.assertIn('--property=PrivateNetwork=yes',command)
        self.assertIn('--property=RestrictAddressFamilies=AF_UNIX',command)
        self.assertNotIn('--property=PrivateNetwork=no',command)
        self.assertEqual(m.run_probe('verify',run=self.runner(VERIFY))['native_guard_checks_passed'],3)
    def test_discover_has_only_qrtr_and_unix_families(self):
        _,command=m.command('discover')
        self.assertIn('--property=RestrictAddressFamilies=AF_UNIX AF_QIPCRTR',command)
        self.assertIn('--property=RuntimeMaxSec=20s',command);self.assertIn('--property=KillMode=control-group',command)
        self.assertIn('--property=PrivateDevices=yes',command);self.assertIn('--property=DevicePolicy=closed',command)
        self.assertIn('--setenv=LD_LIBRARY_PATH='+str(m.STATE/'probe'),command)
    def test_oversized_output_refused(self):
        with self.assertRaises(ValueError):m.interpret('discover',GOOD+'x'*32768)
    def test_nonzero_exit_rejected_after_stop(self):
        run=self.runner()
        def failed(cmd,**kw):
            result=run(cmd,**kw)
            if cmd[0].endswith('systemd-run'):result.returncode=1
            return result
        with self.assertRaises(ValueError):m.run_probe('discover',run=failed)
        self.assertEqual(self.calls[-1][1],'is-active')
    def test_failed_discovery_returns_only_classified_reasons(self):
        output='SSC_PHONE_METADATA_DIAGNOSTIC type=all reason=discovery_timeout\n'+GOOD.replace('available=yes','available=no')
        run=self.runner(output)
        def failed(cmd,**kw):
            result=run(cmd,**kw)
            if cmd[0].endswith('systemd-run'):result.returncode=255
            return result
        with self.assertRaisesRegex(ValueError,'discovery_timeout'):
            m.run_probe('discover',run=failed)
        self.assertEqual(self.calls[-1][1],'is-active')
    def test_unclassified_discovery_output_stays_withheld(self):
        run=self.runner('private text must not be returned\n')
        def failed(cmd,**kw):
            result=run(cmd,**kw)
            if cmd[0].endswith('systemd-run'):result.returncode=255
            return result
        with self.assertRaises(ValueError) as caught:m.run_probe('discover',run=failed)
        self.assertNotIn('private text',str(caught.exception))
        self.assertIn('"probe": {}',str(caught.exception))
    def test_diagnostic_reasons_are_exact_public_categories(self):
        for reason in m.REASONS:
            self.assertEqual(m.diagnostics('SSC_PHONE_METADATA_DIAGNOSTIC type=gyro reason='+reason),{'gyro':reason})
        for text in ('type=gyro reason=private_identifier','type=other reason=g_io_error',
                     'type=gyro reason=g_io_error\nSSC_PHONE_METADATA_DIAGNOSTIC type=gyro reason=cancelled'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                m.diagnostics('SSC_PHONE_METADATA_DIAGNOSTIC '+text)
    def test_failed_exec_status_is_queried_before_cleanup(self):
        run=self.runner('raw stderr must stay private')
        status='LoadState=loaded\nActiveState=failed\nSubState=failed\nResult=exit-code\nExecMainCode=1\nExecMainStatus=203\n'
        def failed(cmd,**kw):
            result=run(cmd,**kw)
            if cmd[0].endswith('systemd-run'):result.returncode=255
            elif cmd[1]=='show':result.returncode=0;result.stdout=status
            return result
        with self.assertRaises(ValueError) as caught:m.run_probe('discover',run=failed)
        self.assertIn('"ExecMainStatus": 203',str(caught.exception))
        self.assertNotIn('raw stderr',str(caught.exception))
        self.assertEqual([c[1] for c in self.calls[1:]],['show','stop','reset-failed','is-active'])
    def test_private_status_values_are_not_echoed(self):
        def run(cmd,**kw):return SimpleNamespace(returncode=0,stdout='Result=private_value\n')
        self.assertEqual(m.execution_status('synthetic.service',run),{'availability':'unrecognized_status'})
    def test_absent_unit_cannot_report_observed_success(self):
        output='LoadState=not-found\nActiveState=inactive\nSubState=dead\nResult=success\nExecMainCode=0\nExecMainStatus=0\n'
        def run(cmd,**kw):return SimpleNamespace(returncode=0,stdout=output)
        self.assertEqual(m.execution_status('synthetic.service',run),{'availability':'unit_unavailable'})
    def test_failure_status_timeout_still_performs_cleanup(self):
        run=self.runner()
        def failed(cmd,**kw):
            if cmd[1]=='show':self.calls.append(cmd);raise subprocess.TimeoutExpired('show',2)
            result=run(cmd,**kw)
            if cmd[0].endswith('systemd-run'):result.returncode=255
            return result
        with self.assertRaisesRegex(ValueError,'query_failed'):m.run_probe('discover',run=failed)
        self.assertEqual(self.calls[-1][1],'is-active')
    def test_transient_reset_timeout_still_checks_and_refuses(self):
        run=self.runner()
        def failed(cmd,**kw):
            if cmd[1]=='reset-failed':self.calls.append(cmd);raise subprocess.TimeoutExpired('reset-failed',1)
            result=run(cmd,**kw)
            if cmd[0].endswith('systemd-run'):result.returncode=255
            return result
        with self.assertRaises(subprocess.TimeoutExpired):m.run_probe('discover',run=failed)
        self.assertEqual(self.calls[-1][1],'is-active')
    @unittest.skipUnless(__import__('os').name=='posix' and __import__('os').geteuid()==0,'actual root-owned POSIX permissions')
    def test_private_failure_capture_is_exclusive_bounded_and_not_echoed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);root.chmod(0o755)
            result=SimpleNamespace(stdout='private_value'*5000,stderr='private stderr')
            facts=m.save_private_failure(root,result)
            self.assertEqual((root/'failure-private').stat().st_mode&0o777,0o700)
            self.assertEqual((root/'failure-private/stdout').stat().st_mode&0o777,0o600)
            self.assertEqual((root/'failure-private/stdout').stat().st_size,32768)
            self.assertTrue(facts['files']['stdout']['truncated'])
            self.assertNotIn('private_value',json.dumps(facts))
            with self.assertRaises(FileExistsError):m.save_private_failure(root,result)
    def test_stop_timeout_still_queries_state_and_refuses(self):
        run=self.runner()
        def stop_timeout(cmd,**kw):
            if len(cmd)>1 and cmd[1]=='stop':
                self.calls.append(cmd);raise subprocess.TimeoutExpired('stop',5)
            return run(cmd,**kw)
        with self.assertRaises(subprocess.TimeoutExpired):m.run_probe('discover',run=stop_timeout)
        self.assertEqual(self.calls[-1][1],'is-active')

class ArchiveTests(unittest.TestCase):
    def fixture(self,root,symlink=False):
        manifest={'scope':'PUBLIC_PHONE_SSC_METADATA_ONLY_PROBE','files':{},'one_way_send_guard':True,
          'samples_or_streams_allowed':False,'private_inputs_included':False,'hardware_qualified':False,
          'runtime_libraries':{n:{} for n in m.RUNTIME_LIBRARIES}}
        archive=root/'synthetic.tar.gz'
        with tarfile.open(archive,'w:gz') as tf:
            for n in sorted(m.NAMES):
                data=bytearray(32);data[:6]=b'\x7fELF\x02\x01';data[18:20]=(183).to_bytes(2,'little')
                info=tarfile.TarInfo(n);info.size=len(data);info.mode=0o755
                if symlink:info.type=tarfile.SYMTYPE;info.linkname='/tmp/never-follow'
                tf.addfile(info,io.BytesIO(data))
                manifest['files'][n]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':0o755}
        manifest['archive_sha256']=m.sha(archive);return archive,manifest
    def test_exact_synthetic_inventory(self):
        with tempfile.TemporaryDirectory() as d:
            archive,manifest=self.fixture(Path(d));self.assertEqual(set(m.entries(archive,manifest)),m.NAMES)
    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            archive,manifest=self.fixture(Path(d),True)
            with self.assertRaises(ValueError):m.entries(archive,manifest)
    def test_changed_capability_or_hash_refused(self):
        with tempfile.TemporaryDirectory() as d:
            archive,manifest=self.fixture(Path(d));manifest['samples_or_streams_allowed']=True
            with self.assertRaises(ValueError):m.entries(archive,manifest)
            manifest['samples_or_streams_allowed']=False;manifest['archive_sha256']='0'*64
            with self.assertRaises(ValueError):m.entries(archive,manifest)

if __name__=='__main__':unittest.main()

