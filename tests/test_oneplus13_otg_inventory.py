# SPDX-License-Identifier: GPL-2.0-only
"""Offline checks of the actual optional read-only OTG inventory helper."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'oneplus13_otg_inventory_under_test',
    ROOT / 'tools/oneplus13/ram-usb-role-check.py',
)
USB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(USB)
NAMES = ('oneplus13_otg_ap_enable', 'oneplus13_otg_switch')


class OtgInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='op13-otg-inventory-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / 'bus/auxiliary/devices/pmic_glink.power-supply.0'
        self.base.mkdir(parents=True)

    def attributes(self, names=NAMES):
        for name in names:
            (self.base / name).write_text('fixture is not read directly')

    def inventory(self, run):
        with patch.object(USB, 'write', side_effect=AssertionError('write forbidden')):
            return USB.firmware_otg_inventory(self.root, run=run)

    def test_missing_attributes_are_unknown_without_spawning(self):
        run = Mock(side_effect=AssertionError('no attribute may be read'))
        self.assertEqual(self.inventory(run), {
            'read_only': True, 'values': {}, 'unavailable': list(NAMES),
            'complete': False, 'status': dict.fromkeys(NAMES, 'missing'),
            'interface': {'device_present': True, 'driver_bound': False},
        })
        run.assert_not_called()

    def test_two_boolean_values_are_read_once_with_fixed_argv_and_deadlines(self):
        self.attributes()
        run = Mock(side_effect=[
            subprocess.CompletedProcess([], 0, '0\n', ''),
            subprocess.CompletedProcess([], 0, '1\n', ''),
        ])
        self.assertEqual(self.inventory(run), {
            'read_only': True, 'values': dict(zip(NAMES, (0, 1))),
            'unavailable': [], 'complete': True, 'status': dict.fromkeys(NAMES, 'ok'),
            'interface': {'device_present': True, 'driver_bound': False},
        })
        self.assertEqual(run.call_count, 2)
        for call, name in zip(run.call_args_list, NAMES):
            self.assertEqual(call.args, (['cat', str(self.base / name)],))
            self.assertEqual(call.kwargs, {
                'capture_output': True, 'text': True, 'timeout': 3, 'check': False,
            })

    def test_only_present_attribute_is_read_and_partial_is_not_complete(self):
        self.attributes([NAMES[1]])
        run = Mock(return_value=subprocess.CompletedProcess([], 0, '0\n', ''))
        facts = self.inventory(run)
        self.assertEqual(facts['values'], {NAMES[1]: 0})
        self.assertEqual(facts['unavailable'], [NAMES[0]])
        self.assertFalse(facts['complete'])
        self.assertEqual(run.call_count, 1)

    def test_all_non_boolean_or_multi_value_outputs_are_unknown(self):
        self.attributes()
        for value in ('', '2', '-1', 'true', '01', '+1', '0\n1',
                      '1\nprivate', 'x' * 10000):
            with self.subTest(value=value[:16]):
                run = Mock(return_value=subprocess.CompletedProcess([], 0, value, ''))
                facts = self.inventory(run)
                self.assertEqual(facts['values'], {})
                self.assertEqual(facts['unavailable'], list(NAMES))
                self.assertFalse(facts['complete'])
                self.assertEqual(facts['status'], dict.fromkeys(NAMES, 'invalid_value'))

    def test_failed_command_with_boolean_stdout_is_not_success(self):
        self.attributes()
        run = Mock(return_value=subprocess.CompletedProcess([], 1, '1\n', 'private'))
        facts = self.inventory(run)
        self.assertEqual(facts['values'], {})
        self.assertEqual(facts['unavailable'], list(NAMES))
        self.assertNotIn('private', str(facts))
        self.assertEqual(facts['status'], dict.fromkeys(NAMES, 'read_error'))

    def test_timeout_does_not_claim_partial_stdout_and_continues_other_probe(self):
        self.attributes()
        run = Mock(side_effect=[
            subprocess.TimeoutExpired(['cat'], 3, output='1\n', stderr='private'),
            subprocess.CompletedProcess([], 0, '0\n', ''),
        ])
        facts = self.inventory(run)
        self.assertEqual(facts['values'], {NAMES[1]: 0})
        self.assertEqual(facts['unavailable'], [NAMES[0]])
        self.assertFalse(facts['complete'])
        self.assertEqual(run.call_count, 2)
        self.assertNotIn('private', str(facts))
        self.assertEqual(facts['status'], {NAMES[0]: 'timeout', NAMES[1]: 'ok'})

    def test_oserror_is_unknown_and_does_not_prevent_second_probe(self):
        self.attributes()
        run = Mock(side_effect=[OSError('private'),
                               subprocess.CompletedProcess([], 0, '1\n', '')])
        facts = self.inventory(run)
        self.assertEqual(facts['values'], {NAMES[1]: 1})
        self.assertEqual(facts['unavailable'], [NAMES[0]])
        self.assertFalse(facts['complete'])
        self.assertEqual(run.call_count, 2)
        self.assertNotIn('private', str(facts))
        self.assertEqual(facts['status'], {NAMES[0]: 'read_error', NAMES[1]: 'ok'})

    def test_absent_auxiliary_device_is_distinct_from_missing_attributes(self):
        self.base.rmdir()
        facts = self.inventory(Mock(side_effect=AssertionError('no reads')))
        self.assertFalse(facts['interface']['device_present'])
        self.assertEqual(facts['status'], dict.fromkeys(NAMES, 'missing'))

    def test_directory_is_not_an_attribute_and_never_read(self):
        for name in NAMES:
            (self.base / name).mkdir()
        facts = self.inventory(Mock(side_effect=AssertionError('no reads')))
        self.assertEqual(facts['status'], dict.fromkeys(NAMES, 'not_regular'))

    def test_stat_errors_are_distinct_and_do_not_leak_exception_text(self):
        with patch.object(Path, 'stat', side_effect=PermissionError('private')):
            facts = self.inventory(Mock(side_effect=AssertionError('no reads')))
        self.assertEqual(facts['status'], dict.fromkeys(NAMES, 'stat_error'))
        self.assertNotIn('private', str(facts))

    def test_unrelated_interface_and_extra_attributes_are_never_read(self):
        self.attributes()
        (self.base / 'oneplus13_otg_set').write_text('private')
        wrong = self.base.parent / 'pmic_glink.power-supply.1'
        wrong.mkdir()
        (wrong / NAMES[0]).write_text('private')
        run = Mock(return_value=subprocess.CompletedProcess([], 0, '1\n', ''))
        facts = self.inventory(run)
        self.assertTrue(facts['complete'])
        self.assertEqual(run.call_count, 2)
        self.assertEqual([call.args[0][1] for call in run.call_args_list],
                         [str(self.base / name) for name in NAMES])


if __name__ == '__main__':
    unittest.main()
