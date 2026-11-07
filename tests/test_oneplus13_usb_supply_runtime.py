# SPDX-License-Identifier: GPL-2.0-only
"""Actual staged USB runtime fault controls; no phone, mount or firmware traffic."""
import importlib.util
from pathlib import Path
import stat
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'tools/oneplus13'/filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
supply = load('usb_supply_runtime', 'ram-usb-supply-check.py')
evidence = load('native_usb_evidence', 'ram-usb-native-host-check.py')

@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux runtime fixtures required')
class SupplyRuntimeTests(unittest.TestCase):

    def test_require_armed_refuses_expired_boottime_and_other_boot(self):
        clock = Mock()
        boot = Mock()
        boot.read_text.return_value = 'this-boot\n'
        for elapsed, boot_id in ((106, 'this-boot'), (20, 'other-boot')):
            clock.read_text.return_value = '{"boot_id":"' + boot_id + '","arm_begin_boottime":10}'
            with patch.object(supply, 'ARM_CLOCK', clock), patch.object(supply, 'BOOT_ID', boot), patch.object(supply.time, 'clock_gettime', return_value=10 + elapsed), patch.object(supply, 'lease_status', return_value=dict(state=5)):
                with self.assertRaisesRegex(RuntimeError, 'eligibility expired'):
                    supply.require_armed()

    def test_idle_restore_receipt_does_not_prove_cleanup(self):
        usb = types.SimpleNamespace(mode_paths=lambda: 'mode', write=Mock(), read=lambda p: 'device')
        status = dict(state=0, used=0, saved_ap=0, saved_switch=0, saved_vbus=0, saved_current=0, dirty_ap=0, dirty_switch=0, dirty_vbus=0, dirty_current=0)
        with patch.object(supply, 'load_usb', return_value=usb), patch.object(supply, 'command'), patch.object(supply, 'lease_status', return_value=status), patch.object(supply, 'property_values', return_value={n: 0 for n in supply.NAMES}):
            self.assertFalse(supply.restore_receipt()['cleanup_complete'])

    def host_probe(self, *, attached=True, expired=False):
        clock = [0.0]
        active = [False]
        usb = types.SimpleNamespace(inventory=lambda: {'ready': True}, typec_inventory=lambda: {'attached': attached}, typec_host_source=lambda t: t['attached'], mode_paths=lambda: 'mode', write=Mock(), read=lambda p: 'host', reconcile_host=Mock(), host_controller_ready=lambda f: f['ready'])
        start = Mock(side_effect=lambda stage: active.__setitem__(0, True))
        values = dict(zip(supply.NAMES, (1, 1, 1, 1250)))
        native = types.SimpleNamespace(block_candidates=lambda u: [])
        armed = Mock(side_effect=RuntimeError('expired') if expired else None)

        def sleep(seconds):
            clock[0] += seconds
        with patch.object(supply, 'load_usb', return_value=usb), patch.object(supply, 'load_native', return_value=native), patch.object(supply, 'lease_status', side_effect=lambda: dict(state=1 if active[0] else 5)), patch.object(supply, 'require_armed', armed), patch.object(supply, 'command', start), patch.object(supply, 'property_values', return_value=values), patch.object(supply.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(supply.time, 'sleep', side_effect=sleep):
            try:
                result = supply.host_receipt()
            except RuntimeError as error:
                result = error
        return (result, start, usb, clock[0])

    def test_ready_xhci_does_not_wait_for_unpowered_storage_driver(self):
        result, start, usb, elapsed = self.host_probe()
        self.assertEqual(result['stage'], 'host')
        self.assertEqual(usb.reconcile_host.call_count, 1)
        self.assertEqual(elapsed, 30)
        start.assert_called_once_with('start')

    def test_no_host_source_means_no_start(self):
        result, start, usb, elapsed = self.host_probe(attached=False)
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(elapsed, 90)
        start.assert_not_called()
        usb.write.assert_not_called()

    def test_expired_arm_means_no_role_write_or_start(self):
        result, start, usb, _ = self.host_probe(expired=True)
        self.assertIsInstance(result, RuntimeError)
        start.assert_not_called()
        usb.write.assert_not_called()

    def pinned(self, *, failed_unmount=False, changed_identity=False):
        expected = dict(device='/dev/sdz1', major_minor='8:1')
        mounted = [False]
        calls = []
        native = types.SimpleNamespace(unique_object=evidence.unique_object, block_candidates=lambda u: [] if changed_identity and len(calls) >= 2 else [expected])

        def run(argv, **kwargs):
            calls.append(argv[0])
            if argv[0] == 'blkid':
                return types.SimpleNamespace(returncode=0, stdout='vfat\n')
            if argv[0] == 'mount':
                self.assertIn('--no-canonicalize', argv)
                self.assertIn('/proc/self/fd/42', argv)
                self.assertEqual(kwargs['pass_fds'], (42,))
                mounted[0] = True
                return types.SimpleNamespace(returncode=0, stdout='')
            if argv[0] == 'umount':
                if failed_unmount:
                    raise subprocess.TimeoutExpired(argv, 12)
                mounted[0] = False
                return types.SimpleNamespace(returncode=0, stdout='')
            return types.SimpleNamespace(returncode=0, stdout='{"read_bytes":32}')
        row = ['1', '0', '8:1', '/', '/run/mock', 'ro,nosuid,nodev,noexec', '-']
        with patch.object(supply.os, 'open', return_value=42), patch.object(supply.os, 'fstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=supply.os.makedev(8, 1))), patch.object(supply.os, 'close') as close, patch.object(supply.os, 'rmdir'), patch.object(supply.tempfile, 'mkdtemp', return_value='/run/mock'), patch.object(supply, 'mount_entry', side_effect=lambda p: row if mounted[0] else None), patch.object(supply.subprocess, 'run', side_effect=run):
            result = supply.pinned_storage_probe(Mock(), native, expected)
            close.assert_called_once_with(42)
            return result

    def test_pinned_mount_and_nonempty_prefix_pass(self):
        result = self.pinned()
        self.assertTrue(result['read_only_io_pass'])
        self.assertTrue(result['cleanup_complete'])

    def test_pinned_unmount_timeout_never_passes(self):
        result = self.pinned(failed_unmount=True)
        self.assertFalse(result['read_only_io_pass'])
        self.assertFalse(result['cleanup_complete'])

    def test_mount_identity_change_invalidates_file_proof(self):
        result = self.pinned(changed_identity=True)
        self.assertFalse(result['read_only_io_pass'])
        self.assertTrue(result['cleanup_complete'])

    def terminal(self):
        return dict(state=2, used=1, saved_ap=0, saved_switch=0, saved_vbus=0, saved_current=0, dirty_ap=0, dirty_switch=0, dirty_vbus=0, dirty_current=0)

    def test_firmware_restore_does_not_switch_role_before_cable_return(self):
        usb = types.SimpleNamespace(write=Mock())
        with patch.object(supply, 'load_usb', return_value=usb), patch.object(supply, 'command'), patch.object(supply, 'lease_status', return_value=self.terminal()), patch.object(supply, 'property_values', return_value={n: 0 for n in supply.NAMES}):
            result = supply.restore_receipt()
        self.assertTrue(result['cleanup_complete'])
        self.assertFalse(result['role_restored'])
        usb.write.assert_not_called()

    def role(self, *, rebound=False, attached_device=True):
        usb = types.SimpleNamespace(mode_paths=lambda: 'mode', write=Mock(), read=lambda p: 'device', inventory=lambda: dict(udc=['a600000.usb']), typec_inventory=lambda: dict(ports=[dict(partner_present=True, data_role='[device] host' if attached_device else '[host] device', power_role='[sink] source' if attached_device else '[source] sink')]))
        after = self.terminal() if not rebound else dict(self.terminal(), state=0, used=0)
        with patch.object(supply, 'load_usb', return_value=usb), patch.object(supply, 'lease_status', side_effect=lambda: after if attached_device and rebound and usb.write.called else self.terminal()), patch.object(supply, 'property_values', return_value={n: 0 for n in supply.NAMES}):
            clock = [0.0]
            with patch.object(supply.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(supply.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
                return supply.role_receipt()

    def test_role_requires_device_sink_udc_and_same_terminal_lease(self):
        self.assertTrue(self.role()['cleanup_complete'])
        self.assertFalse(self.role(rebound=True)['cleanup_complete'])
        with self.assertRaisesRegex(RuntimeError, 'Actual device/sink'):
            self.role(attached_device=False)

if __name__ == '__main__':
    unittest.main()
