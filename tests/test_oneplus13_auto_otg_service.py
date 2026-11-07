# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the actual desktop controller without sysfs writes or hardware."""
import errno
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('auto_otg',ROOT/'tools/oneplus13/auto-otg.py')
auto=importlib.util.module_from_spec(spec);spec.loader.exec_module(auto)


class MachineIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.identity('Qualcomm Technologies, Inc. Sun MTP,dodge', ['qcom,sun'])

    def identity(self, model, compatible, chassis='handset'):
        for name, value in [('model', model), ('compatible', '\0'.join(compatible)),
                            ('chassis-type', chassis)]:
            (self.root/name).write_bytes(value.encode() + b'\0')

    def test_abl_rewritten_phone_is_recognized(self):
        self.assertTrue(auto.oneplus13_machine(self.root))

    def test_original_phone_requires_both_compatibles(self):
        model = 'OnePlus 13 (AA569 native display experimental candidate)'
        self.identity(model, ['oneplus,dodge', 'qcom,sm8750'])
        self.assertTrue(auto.oneplus13_machine(self.root))
        for compatible in [['oneplus,dodge'], ['qcom,sm8750']]:
            self.identity(model, compatible)
            self.assertFalse(auto.oneplus13_machine(self.root))

    def test_generic_sun_and_other_phones_are_rejected(self):
        for model in ['Qualcomm Technologies, Inc. Sun MTP',
                      'Qualcomm Technologies, Inc. Sun MTP,other', 'OnePlus 13R']:
            self.identity(model, ['qcom,sun'])
            self.assertFalse(auto.oneplus13_machine(self.root))

    def test_tablet_and_incomplete_identity_are_rejected(self):
        self.identity('Qualcomm Technologies, Inc. Sun MTP,dodge', ['qcom,sun'], 'tablet')
        self.assertFalse(auto.oneplus13_machine(self.root))
        (self.root/'model').unlink()
        self.assertFalse(auto.oneplus13_machine(self.root))

    def test_substring_compatible_does_not_match(self):
        self.identity('Qualcomm Technologies, Inc. Sun MTP,dodge', ['qcom,sun-other'])
        self.assertFalse(auto.oneplus13_machine(self.root))

    def test_non_root_backend_is_rejected_before_device_access(self):
        with patch.object(auto.os, 'geteuid', return_value=1000), \
                patch.object(auto, 'oneplus13_machine') as gate:
            with self.assertRaisesRegex(RuntimeError, 'Exact OnePlus 13'):
                auto.Backend()
            gate.assert_not_called()


class Fake:
    def __init__(self):
        self.value=dict.fromkeys(auto.FIELDS,0)
        self.value.update(healthy=1,generation=1,source=1)
        self.calls=[]
        self.physical=dict(host_source=False,host_controller_ready=False)
        self.fail_on=False
        self.samples=None
    def status(self):return dict(self.value)
    def observe(self):
        return dict(self.samples.pop(0) if self.samples else self.physical)
    def command(self,action):
        self.calls.append(action)
        if action=='arm':self.value.update(state=5,dirty_ap=1,dirty_switch=1)
        elif action.startswith('on '):
            if self.fail_on:
                self.value['generation']+=1
                raise OSError(errno.EINVAL,'event changed')
            self.value.update(state=1,dirty_current=1,dirty_vbus=1)
        elif action in ('off','close'):
            self.value.update(state=2,dirty_ap=0,dirty_switch=0,dirty_current=0,dirty_vbus=0)


class DesktopControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend=Fake();self.now=0
        self.control=auto.Controller(self.backend,clock=lambda:self.now)
    def test_firmware_event_alone_never_enables_supply(self):
        self.control.step();self.assertEqual(self.backend.calls,['arm'])
    def test_host_without_source_or_xhci_never_enables_supply(self):
        self.backend.physical=dict(host_source=True,host_controller_ready=False)
        self.control.step();self.assertEqual(self.backend.calls,['arm'])
    def test_actual_host_source_and_controller_start_once(self):
        self.backend.physical=dict(host_source=True,host_controller_ready=True)
        self.control.step();self.control.step()
        self.assertEqual(self.backend.calls,['arm','on 1'])
    def test_active_supply_survives_controller_rebuild_with_valid_source(self):
        self.backend.physical=dict(host_source=True,host_controller_ready=True)
        self.control.step()
        self.backend.physical['host_controller_ready']=False
        self.control.step()
        self.backend.physical['host_controller_ready']=True
        self.control.step()
        self.assertEqual(self.backend.calls,['arm','on 1'])
        self.assertEqual(self.backend.value['state'],1)
    def test_real_role_loss_still_turns_off_during_controller_rebuild(self):
        self.backend.physical=dict(host_source=True,host_controller_ready=True)
        self.control.step()
        self.backend.physical=dict(host_source=False,host_controller_ready=False)
        self.control.step()
        self.assertEqual(self.backend.calls[-1],'off')
    def test_firmware_source_loss_still_turns_off_with_host_role_present(self):
        self.backend.physical=dict(host_source=True,host_controller_ready=True)
        self.control.step();self.backend.value['source']=0
        self.backend.physical['host_controller_ready']=False
        self.control.step()
        self.assertEqual(self.backend.calls[-1],'off')
    def test_second_physical_sample_catches_disconnect(self):
        self.backend.samples=[dict(host_source=True,host_controller_ready=True),
                              dict(host_source=False,host_controller_ready=False)]
        self.control.step();self.assertEqual(self.backend.calls,['arm'])
    def test_unplug_restores_and_next_insert_is_a_new_cycle(self):
        self.backend.physical=dict(host_source=True,host_controller_ready=True)
        self.control.step();self.backend.physical['host_source']=False
        self.control.step();self.assertEqual(self.backend.calls[-1],'off')
        self.backend.physical['host_source']=True;self.control.step()
        self.assertEqual(self.backend.calls,['arm','on 1','off','arm','on 1'])
    def test_heartbeat_is_not_a_power_enable_retry(self):
        self.control.step();self.now=2;self.control.step()
        self.assertEqual(self.backend.calls,['arm'])
        self.now=3;self.control.step();self.assertEqual(self.backend.calls,['arm','keepalive'])
    def test_generation_change_during_start_does_not_claim_success(self):
        self.backend.physical=dict(host_source=True,host_controller_ready=True)
        self.backend.fail_on=True;state=self.control.step()
        self.assertEqual(state['on_count'],0);self.assertEqual(state['kernel']['state'],5)
    def test_poisoned_service_never_rearms(self):
        self.backend.value.update(state=3,healthy=0)
        with self.assertRaises(RuntimeError):self.control.step()
        self.assertEqual(self.backend.calls,[])
    def test_dirty_restored_receipt_cannot_start_a_new_cycle(self):
        self.backend.value.update(state=2,dirty_current=1)
        with self.assertRaises(RuntimeError):self.control.step()
        self.assertEqual(self.backend.calls,[])
    def test_close_requires_explicit_clean_readback(self):
        self.control.step();self.assertTrue(self.control.close()['cleanup_proven'])
        self.assertEqual(self.backend.calls[-1],'close')
    def test_duplicate_and_unknown_kernel_receipts_are_rejected(self):
        raw=' '.join(k+'='+str(v) for k,v in self.backend.value.items())+'\n'
        self.assertEqual(auto.parse_status(raw),self.backend.value)
        for changed in [raw+'state=0',raw.replace('healthy=1','healthy=2'),raw.replace('state=0','state=6')]:
            with self.assertRaises(RuntimeError):auto.parse_status(changed)


class BootPolicyTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0,str(ROOT/'tools/oneplus13'))
        import importlib
        self.verifier=importlib.import_module('verify-complete-image')
        self.parent=dict(cmdline='oneplus13.desktop=1 oneplus13.session=old',session_identity_nonce='old')
        self.manifest=dict(scope='AUTOMATIC_OTG_DESKTOP_RAM_CANDIDATE',
            session_identity_nonce='fresh',automatic_usb_service_enabled=True,
            automatic_usb_hardware_qualified=False,automatic_usb_watchdog_seconds=15,
            automatic_usb_heartbeat_seconds=3,usb_firmware_lease_available=False,
            usb_supply_setters_available=False,
            cmdline='oneplus13.desktop=1 oneplus13.session=fresh oneplus13.usb_host_probe=1 qcom_battmgr.oneplus13_otg_auto=1 oneplus13.first_user_audio=1')
    def test_exact_automatic_profile_is_allowed(self):
        self.verifier.verify_automatic_boot_arguments(self.parent,self.manifest)
    def test_automatic_owner_cannot_leak_to_unqualified_parent(self):
        with self.assertRaises(ValueError):
            self.verifier.reject_parent_usb_lease(self.manifest['cmdline'])
    def test_manual_owner_cannot_coexist_in_automatic_profile(self):
        self.manifest['cmdline']+=' qcom_battmgr.oneplus13_otg_supply_lease=1'
        with self.assertRaises(ValueError):
            self.verifier.verify_automatic_boot_arguments(self.parent,self.manifest)
    def test_manifest_cannot_claim_hardware_acceptance(self):
        self.manifest['automatic_usb_hardware_qualified']=True
        with self.assertRaises(ValueError):
            self.verifier.verify_automatic_boot_arguments(self.parent,self.manifest)

    def test_automatic_usb_mount_policy_is_late_and_readonly(self):
        rule=ROOT/'devices/oneplus13/desktop/99-oneplus13-auto-usb-readonly.rules'
        self.assertTrue(rule.is_file())
        self.assertTrue(rule.name.startswith('99-'))
        text=rule.read_text()
        self.assertIn('ENV{ID_FS_USAGE}=="filesystem"',text)
        self.assertIn('UDISKS_MOUNT_OPTIONS_DEFAULTS}="ro"',text)
        self.assertIn('TEST=="/run/oneplus13-auto-otg/desktop-mode"',text)


if __name__=='__main__':unittest.main()
