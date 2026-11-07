# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the installed helper lifecycle without accessing a phone."""
from contextlib import ExitStack
import importlib.util
import itertools
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('usb_lease_helper',
    ROOT / 'tools/oneplus13/ram-usb-role-check.py')
USB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(USB)
IDLE = dict(state=0, used=0, dirty_ap=0, dirty_switch=0)
ACTIVE = dict(state=1, used=1, dirty_ap=1, dirty_switch=1)
RESTORED = dict(state=2, used=1, dirty_ap=0, dirty_switch=0)
UNKNOWN = dict(state=3, used=1, dirty_ap=1, dirty_switch=1)
FLAGS = 'oneplus13.usb_host_probe=1 qcom_battmgr.oneplus13_otg_lease=1'


class FirmwareLeaseHelperTests(unittest.TestCase):
    def test_exact_numeric_receipt_only(self):
        run = Mock(return_value=Mock(returncode=0, stdout='state=1 used=1 dirty_ap=1 dirty_switch=1\n'))
        self.assertEqual(USB.firmware_lease_status(run), ACTIVE)
        self.assertEqual(run.call_args.kwargs['timeout'], 3)
        for value in ('', 'state=5 used=1 dirty_ap=1 dirty_switch=1',
                      'state=1 used=1 dirty_ap=1 dirty_switch=1\nextra',
                      'state=1 used=1 dirty_ap=1 dirty_switch=1\n\n',
                      'state=1 used=1 dirty_ap=2 dirty_switch=1'):
            run.return_value.stdout = value
            with self.assertRaises(RuntimeError): USB.firmware_lease_status(run)
        run.return_value.stdout = 'state=0 used=0 dirty_ap=0 dirty_switch=0'
        run.return_value.returncode = 1
        with self.assertRaises(RuntimeError): USB.firmware_lease_status(run)

    def test_fixed_commands_have_serial_transaction_budgets(self):
        run = Mock(return_value=Mock(returncode=0))
        for command, budget in [('start', 20), ('restore', 15)]:
            USB.firmware_lease_command(command, run)
            self.assertEqual(run.call_args.kwargs['timeout'], budget)
            self.assertEqual(run.call_args.args[0][-2:], [str(USB.FIRMWARE_LEASE), command])
        with self.assertRaises(ValueError): USB.firmware_lease_command('arbitrary', run)
        self.assertEqual(run.call_count, 2)

    def test_active_restore_requires_used_terminal_clean_receipt(self):
        with patch.object(USB, 'firmware_lease_status', side_effect=[ACTIVE, RESTORED]), \
             patch.object(USB, 'firmware_lease_command') as command:
            self.assertTrue(USB.firmware_lease_restore()['cleanup_complete'])
            command.assert_called_once_with('restore', USB.subprocess.run)

    def test_unknown_never_retries_and_idle_rebind_is_not_cleanup(self):
        for state in (UNKNOWN, IDLE, dict(RESTORED, used=0), dict(RESTORED, dirty_switch=1)):
            with patch.object(USB, 'firmware_lease_status', return_value=state), \
                 patch.object(USB, 'firmware_lease_command') as command:
                self.assertFalse(USB.firmware_lease_restore()['cleanup_complete'])
                command.assert_not_called()

    def test_refused_clean_but_receipt_failure_unknown(self):
        with patch.object(USB, 'firmware_lease_status', return_value=dict(RESTORED, state=4)):
            self.assertTrue(USB.firmware_lease_restore()['cleanup_complete'])
        with patch.object(USB, 'firmware_lease_status', side_effect=RuntimeError('bad receipt')), \
             patch.object(USB, 'firmware_lease_command') as command:
            with self.assertRaises(RuntimeError): USB.firmware_lease_restore()
            command.assert_not_called()

    def run_main(self, args, *, root=0, flags=FLAGS, command_error=None,
                 role_error=False, restore=RESTORED, storage=False, clock=None):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            base = Path(temporary)
            marker = base / 'ram-session'; marker.touch()
            mode = base / 'mode'; mode.write_text('device')
            state = base / 'result'
            facts = dict(udc=['a600000.usb'], usb_peripherals=[], usb_storage_bound=storage,
                         usb_hid_bound=False, host_controllers=[])
            for name, value in [('RAM_MARKER', marker), ('STATE', state)]:
                stack.enter_context(patch.object(USB, name, value))
            stack.enter_context(patch.object(USB.sys, 'argv', ['usb-role'] + args))
            stack.enter_context(patch.object(USB.os, 'geteuid', return_value=root))
            original_read = Path.read_text
            stack.enter_context(patch.object(Path, 'read_text',
                lambda path, *a, **kw: flags if str(path) == '/proc/cmdline' else original_read(path, *a, **kw)))
            stack.enter_context(patch.object(USB, 'mode_paths', return_value=mode))
            stack.enter_context(patch.object(USB, 'inventory', return_value=facts))
            stack.enter_context(patch.object(USB, 'typec_inventory', return_value={}))
            stack.enter_context(patch.object(USB, 'firmware_otg_inventory', return_value={}))
            stack.enter_context(patch.object(USB, 'kernel_diagnostics', return_value={}))
            wait = stack.enter_context(patch.object(USB, 'wait_for_host_driver', return_value=(facts, 1)))
            stack.enter_context(patch.object(USB.time, 'sleep'))
            stack.enter_context(patch.object(USB, 'usb_hid_input_present', return_value=False))
            stack.enter_context(patch.object(USB, 'firmware_lease_status', side_effect=[IDLE, ACTIVE]))
            command = stack.enter_context(patch.object(USB, 'firmware_lease_command', side_effect=command_error))
            cleanup = stack.enter_context(patch.object(USB, 'firmware_lease_restore',
                return_value=dict(status=restore, cleanup_complete=restore == RESTORED)))
            probe = stack.enter_context(patch.object(USB, 'storage_read_only_probe',
                return_value=dict(read_only_io_pass=True, cleanup_complete=True)))
            stack.enter_context(patch('builtins.print'))
            if clock is not None:
                stack.enter_context(patch.object(USB.time, 'monotonic', side_effect=clock))
            if role_error:
                original_write = USB.write
                def write(path, value):
                    if value == 'device': raise OSError('device unavailable')
                    original_write(path, value)
                stack.enter_context(patch.object(USB, 'write', side_effect=write))
            error = None
            try: USB.main()
            except (RuntimeError, OSError, SystemExit, subprocess.TimeoutExpired) as exc: error = exc
            receipt = json.loads((state/'result.json').read_text()) if (state/'result.json').exists() else None
            return receipt, error, command.call_args_list, cleanup.call_count, wait.call_args, probe.call_count

    def test_default_role_probe_never_starts_firmware_lease(self):
        receipt, error, commands, cleanups, wait, _ = self.run_main([])
        self.assertEqual(error.code, 0)
        self.assertTrue(receipt['cleanup_complete'])
        self.assertEqual((commands, cleanups), ([], 0))
        self.assertEqual(wait.args[0], 75)

    def test_root_flags_and_wait_gates_precede_mutation(self):
        for kwargs in ({'root': 1000}, {'flags': ''}, {'flags': 'oneplus13.usb_host_probe=1'}):
            receipt, error, commands, cleanups, _, _ = self.run_main(['--firmware-lease'], **kwargs)
            self.assertIsInstance(error, RuntimeError)
            self.assertIsNone(receipt)
            self.assertEqual((commands, cleanups), ([], 0))
        receipt, error, commands, cleanups, _, _ = self.run_main(['--firmware-lease', '--wait-seconds', '61'])
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual((commands, cleanups), ([], 0))

    def test_opt_in_default_wait_is_60_with_same_restore_reserve(self):
        receipt, error, commands, cleanups, wait, _ = self.run_main(['--firmware-lease'])
        self.assertEqual(error.code, 0)
        self.assertEqual(commands[0].args, ('start',))
        self.assertEqual(cleanups, 1)
        self.assertLessEqual(wait.args[0], 60)
        self.assertEqual(receipt['peripheral_wait_requested_seconds'], 60)
        self.assertEqual(USB.FIRMWARE_WORK_SECONDS, 80)
        self.assertFalse(receipt['physical_connection_time_verified'])
        self.assertEqual(receipt['peripheral_wait_outcome'], 'no_peripheral_observed_within_budget')
        self.assertTrue(receipt['cleanup_complete'])

    def test_start_failure_and_timeout_still_restore(self):
        for failure in (RuntimeError('start failed'), subprocess.TimeoutExpired('start', 20)):
            receipt, error, commands, cleanups, _, _ = self.run_main(
                ['--firmware-lease'], command_error=failure)
            self.assertIs(error, failure)
            self.assertTrue(receipt['firmware_lease_attempted'])
            self.assertEqual(cleanups, 1)
            self.assertTrue(receipt['role_restored'])

    def test_device_role_failure_still_restores_firmware(self):
        receipt, error, _, cleanups, _, _ = self.run_main(['--firmware-lease'], role_error=True)
        self.assertEqual(error.code, 1)
        self.assertEqual(cleanups, 1)
        self.assertFalse(receipt['cleanup_complete'])
        self.assertTrue(receipt['firmware_lease_restore']['cleanup_complete'])

    def test_unknown_firmware_cleanup_prevents_success(self):
        receipt, error, _, _, _, _ = self.run_main(['--firmware-lease'], restore=UNKNOWN)
        self.assertEqual(error.code, 1)
        self.assertFalse(receipt['cleanup_complete'])

    def test_storage_requires_32_seconds_before_restore_reserve(self):
        receipt, error, _, _, _, probes = self.run_main(['--firmware-lease'],
            storage=True, clock=itertools.chain([100, 100, 149, 150, 151, 152], itertools.repeat(153)).__next__)
        self.assertEqual(probes, 0)
        self.assertTrue(receipt['storage_skipped_lease_budget'])
        self.assertFalse(receipt['functional_pass'])

    def test_diagnostics_and_start_reduce_wait_without_extending_work(self):
        receipt, error, _, cleanups, wait, _ = self.run_main(['--firmware-lease'],
            clock=itertools.chain([100, 100, 140, 141, 142], itertools.repeat(143)).__next__)
        self.assertEqual(error.code, 0)
        self.assertEqual(wait.args[0], 32)
        self.assertEqual(receipt['peripheral_wait_budget_seconds'], 32)
        self.assertEqual(cleanups, 1)

    def test_cleanup_is_recorded_after_explicit_60_second_wait(self):
        receipt, error, commands, cleanups, wait, _ = self.run_main(
            ['--firmware-lease', '--wait-seconds', '60'])
        self.assertEqual(error.code, 0)
        self.assertLessEqual(wait.args[0], 60)
        self.assertEqual([c.args for c in commands], [('start',)])
        self.assertEqual(cleanups, 1)
        self.assertLessEqual(receipt['peripheral_wait_begin_uptime'], receipt['peripheral_wait_end_uptime'])
        self.assertLessEqual(receipt['firmware_restore_begin_uptime'], receipt['firmware_restore_end_uptime'])

    def test_lease_headers_bundled_and_targets_boot_opt_in(self):
        build = (ROOT/'tools/oneplus13/build.sh').read_text()
        for name in ('otg-lease.h', 'otg-kernel.h', 'otg-supply-lease.h'):
            self.assertIn('usb-draft/' + name, build)
            self.assertIn('drivers/power/supply/oneplus13-' + name, build)
        targets = (ROOT/'tools/oneplus13/build-targets-boot.py').read_text()
        self.assertIn('qcom_battmgr.oneplus13_otg_lease=1', targets)
        self.assertIn('"usb_firmware_lease_auto_started": False', targets)


if __name__ == '__main__': unittest.main()
