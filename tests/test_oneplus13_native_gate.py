# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the actual Python entry point's identity guards on synthetic roots."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import runpy
import test_oneplus13_native_desktop as fixtures

REPO = Path(__file__).resolve().parents[1]
BASH = os.environ.get('TEST_BASH') or shutil.which('bash')


def shell_path(path):
    value = Path(path).resolve().as_posix()
    return '/'+value[0].lower()+value[2:] if len(value)>2 and value[1]==':' else value


class NativeGateTests(unittest.TestCase):
    def test_identity_matrix_runs_actual_python_entry(self):
        helper = runpy.run_path(str(REPO/'devices/oneplus13/start-native-display'))
        cases = json.loads((REPO/'devices/oneplus13/test-fixtures/native-identity.json').read_text())
        for case in cases:
            with self.subTest(case=case['name']), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                fixtures.NativeDesktopTests().fixture(root)
                base=root/'sys/firmware/devicetree/base'
                for key in ['model','chassis']:
                    target=base/('chassis-type' if key=='chassis' else key)
                    if case[key] is not None:
                        target.write_bytes(case[key].encode()+b'\0')
                    else:target.unlink(missing_ok=True)
                (base/'compatible').write_bytes(('\0'.join(case['compatible'])+'\0').encode())
                accepted=True
                try:helper['validate_files'](root,'test-release')
                except (ValueError,OSError):accepted=False
                self.assertEqual(accepted,case['accept'])

    def test_native_desktop_rejected_before_reading_any_inputs(self):
        run = subprocess.run([sys.executable,str(REPO/'tools/oneplus13/package-desktop.py'),
            '--stock','missing','--artifacts','missing','--desktop','missing',
            '--ufdt-tool','missing','--output','missing','--native-xml','missing'],
            capture_output=True,text=True)
        self.assertEqual(run.returncode,2)
        self.assertIn('Native desktop packaging is disabled',run.stderr)
        self.assertNotIn('Traceback',run.stderr)
