# SPDX-License-Identifier: GPL-2.0-only
"""Execute the data-only handoff library against isolated host fixtures."""
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[1]
BASH=os.environ.get('TEST_BASH') or shutil.which('bash')
LIB=REPO/'devices/oneplus13/desktop/native-handoff'
PROFILE='lib/firmware/qcom/sm8750/oneplus/dodge/aa569-spr-v1.bin'

def shell_path(path):
    value=Path(path).resolve().as_posix()
    return '/'+value[0].lower()+value[2:] if len(value)>2 and value[1]==':' else value

@unittest.skipUnless(BASH,'A POSIX shell is required')
class NativeHandoffTests(unittest.TestCase):
    def fixture(self,base):
        src,dst=base/'source',base/'target'
        src.mkdir();dst.mkdir()
        payload={PROFILE:bytes(176),'sbin/start-native-display':b'#!/bin/sh\nexit 99\n',
                 'etc/oneplus13-native-candidate':b'test-release\n'}
        payload['etc/oneplus13-native-handoff']=('ONEPLUS13_NATIVE_HANDOFF_V1\ntest-release\n'+
            hashlib.sha256(payload[PROFILE]).hexdigest()+'\n'+
            hashlib.sha256(payload['sbin/start-native-display']).hexdigest()+'\n').encode()
        for name,data in payload.items():
            (src/name).parent.mkdir(parents=True,exist_ok=True)
            (src/name).write_bytes(data)
        return src,dst,payload

    def run_handoff(self,src,dst,release='test-release',reuse_installed='0'):
        command='. '+shlex.quote(shell_path(LIB))+'\nstage_native_payload '+ ' '.join(
            shlex.quote(x) for x in [shell_path(src),shell_path(dst),release,reuse_installed])
        return subprocess.run([BASH,'--noprofile','--norc','-c',command],capture_output=True,text=True)

    def test_complete_payload_preserved_as_data(self):
        with tempfile.TemporaryDirectory() as temp:
            src,dst,files=self.fixture(Path(temp))
            run=self.run_handoff(src,dst)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertIn('STAGED_NOT_ACTIVATED',run.stdout)
            for name,target in [(PROFILE,'usr/'+PROFILE),
                ('sbin/start-native-display','run/oneplus13/native/start-native-display'),
                ('etc/oneplus13-native-candidate','etc/oneplus13-native-candidate'),
                ('etc/oneplus13-native-handoff','etc/oneplus13-native-handoff')]:
                self.assertEqual((dst/target).read_bytes(),files[name])
            self.assertFalse((dst/'sbin/start-native-display').exists())

    def test_absent_payload_is_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source';dst=root/'target';src.mkdir();dst.mkdir()
            run=self.run_handoff(src,dst)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('HANDOFF_ABSENT',run.stdout)
            self.assertEqual(list(dst.iterdir()),[])

    def test_incomplete_old_or_corrupted_payload_rejected_before_copy(self):
        for fault in ['missing-marker','missing-helper','profile-byte','helper-byte','release',
                      'marker-release','marker-version','extra-line','short-profile','bad-digest']:
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as temp:
                src,dst,files=self.fixture(Path(temp))
                marker=src/'etc/oneplus13-native-handoff'
                if fault=='missing-marker':marker.unlink()
                elif fault=='missing-helper':(src/'sbin/start-native-display').unlink()
                elif fault=='profile-byte':(src/PROFILE).write_bytes(b'1'+bytes(175))
                elif fault=='helper-byte':(src/'sbin/start-native-display').write_bytes(b'changed')
                elif fault=='release':(src/'etc/oneplus13-native-candidate').write_text('other\n')
                elif fault=='marker-release':marker.write_bytes(marker.read_bytes().replace(b'test-release',b'other-release'))
                elif fault=='marker-version':marker.write_bytes(marker.read_bytes().replace(b'_V1',b'_V2'))
                elif fault=='extra-line':marker.write_bytes(marker.read_bytes()+b'extra\n')
                elif fault=='short-profile':(src/PROFILE).write_bytes(bytes(175))
                else:marker.write_bytes(marker.read_bytes().replace(hashlib.sha256(files[PROFILE]).hexdigest().encode(),b'g'*64))
                run=self.run_handoff(src,dst)
                self.assertNotEqual(run.returncode,0,run.stdout)
                self.assertEqual(list(dst.iterdir()),[])

    def test_existing_destination_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            src,dst,_=self.fixture(Path(temp))
            path=dst/'usr'/PROFILE;path.parent.mkdir(parents=True);path.write_bytes(b'keep')
            self.assertNotEqual(self.run_handoff(src,dst).returncode,0)
            self.assertEqual(path.read_bytes(),b'keep')

    def test_persistent_boot_reuses_only_matching_installed_files(self):
        with tempfile.TemporaryDirectory() as temp:
            src,dst,files=self.fixture(Path(temp))
            first=self.run_handoff(src,dst,reuse_installed='1')
            self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            native=dst/'run/oneplus13/native'
            shutil.rmtree(native)
            second=self.run_handoff(src,dst,reuse_installed='1')
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertEqual((native/'start-native-display').read_bytes(),files['sbin/start-native-display'])
            shutil.rmtree(native)
            path=dst/'usr'/PROFILE
            path.write_bytes(b'keep')
            self.assertNotEqual(self.run_handoff(src,dst,reuse_installed='1').returncode,0)
            self.assertEqual(path.read_bytes(),b'keep')
            self.assertFalse(native.exists())

    def test_copy_error_is_fatal_even_in_conditional_caller(self):
        with tempfile.TemporaryDirectory() as temp:
            src,dst,_=self.fixture(Path(temp))
            command=('. '+shlex.quote(shell_path(LIB))+ '\ncp() { return 42; }\n'+
                'if stage_native_payload '+ ' '.join(shlex.quote(x) for x in
                [shell_path(src),shell_path(dst),'test-release'])+'; then exit 99; else exit 0; fi')
            run=subprocess.run([BASH,'--noprofile','--norc','-c',command],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertNotIn('STAGED_NOT_ACTIVATED',run.stdout)
