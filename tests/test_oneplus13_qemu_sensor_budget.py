# SPDX-License-Identifier: GPL-2.0-only
import importlib.util,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/oneplus13'))
spec=importlib.util.spec_from_file_location('exact_rootfs_vm',ROOT/'tools/oneplus13/qemu-exact-rootfs.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Budget(unittest.TestCase):
    def test_changes_only_explicit_qemu_http_fixture(self):
        fixture=(ROOT/'devices/oneplus13/desktop/qemu-install').read_bytes()
        changed=m.reserve_emulator_time(fixture)
        self.assertEqual(changed.replace(b'deadline=300 ;; esac',b'deadline=180 ;; esac'),fixture)
        self.assertIn(b'grep -qx linux,dummy-virt',changed)
        self.assertIn(b'deadline=120\n',changed)
    def test_phone_or_changed_observer_fixture_refused(self):
        fixture=(ROOT/'devices/oneplus13/desktop/qemu-install').read_bytes()
        for value in [b'OnBootSec=600s\n',fixture.replace(b'linux,dummy-virt',b'oneplus,dodge'),
                      fixture+ b'\ndeadline=180 ;; esac\n']:
            with self.subTest(length=len(value)),self.assertRaises(ValueError):m.reserve_emulator_time(value)
if __name__=='__main__':unittest.main()
