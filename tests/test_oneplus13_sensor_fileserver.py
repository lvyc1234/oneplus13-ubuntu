# SPDX-License-Identifier: GPL-2.0-only
import copy,hashlib,importlib.util,io,json,os,stat,struct,tarfile,tempfile,unittest
import contextlib
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-sensor-fileserver.py'
spec=importlib.util.spec_from_file_location('phone_sensor_fileserver',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

TRACE='''100 ioctl(0x3, 0x5208, 0) = 0 <0.001>
100 ioctl(0x3, 0xc0105203, 0xffff) = 0 <0.001>
100 ioctl(0x3, 0xc0105203, 0xffff) = 0 <0.001>
100 ioctl(0x3, 0xc0105203, 0xffff) = 0 <0.001>
100 openat(AT_FDCWD, "/usr/share/erhai-sensors/hexagonrpcd/sensors/registry", O_RDONLY|O_DIRECTORY) = 4 <0.001>
100 openat(AT_FDCWD, "/usr/share/erhai-sensors/hexagonrpcd/sensors/sns_reg.conf", O_RDONLY) = 5 <0.001>
'''

class Archive(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        elf=bytearray(64);elf[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',elf,18,183)
        self.entries={m.PREFIX+n:(bytes(elf),0o755,None) for n in ['bin/hexagonrpcd','bin/ssccli','libexec/iio-sensor-proxy','lib/hex.so','lib/ssc.so']}
        for name in ['usr/share/erhai-sensors/hexagonrpcd/sensors/sns_reg.conf','var/lib/erhai/sensors/persist/registry/sample']:
            self.entries[name]=(b'phone-data',0o640,None)
        self.entries.update({n:(None,0o777,t) for n,t in m.LINKS.items()})
        self.manifest={'scope':'PRIVATE_PHONE_SENSOR_FILESERVER_INPUTS','links':m.LINKS,
            'files':{n:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest(),'mode':mode} for n,(v,mode,t) in self.entries.items() if v is not None}}
    def archive(self,entries=None):
        path=self.root/'runtime.tar.gz'
        with tarfile.open(path,'w:gz') as tf:
            for n,(value,mode,target) in (entries or self.entries).items():
                member=tarfile.TarInfo(n);member.mode=mode
                if target is None:member.size=len(value);tf.addfile(member,io.BytesIO(value))
                else:member.type=tarfile.SYMTYPE;member.linkname=target;tf.addfile(member)
        return path
    def test_exact_elf_data_and_three_mapping_links(self):
        self.assertEqual(m.archive_entries(self.archive(),self.manifest),self.entries)
    def test_changed_private_byte_rejected(self):
        changed=dict(self.entries);changed['var/lib/erhai/sensors/persist/registry/sample']=(b'other-data',0o640,None)
        with self.assertRaises(ValueError):m.archive_entries(self.archive(changed),self.manifest)
    def test_hook_outside_allowlist_rejected(self):
        changed=dict(self.entries);changed['etc/systemd/system/automatic.service']=(b'start',0o644,None)
        with self.assertRaises(ValueError):m.archive_entries(self.archive(changed),self.manifest)
    def test_wrong_mapping_rejected(self):
        changed=dict(self.entries);changed[next(iter(m.LINKS))]=(None,0o777,'/persist/registry')
        with self.assertRaises(ValueError):m.archive_entries(self.archive(changed),self.manifest)
    def test_nonarm64_elf_rejected(self):
        key=m.PREFIX+'bin/ssccli';data=bytearray(self.entries[key][0]);struct.pack_into('<H',data,18,62)
        changed=dict(self.entries);changed[key]=(bytes(data),0o755,None)
        mf=copy.deepcopy(self.manifest);mf['files'][key]['sha256']=hashlib.sha256(data).hexdigest()
        with self.assertRaises(ValueError):m.archive_entries(self.archive(changed),mf)
    def test_executable_registry_rejected(self):
        key='var/lib/erhai/sensors/persist/registry/sample';changed=dict(self.entries);changed[key]=(b'phone-data',0o755,None)
        mf=copy.deepcopy(self.manifest);mf['files'][key]['mode']=0o755
        with self.assertRaises(ValueError):m.archive_entries(self.archive(changed),mf)
    def test_traversal_rejected(self):
        for name in ['../etc/file','/etc/file','usr/../etc/file','usr//lib/file','usr\\lib\\file']:
            with self.subTest(name=name),self.assertRaises(ValueError):m.canonical(name)
    @unittest.skipIf(os.name=='nt','Install uses target Linux symlink semantics')
    def test_install_private_data_and_preserve_bytes_modes(self):
        target=self.root/'target';target.mkdir();ownership=[]
        m.install(target,self.entries,lambda *a:ownership.append(a))
        for n,(value,mode,t) in self.entries.items():
            if value is not None:
                self.assertEqual((target/n).read_bytes(),value)
                self.assertEqual(stat.S_IMODE((target/n).stat().st_mode),mode)
            else:self.assertEqual(os.readlink(target/n),t)
        self.assertEqual(len(ownership),2)
    @unittest.skipIf(os.name=='nt','Install uses target Linux symlink semantics')
    def test_preexisting_parent_link_refused_before_any_write(self):
        target=self.root/'target';target.mkdir();outside=self.root/'outside';outside.mkdir()
        (target/'usr').symlink_to(outside)
        with self.assertRaises(ValueError):m.install(target,self.entries,lambda *a:None)
        self.assertEqual(list(outside.iterdir()),[])
    def test_existing_destination_is_not_overwritten(self):
        target=self.root/'target';file=target/m.PREFIX/'bin/hexagonrpcd';file.parent.mkdir(parents=True);file.write_bytes(b'existing')
        with self.assertRaises(ValueError):m.install(target,self.entries,lambda *a:None)
        self.assertEqual(file.read_bytes(),b'existing')
    @unittest.skipIf(os.name=='nt','Target POSIX umask and permission semantics')
    def test_umask077_still_allows_new_ancestors_to_be_traversed(self):
        target=self.root/'target';target.mkdir()
        previous=os.umask(0o077)
        try:m.install(target,self.entries,lambda *a:None)
        finally:os.umask(previous)
        for name in self.entries:
            for parent in (target/name).parents:
                if parent==target:break
                self.assertTrue(parent.stat().st_mode&stat.S_IXOTH,str(parent))
    @unittest.skipIf(os.name=='nt','Target POSIX umask and permission semantics')
    def test_existing_inaccessible_ancestor_rejected_not_relaxed(self):
        target=self.root/'target';directory=target/'usr/lib/erhai'
        directory.mkdir(parents=True);directory.chmod(0o700)
        with self.assertRaises(ValueError):m.install(target,self.entries,lambda *a:None)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o700)

class Trace(unittest.TestCase):
    def test_current_mode_refusal_is_counted_without_private_path(self):
        result=m.daemon_error_summary('Tried to open /private/unit/data in an unsupported mode')
        self.assertEqual(result['write_refusals'],1)
        self.assertNotIn('/private/unit/data',json.dumps(result))
    def test_error_summary_preserves_refusals_without_private_paths(self):
        private='/private/unit/unique-data'
        result=m.daemon_error_summary('Could not open '+private+': No such file or directory\nTried to open '+private+' for writing\nRefused request on handle 1\n')
        self.assertEqual(result['error_lines'],2)
        self.assertEqual(result['write_refusals'],1)
        self.assertEqual(result['missing_file_lookups'],1)
        self.assertEqual(len(result['missing_file_lookup_sha256']),1)
        self.assertNotEqual(result['missing_file_lookup_sha256'][0],private)
        self.assertEqual(result['unsupported_or_refused_dispatches'],1)
        self.assertNotIn(private,json.dumps(result))
    def test_success_evidence_keeps_remote_release_unclaimed(self):
        result=m.trace_facts(TRACE)
        self.assertEqual(result['attach_calls'],1)
        self.assertEqual(result['successful_invoke_calls_observed'],3)
        self.assertTrue(result['registry_mapping_open_observed'])
        self.assertFalse(result['raw_rpc_arguments_or_samples_saved'])
    def test_failed_attach_cannot_pass(self):
        with self.assertRaises(ValueError):m.trace_facts(TRACE.replace('0x5208, 0) = 0','0x5208, 0) = -1 EPERM'))
    def test_second_attach_cannot_pass(self):
        with self.assertRaises(ValueError):m.trace_facts(TRACE+TRACE.splitlines()[0]+'\n')
    def test_failed_file_open_is_not_a_mapping_pass(self):
        result=m.trace_facts(TRACE.replace('O_RDONLY|O_DIRECTORY) = 4','O_RDONLY|O_DIRECTORY) = -1 ENOENT'))
        self.assertFalse(result['registry_mapping_open_observed'])
    def test_original_builder_trailing_slash_registry_mapping(self):
        self.assertTrue(m.trace_facts(TRACE.replace('/sensors/registry"','/sensors/registry/"'))['registry_mapping_open_observed'])
    def test_neighbor_registry_name_is_not_the_required_mapping(self):
        self.assertFalse(m.trace_facts(TRACE.replace('/sensors/registry"','/sensors/registry-other"'))['registry_mapping_open_observed'])

class BoundedService(unittest.TestCase):
    def test_service_has_bounds_and_no_client_or_dynamic_pd(self):
        cmd=m.service_command('/run/trace');joined=' '.join(cmd)
        for value in ['RuntimeMaxSec=40s','TimeoutStopSec=12s','Restart=no','ProtectSystem=strict','PrivateNetwork=yes','User=65534','DevicePolicy=closed']:
            self.assertIn('--property='+value,cmd)
        self.assertNotIn('ssccli',joined);self.assertNotIn('iio-sensor-proxy',joined)
        self.assertNotIn('-p',cmd);self.assertNotIn('-c',cmd)
        self.assertIn('-W',cmd)
        self.assertIn('raw=ioctl',cmd)
    def run_service(self,active='active',result='success',log='',trace=TRACE,diagnostic=False,show_error=False,launch_timeout=False,hook=None,hook_advance=0):
        calls=[];now=[0];stopped=[False]
        self.last_calls=calls
        def run(args,seconds=8):
            calls.append((args,seconds))
            if args[0].endswith('systemd-run'):
                if launch_timeout:raise m.subprocess.TimeoutExpired(args,8)
                return ''
            if args[1]=='is-active':
                if isinstance(active,Exception):raise active
                return active
            if args[1]=='stop':stopped[0]=True;return ''
            if args[1]=='show':
                if stopped[0]:return 'ActiveState=inactive\nSubState=dead\nMainPID=0\nResult='+result
                if show_error:raise m.subprocess.CalledProcessError(1,args)
                return 'ActiveState=active\nSubState=running\nMainPID=123\nResult=success'
            raise AssertionError(args)
        def sleep(value):now[0]+=value
        self.hook_calls=0
        def during_active():
            self.hook_calls+=1
            hook()
            now[0]+=hook_advance
        def contents(path,*a,**k):return trace if str(path).endswith('syscalls.log') else log
        with patch.object(m.Path,'read_text',contents):
            answer=m.serve('/run/trace',run,sleep,lambda:now[0],diagnostic=diagnostic,
                           during_active=during_active if hook is not None else None)
        return answer,calls
    def test_success_stops_once_before_acceptance(self):
        answer,calls=self.run_service()
        self.assertTrue(answer['file_server_cleanly_stopped'])
        self.assertFalse(answer['dsp_release_acknowledgement_observed'])
        self.assertEqual(sum(a[1]=='stop' for a,s in calls if len(a)>1),1)
        self.assertIn(16,[s for a,s in calls if a[1]=='stop'])
    def test_early_exit_requests_stop_and_refuses_pass(self):
        with self.assertRaises(ValueError):self.run_service(active='inactive')
    def test_nonzero_active_query_still_stops_and_preserves_diagnostic(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaises(ValueError):
            self.run_service(active=m.subprocess.CalledProcessError(3,['systemctl']),diagnostic=True)
        self.assertIn('RAM_SENSOR_FILESERVER_DIAGNOSTIC ',output.getvalue())
        self.assertEqual(sum(a[1]=='stop' for a,s in self.last_calls if len(a)>1),1)
    def test_failed_pre_stop_query_cannot_skip_stop(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaises(ValueError):
            self.run_service(show_error=True,diagnostic=True)
        self.assertIn('"service_status_query_failed": true',output.getvalue())
        self.assertEqual(sum(a[1]=='stop' for a,s in self.last_calls if len(a)>1),1)
    def test_launch_timeout_still_requests_exactly_one_stop(self):
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(ValueError):
            self.run_service(launch_timeout=True,diagnostic=True)
        self.assertEqual(sum(a[1]=='stop' for a,s in self.last_calls if len(a)>1),1)
    def test_systemd_run_early_error_still_preserves_diagnostic(self):
        output=io.StringIO();stopped=[False]
        def run(args,seconds=8):
            if args[0].endswith('systemd-run'):
                raise m.subprocess.CalledProcessError(1,args)
            if args[1]=='is-active':return 'inactive'
            if args[1]=='stop':stopped[0]=True;return ''
            if args[1]=='show':
                if stopped[0]:return 'ActiveState=inactive\nSubState=dead\nMainPID=0\nResult=success'
                return 'ActiveState=failed\nSubState=failed\nMainPID=0\nResult=exit-code'
            raise AssertionError(args)
        with patch.object(m.Path,'read_text',lambda path,*a,**k:TRACE if str(path).endswith('syscalls.log') else 'Large input'):
            with contextlib.redirect_stdout(output),self.assertRaises(ValueError):
                m.serve('/run/trace',run,lambda value:None,lambda:0,diagnostic=True)
        line=next(x for x in output.getvalue().splitlines() if x.startswith('RAM_SENSOR_FILESERVER_DIAGNOSTIC '))
        facts=json.loads(line.split(' ',1)[1])
        self.assertTrue(facts['service_launch_returned_error'])
        self.assertEqual(facts['pre_stop_service_state']['ActiveState'],'failed')
        self.assertTrue(facts['file_server_cleanly_stopped'])
    def test_kernel_close_timeout_refuses_pass(self):
        with self.assertRaises(ValueError):self.run_service(result='timeout')
    def test_unsupported_reverse_method_refuses_pass(self):
        with self.assertRaises(ValueError):self.run_service(log='Refused request on handle 1')
    def test_write_refusal_cannot_silently_pass(self):
        with self.assertRaises(ValueError):self.run_service(log='Tried to open /private/registry/DIR for writing')
    def test_diagnostic_allows_only_classified_missing_files(self):
        answer,_=self.run_service(log='Could not open optional.so: No such file or directory',diagnostic=True)
        self.assertFalse(answer['file_server_accepted'])
        self.assertEqual(answer['write_refusals'],0)
        self.assertEqual(answer['diagnostic_missing_file_lookups'],1)
    def test_diagnostic_still_refuses_write_mode(self):
        with self.assertRaises(ValueError):
            self.run_service(log='Tried to open /private/registry/DIR for writing',diagnostic=True)
    def test_diagnostic_refuses_current_unsupported_mode_message(self):
        with self.assertRaises(ValueError):
            self.run_service(log='Tried to open /private/registry/DIR in an unsupported mode',diagnostic=True)
    def test_diagnostic_rejects_unclassified_error(self):
        with self.assertRaises(ValueError):
            self.run_service(log='Could not start unknown internal subsystem',diagnostic=True)
    def test_complete_trace_diagnostic_precedes_missing_lookup_failure(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaises(ValueError):
            self.run_service(log='Could not open optional.so: No such file or directory')
        line=next(x for x in output.getvalue().splitlines() if x.startswith('RAM_SENSOR_FILESERVER_DIAGNOSTIC '))
        facts=json.loads(line.split(' ',1)[1])
        self.assertTrue(facts['complete_local_trace_facts']['attach_ioctl_trace_success'])
        self.assertEqual(facts['daemon_errors']['missing_file_lookups'],1)
        self.assertTrue(facts['acceptance_not_implied'])
    def test_partial_trace_is_evidence_failure_not_acceptance(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaises(ValueError):self.run_service(trace=TRACE.splitlines()[-1])
        facts=json.loads(output.getvalue().split(' ',1)[1])
        self.assertIsNone(facts['complete_local_trace_facts'])
        self.assertIsNotNone(facts['trace_qualification_error'])
    def test_reviewed_callback_runs_once_while_active(self):
        answer,_=self.run_service(hook=lambda:None,hook_advance=4)
        self.assertEqual(self.hook_calls,1);self.assertTrue(answer['file_server_cleanly_stopped'])
    def test_callback_failure_still_stops_file_service(self):
        def failed():raise ValueError('synthetic metadata refusal')
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaisesRegex(ValueError,'synthetic metadata refusal'):
            self.run_service(hook=failed)
        self.assertEqual(sum(a[1]=='stop' for a,s in self.last_calls if len(a)>1),1)
        line=next(x for x in output.getvalue().splitlines() if x.startswith('RAM_SENSOR_FILESERVER_DIAGNOSTIC '))
        self.assertTrue(json.loads(line.split(' ',1)[1])['file_server_cleanly_stopped'])
    def test_callback_overrun_stops_and_refuses_pass(self):
        with self.assertRaises(ValueError):self.run_service(hook=lambda:None,hook_advance=29)
        self.assertEqual(sum(a[1]=='stop' for a,s in self.last_calls if len(a)>1),1)
    def test_inactive_service_cannot_start_callback(self):
        with self.assertRaises(ValueError):self.run_service(active='inactive',hook=lambda:None)
        self.assertEqual(self.hook_calls,0)

if __name__=='__main__':unittest.main()
