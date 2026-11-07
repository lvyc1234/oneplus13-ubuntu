"""Exercise the production verifier policy used by Actions image completion."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/oneplus13'))
spec = importlib.util.spec_from_file_location('usb_image_verifier', ROOT/'tools/oneplus13/verify-complete-image.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class UsbImagePolicyTests(unittest.TestCase):
    def setUp(self):
        self.base = dict(cmdline='console=tty0 oneplus13.session=base root=overlay',
                         session_identity_nonce='base')
        self.target = dict(cmdline='console=tty0 oneplus13.session=target root=overlay '
            'oneplus13.ir_probe=1 oneplus13.usb_host_probe=1 ' + verifier.USB_LEASE_FLAG
            + ' ' + verifier.USB_SUPPLY_READ_FLAG + ' ' + verifier.USB_SUPPLY_LEASE_FLAG,
            session_identity_nonce='target', usb_firmware_lease_available=True,
            usb_firmware_lease_auto_started=False, usb_supply_read_available=True,
            usb_supply_read_auto_started=False, usb_supply_setters_available=True,
            usb_supply_setters_auto_started=False)

    def test_manual_target_policy_accepts_generated_flags(self):
        verifier.verify_targets_boot_arguments(self.base, self.target)

    def test_missing_extra_duplicate_or_reordered_argument_rejected(self):
        original = self.target['cmdline']
        for changed in (original.replace(' '+verifier.USB_LEASE_FLAG, ''),
                        original+' '+verifier.USB_LEASE_FLAG, original+' unexpected=1',
                        original.replace('oneplus13.ir_probe=1 oneplus13.usb_host_probe=1',
                                         'oneplus13.usb_host_probe=1 oneplus13.ir_probe=1')):
            with self.subTest(cmdline=changed), self.assertRaises(ValueError):
                verifier.verify_targets_boot_arguments(self.base, {**self.target, 'cmdline':changed})

    def test_manual_policy_requires_exact_booleans(self):
        for key, values in [('usb_firmware_lease_available', (False, None, 1, 'true')),
                            ('usb_firmware_lease_auto_started', (True, None, 0, 'false')),
                            ('usb_supply_read_available', (False, None, 1, 'true')),
                            ('usb_supply_read_auto_started', (True, None, 0, 'false')),
                            ('usb_supply_setters_available', (False, None, 0, 'true')),
                            ('usb_supply_setters_auto_started', (True, None, 0, 'false'))]:
            for value in values:
                with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                    verifier.verify_targets_boot_arguments(self.base,{**self.target,key:value})

    def test_lease_in_parent_boot_rejected(self):
        key = verifier.USB_LEASE_FLAG.split('=', 1)[0]
        for suffix in ('', '=1', '=y', '=Y', '=true', '=0', '=n', '=unexpected'):
            token = key + suffix
            with self.subTest(token=token):
                with self.assertRaisesRegex(ValueError, 'leaked'):
                    verifier.reject_parent_usb_lease(self.base['cmdline']+' '+token)
                parent = {**self.base, 'cmdline':self.base['cmdline']+' '+token}
                target = {**self.target, 'cmdline':self.target['cmdline']+' '+token}
                with self.assertRaisesRegex(ValueError, 'leaked'):
                    verifier.verify_targets_boot_arguments(parent, target)

    def test_target_boolean_aliases_and_conflicting_duplicates_rejected(self):
        for suffix in ('y', 'Y', 'true', '0', 'false'):
            changed = self.target['cmdline'].replace(verifier.USB_LEASE_FLAG,
                verifier.USB_LEASE_FLAG.split('=', 1)[0]+'='+suffix)
            for cmdline in (changed, self.target['cmdline']+' '+changed.split()[-1]):
                with self.subTest(cmdline=cmdline), self.assertRaises(ValueError):
                    verifier.verify_targets_boot_arguments(self.base, {**self.target, 'cmdline':cmdline})

    def test_supply_read_requires_exact_target_optin_and_no_parent_alias(self):
        key = verifier.USB_SUPPLY_READ_FLAG.split('=', 1)[0]
        for suffix in ('', '=0', '=1', '=y', '=true', '=n'):
            token = key + suffix
            with self.subTest(token=token):
                with self.assertRaisesRegex(ValueError, 'leaked'):
                    verifier.reject_parent_usb_lease(self.base['cmdline'] + ' ' + token)
                changed = self.target['cmdline'].replace(verifier.USB_SUPPLY_READ_FLAG, token)
                if suffix != '=1':
                    with self.assertRaises(ValueError):
                        verifier.verify_targets_boot_arguments(self.base, {**self.target, 'cmdline':changed})
                with self.assertRaises(ValueError):
                    verifier.verify_targets_boot_arguments(self.base, {**self.target,
                        'cmdline':self.target['cmdline'] + ' ' + token})
        with self.assertRaises(ValueError):
            verifier.verify_targets_boot_arguments(self.base, {**self.target,
                'cmdline':self.target['cmdline'].replace(' ' + verifier.USB_SUPPLY_READ_FLAG, '')})

    def test_supply_lease_requires_exact_target_optin_and_no_parent_alias(self):
        key = verifier.USB_SUPPLY_LEASE_FLAG.split('=', 1)[0]
        for suffix in ('', '=0', '=1', '=y', '=true', '=n'):
            token = key + suffix
            with self.subTest(token=token):
                with self.assertRaisesRegex(ValueError, 'leaked'):
                    verifier.reject_parent_usb_lease(self.base['cmdline'] + ' ' + token)
                changed = self.target['cmdline'].replace(verifier.USB_SUPPLY_LEASE_FLAG, token)
                if suffix != '=1':
                    with self.assertRaises(ValueError):
                        verifier.verify_targets_boot_arguments(self.base, {**self.target, 'cmdline':changed})
                with self.assertRaises(ValueError):
                    verifier.verify_targets_boot_arguments(self.base, {**self.target,
                        'cmdline':self.target['cmdline'] + ' ' + token})
        with self.assertRaises(ValueError):
            verifier.verify_targets_boot_arguments(self.base, {**self.target,
                'cmdline':self.target['cmdline'].replace(' ' + verifier.USB_SUPPLY_LEASE_FLAG, '')})


if __name__ == '__main__': unittest.main()
