# SPDX-License-Identifier: GPL-2.0-only
"""Exercise filesystem ownership and failed-insertion cleanup with local fixtures."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import importlib.util
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('usb_filesystems', ROOT/'tools/oneplus13/usb-filesystem-modules.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux runtime fixture required')
class ModuleOwnershipTests(unittest.TestCase):
    def exercise(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            def path(value):
                return base/str(value).lstrip('/')
            root = path('/run/oneplus13/combined')
            root.mkdir(parents=True)
            path('/run/oneplus13/ram-session').touch()
            rule = path('/usr/lib/udev/rules.d/10-oneplus13-ram-storage.rules')
            rule.parent.mkdir(parents=True)
            rule.write_text(helper.RULE)
            release = helper.os.uname().release
            hashes = {}
            for name in helper.NAMES:
                relative = 'lib/modules/'+release+'/kernel/fs/'+name+'/'+name+'.ko'
                module = path('/'+relative)
                module.parent.mkdir(parents=True)
                module.write_bytes(name.encode())
                hashes[relative] = hashlib.sha256(name.encode()).hexdigest()
            if mode == 'bad-hash':
                hashes[next(iter(hashes))] = '0'*64
            (root/'config.private.json').write_text(json.dumps(dict(
                identity=dict(kernel_release=release), module_sha256=hashes)))
            if mode == 'preexisting':
                path('/sys/module/exfat').mkdir(parents=True)
            calls = []
            def run(argv, **kwargs):
                calls.append(argv[0])
                name = Path(argv[1]).stem if argv[0] == 'insmod' else argv[1]
                module = path('/sys/module/'+name)
                if argv[0] == 'insmod':
                    module.mkdir(parents=True)
                    if mode == 'timeout':
                        raise subprocess.TimeoutExpired(argv, 8)
                else:
                    if mode == 'busy':
                        return types.SimpleNamespace(returncode=1)
                    module.rmdir()
                return types.SimpleNamespace(returncode=0)
            with patch.object(helper, 'ROOT', root), patch.object(helper, 'STATE', root/'state.json'), \
                 patch.object(helper, 'Path', side_effect=path), \
                 patch.object(helper.os, 'geteuid', return_value=0), \
                 patch.object(helper.subprocess, 'run', side_effect=run):
                if mode in ('bad-hash', 'timeout'):
                    with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                        helper.load()
                else:
                    loaded = helper.load()
                    self.assertTrue(loaded['load_pass'])
                restored = helper.restore()
                self.assertEqual(restored['cleanup_complete'], mode != 'busy')
                if mode == 'preexisting':
                    self.assertTrue(path('/sys/module/exfat').is_dir())
                    self.assertEqual(restored['owned'], ['ntfs3'])
                if mode == 'bad-hash':
                    self.assertEqual(calls, [])
                if mode == 'timeout':
                    self.assertEqual(calls, ['insmod', 'rmmod'])
                    self.assertFalse(path('/sys/module/exfat').exists())

    def test_preexisting_module_is_preserved(self):
        self.exercise('preexisting')

    def test_timeout_after_insertion_is_still_cleaned_up(self):
        self.exercise('timeout')

    def test_bad_module_bytes_never_reach_insmod(self):
        self.exercise('bad-hash')

    def test_busy_module_cannot_pass_cleanup(self):
        self.exercise('busy')


if __name__ == '__main__':
    unittest.main()
