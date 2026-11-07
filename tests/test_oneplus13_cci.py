# SPDX-License-Identifier: GPL-2.0-only
"""Reject stale CCI source and artifact evidence before accepting a build."""
import hashlib
import importlib
import json
import difflib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/oneplus13'))
cci = importlib.import_module('check-cci')
verify = importlib.import_module('verify')


class CCIEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inventory = self.root / 'devices/oneplus13/kernel-patches'
        self.inventory.mkdir(parents=True)
        self.lock = json.loads((ROOT / 'devices/oneplus13/kernel-patches/cci-upstream-source.json').read_text())
        for name in ('cci-upstream-source.json', self.lock['patch_file']):
            (self.inventory / name).write_bytes((ROOT / 'devices/oneplus13/kernel-patches' / name).read_bytes())
        (self.inventory.parent / 'source.json').write_bytes((ROOT / 'devices/oneplus13/source.json').read_bytes())
        # Known synthetic payloads isolate validation from a huge kernel checkout.
        for name in self.lock['generated_files']:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(('fixture for ' + name).encode())
            self.lock['generated_files'][name] = hashlib.sha256(target.read_bytes()).hexdigest()
        self.save_lock()

    def save_lock(self):
        (self.inventory / 'cci-upstream-source.json').write_text(json.dumps(self.lock))

    def test_matching_source_inventory(self):
        self.assertEqual(cci.validate_source(self.root, self.root), self.lock)

    def test_git_patch_roundtrip_rejects_wrong_base_hash(self):
        patch_text = ''
        self.lock['base_files'] = {}
        for name in self.lock['generated_files']:
            before = 'old line\n'
            after = 'new line\n'
            (self.root / name).write_bytes(after.encode())
            self.lock['base_files'][name] = hashlib.sha256(before.encode()).hexdigest()
            self.lock['generated_files'][name] = hashlib.sha256(after.encode()).hexdigest()
            patch_text += ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                  fromfile='a/' + name, tofile='b/' + name))
        patch_file = self.inventory / self.lock['patch_file']
        patch_file.write_bytes(patch_text.encode())
        cci.validate_patch_roundtrip(self.root, self.lock, self.root)
        self.lock['base_files'][next(iter(self.lock['base_files']))] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'pinned base differs'):
            cci.validate_patch_roundtrip(self.root, self.lock, self.root)

    def test_modified_patch_rejected(self):
        with (self.inventory / self.lock['patch_file']).open('ab') as stream:
            stream.write(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'patch differs'):
            cci.validate_source(self.root, self.root)

    def test_each_generated_file_is_checked(self):
        for name in self.lock['generated_files']:
            with self.subTest(name=name):
                target = self.root / name
                original = target.read_bytes()
                target.write_bytes(original + b'corrupt')
                with self.assertRaisesRegex(ValueError, 'generated source differs'):
                    cci.validate_source(self.root, self.root)
                target.write_bytes(original)

    def test_kernel_pin_and_hardware_claim_rejected(self):
        for key, value in [('kernel_pin', '0' * 40), ('hardware_qualified', True)]:
            with self.subTest(key=key):
                original = self.lock[key]
                self.lock[key] = value
                self.save_lock()
                with self.assertRaises(ValueError):
                    cci.load_inventory(self.root)
                self.lock[key] = original
        self.save_lock()

    def test_unexpected_source_paths_rejected(self):
        self.lock['generated_files']['../escape.c'] = '0' * 64
        self.save_lock()
        with self.assertRaisesRegex(ValueError, 'unexpected source paths'):
            cci.load_inventory(self.root)

    def test_artifact_controls_and_binding(self):
        manifest = {'cci_clock_version':1, 'kernel_commit':self.lock['kernel_pin']}
        report = {'scope':'OFFLINE_ACTUAL_CCI_C_FUNCTIONS', 'hardware_tested':False,
                  'pass':True, 'sanitizers':True, 'pinned_base_patch_roundtrip':True,
                  'kernel_commit':manifest['kernel_commit'],
                  'patch_sha256':self.lock['patch_sha256'], 'generated_files':self.lock['generated_files'],
                  'sm8750_required_rate_hz':37500000, 'unknown_rates_rejected':True,
                  'legacy_19_2mhz_controls':True, 'mixed_master_modes_checked':True,
                  'opp_failure_propagated':True, 'clock_stretch_bit_checked':True}
        (self.root / 'cci-upstream-source.json').write_text(json.dumps(self.lock))
        output = self.root / 'cci-clock-verification.json'
        output.write_text(json.dumps(report))
        with patch.object(cci, 'load_inventory', return_value=self.lock):
            verify.check_cci_report(self.root, manifest)
            for key in report:
                with self.subTest(key=key):
                    invalid = dict(report)
                    invalid.pop(key)
                    output.write_text(json.dumps(invalid))
                    with self.assertRaises(ValueError):
                        verify.check_cci_report(self.root, manifest)
            output.write_text(json.dumps(report))
            (self.root / 'cci-upstream-source.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'provenance differs'):
                verify.check_cci_report(self.root, manifest)


if __name__ == '__main__':
    unittest.main()
