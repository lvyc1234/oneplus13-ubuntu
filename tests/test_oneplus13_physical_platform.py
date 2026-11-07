# SPDX-License-Identifier: GPL-2.0-only
"""Run the real shell helper against compiled and ABL-rewritten OF fixtures."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO/'devices/oneplus13/desktop/physical-platform'
BASH = os.environ.get('TEST_BASH') or shutil.which('bash')


def shell_path(path):
    text = Path(path).resolve().as_posix()
    if len(text) > 2 and text[1] == ':':
        return '/' + text[0].lower() + text[2:]
    return text


class PhonePlatformIdentityTests(unittest.TestCase):
    def check(self, accepted=False, model='Qualcomm Technologies, Inc. Sun MTP,dodge',
              chassis='handset', board_compatible='qcom,sun-mtp\0qcom,sun',
              hyper_compatible='qcom,gunyah-hypervisor-1.0\0qcom,gunyah-hypervisor\0simple-bus',
              vmid=3, bound=False, omit_model=False):
        fixtures = REPO/'test-work'
        fixtures.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=fixtures) as directory:
            root = Path(directory)
            base = root/'sys/firmware/devicetree/base'
            vm = base/'hypervisor/qcom,gunyah-vm'
            vm.mkdir(parents=True)
            if not omit_model:
                (base/'model').write_bytes(model.encode()+b'\0')
            (base/'compatible').write_bytes(board_compatible.encode()+b'\0')
            (base/'chassis-type').write_bytes(chassis.encode()+b'\0')
            (base/'hypervisor/compatible').write_bytes(hyper_compatible.encode()+b'\0')
            (vm/'qcom,vmid').write_bytes(vmid.to_bytes(4,'big'))
            if bound:
                (root/'sys/bus/platform/devices/hypervisor/driver').mkdir(parents=True)
            commands = root/'commands'
            commands.mkdir()
            marker = root/'mount-called'
            (commands/'mount').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > '+shlex.quote(shell_path(marker))+'\n',newline='\n')
            (commands/'mount').chmod(0o755)
            command = ('PATH='+shlex.quote(shell_path(commands))+':"$PATH" sh '+
                       shlex.quote(shell_path(HELPER))+' '+shlex.quote(shell_path(root/'sys')))
            result = subprocess.run([BASH,'--noprofile','--norc','-c',command],capture_output=True,text=True)
            if accepted:
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertTrue(marker.exists())
                self.assertIn('ro,nosuid,nodev,noexec,size=4k,mode=0555',marker.read_text())
                self.assertIn('ONEPLUS13_PHYSICAL_PLATFORM_EXPORT_CORRECTED',result.stdout)
            else:
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertFalse(marker.exists(),'rejected platform reached mount')

    def test_observed_phone_identity(self):
        self.check(accepted=True)

    def test_compiled_phone_identity(self):
        self.check(accepted=True,model='OnePlus 13 (offline bring-up candidate)',
                   board_compatible='oneplus,dodge\0qcom,sm8750')

    def test_generic_sun_mtp_is_rejected(self):
        self.check(model='Qualcomm Technologies, Inc. Sun MTP')

    def test_compiled_native_phone_identity(self):
        self.check(accepted=True,model='OnePlus 13 (AA569 native display experimental candidate)',
                   board_compatible='oneplus,dodge\0qcom,sm8750')

    def test_compiled_phone_requires_soc_identity(self):
        self.check(model='OnePlus 13 (offline bring-up candidate)',board_compatible='oneplus,dodge')

    def test_pad_is_rejected(self):
        self.check(model='OnePlus Pad 2 Pro',chassis='tablet')

    def test_other_phone_is_rejected(self):
        self.check(model='OnePlus 13R')

    def test_dodge_substring_is_insufficient(self):
        self.check(model='Qualcomm Technologies, Inc. Sun MTP,dodge-other')

    def test_handset_is_required(self):
        self.check(chassis='tablet')

    def test_matching_model_on_wrong_soc_is_rejected(self):
        self.check(board_compatible='qcom,other')

    def test_compiled_model_with_wrong_compatible_is_rejected(self):
        self.check(model='OnePlus 13 (offline bring-up candidate)')

    def test_secondary_guest_is_rejected(self):
        self.check(vmid=4)

    def test_real_kvm_guest_is_rejected(self):
        self.check(hyper_compatible='linux,kvm')

    def test_bound_hypervisor_is_rejected(self):
        self.check(bound=True)

    def test_missing_identity_is_rejected(self):
        self.check(omit_model=True)


if __name__ == '__main__':
    unittest.main()
