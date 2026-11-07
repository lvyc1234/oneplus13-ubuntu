# SPDX-License-Identifier: GPL-2.0-only
import gzip
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/oneplus13"
sys.path.insert(0, str(TOOLS))
from common import make_cpio, read_cpio


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


layout = load("op13_dualboot", TOOLS / "op13-prepare-dualboot.py")
persistent = load("op13_persistent", TOOLS / "persistent.py")
hardware = load(
    "op13_persistent_hardware",
    ROOT / "devices/oneplus13/desktop/persistent-hardware.py",
)
charger = load(
    "op13_persistent_charger",
    ROOT / "devices/oneplus13/desktop/persistent-charger.py",
)


class LayoutTests(unittest.TestCase):
    def test_stock_oneplus13_geometry_creates_requested_layout(self):
        info = """\
Partition GUID code: 1B81E7E6-F50D-419B-A739-2AEEF8DA3335
Partition unique GUID: 9B5BF515-3A4F-8DDC-8A47-88122B5B3C8A
First sector: 4117956
Last sector: 249145338
Attribute flags: 0000000000000000
"""
        command, sizes = layout.layout(info, 249145338)
        self.assertEqual(sizes["userdata"], 448 * layout.GIB)
        self.assertEqual(sizes["linux_rootfs"], 64 * layout.GIB)
        self.assertGreater(sizes["shared_media"], 400 * layout.GIB)
        self.assertIn("--partition-guid=15:9B5BF515-3A4F-8DDC-8A47-88122B5B3C8A", command)
        self.assertIn("--change-name=16:linux_rootfs", command)
        self.assertIn("--change-name=17:shared_media", command)

    def test_wrong_stock_start_is_rejected(self):
        info = """\
Partition GUID code: 1B81E7E6-F50D-419B-A739-2AEEF8DA3335
Partition unique GUID: 9B5BF515-3A4F-8DDC-8A47-88122B5B3C8A
First sector: 1
Last sector: 249145338
Attribute flags: 0000000000000000
"""
        with self.assertRaises(RuntimeError):
            layout.layout(info, 249145338)


class PersistentInitramfsTests(unittest.TestCase):
    def test_initramfs_adds_persistent_mount_before_ram_protection(self):
        init = """#!/bin/sh
# This runs continuously: later-arriving UFS disks are covered as well.
# Composite USB NCM + serial console.
echo USB

case " $(cat /proc/cmdline) " in
  *' oneplus13.desktop=1 '*) [ -n "$udc" ] && desktop_main ;;
esac
"""
        original = gzip.compress(make_cpio([
            ("init", stat.S_IFREG | 0o755, init.encode(), 0, 0),
        ]), mtime=0)
        packed = persistent.derive(original)
        entries = read_cpio(gzip.decompress(packed))
        text = entries["init"]["data"].decode()
        self.assertLess(text.index("persistent_main"), text.index("# This runs continuously"))
        self.assertIn("oneplus13.persistent=1", text)
        self.assertIn("bin/oneplus13-persistent-prepare", entries)
        self.assertIn("bin/oneplus13-persistent-launch", entries)
        self.assertIn("op13-persistent/persistent-hardware.py", entries)
        self.assertIn("op13-persistent/persistent-charger.py", entries)
        self.assertIn("op13-persistent/oneplus13-charger.target", entries)
        self.assertIn(
            b"/sbin/init --unit=oneplus13-charger.target",
            entries["bin/oneplus13-persistent-launch"]["data"],
        )
        self.assertIn(b"PARTLABEL=linux_rootfs", entries["bin/oneplus13-persistent-prepare"]["data"])
        self.assertIn(
            "  *' oneplus13.persistent=1 '*) ;;\n  *' oneplus13.desktop=1 '*)",
            text,
        )
        self.assertIn(b"reuse_installed=", entries["bin/oneplus13-native-handoff"]["data"])


class ChargerStateTests(unittest.TestCase):
    def test_poweroff_waits_for_startup_and_continuous_disconnect(self):
        state = charger.ChargerState(0)
        for now in (0, 5, 14, 15, 17.9):
            self.assertIsNone(state.action(0, now))
        self.assertEqual(state.action(0, 18), 'poweroff')

    def test_reconnect_and_unknown_supply_cancel_pending_poweroff(self):
        for reading in (1, None, 2):
            with self.subTest(reading=reading):
                state = charger.ChargerState(0)
                self.assertIsNone(state.action(0, 15))
                self.assertIsNone(state.action(reading, 17))
                self.assertIsNone(state.action(0, 19))
                self.assertIsNone(state.action(0, 21.9))
                self.assertEqual(state.action(0, 22), 'poweroff')

    def test_continuous_power_key_hold_requests_normal_reboot(self):
        state = charger.ChargerState(0)
        state.key(True, 20)
        self.assertIsNone(state.action(1, 21.9))
        state.key(True, 21.9)
        self.assertEqual(state.action(1, 22), 'reboot')

    def test_short_presses_do_not_accumulate_into_a_hold(self):
        state = charger.ChargerState(0)
        state.key(True, 20)
        state.key(False, 21)
        state.key(True, 22)
        self.assertIsNone(state.action(1, 23.9))
        state.key(False, 23.9)
        self.assertIsNone(state.action(1, 30))


@unittest.skipUnless(os.name == 'posix' and shutil.which('sh'), 'POSIX shell required')
class PersistentLaunchTests(unittest.TestCase):
    def test_charger_selection_uses_proc_after_it_moves_to_newroot(self):
        launch = ROOT / 'devices/oneplus13/desktop/persistent-launch'
        for cmdline, expected in (
            ('oneplus13.persistent=1 androidboot.mode=charger',
             '/sbin/init\n--unit=oneplus13-charger.target\n'),
            ('oneplus13.persistent=1 oplusboot.mode=reboot', '/sbin/init\n'),
        ):
            with self.subTest(cmdline=cmdline), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                newroot = base / 'newroot'
                (base / 'bin').mkdir()
                (base / 'evidence').mkdir()
                (base / 'cmdline').write_text(cmdline)
                (base / 'bin/mount').write_text(
                    '#!/bin/sh\nif [ "$2" = /proc ]; then\n'
                    'mkdir -p "$3"\ncp "$FIXTURE/cmdline" "$3/cmdline"\nfi\n'
                )
                (base / 'bin/switch_root').write_text(
                    '#!/bin/sh\nshift\nprintf "%s\\n" "$@" > "$FIXTURE/result"\n'
                )
                for file in (base / 'bin').iterdir():
                    file.chmod(0o755)
                text = launch.read_text().replace(
                    '/bin/oneplus13-persistent-prepare', '/bin/true'
                ).replace('/newroot', str(newroot)).replace(
                    '/run/evidence', str(base / 'evidence')
                ).replace('/dev/kmsg', str(base / 'kmsg'))
                script = base / 'launch.sh'
                script.write_text(text)
                result = subprocess.run(
                    ['sh', '-c', '. ' + shlex.quote(str(script)) +
                     '\nwatchdog_pid=\npersistent_main'],
                    env={**os.environ, 'PATH': str(base / 'bin') + ':' + os.environ['PATH'],
                         'FIXTURE': str(base)},
                    capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((base / 'result').read_text(), expected)


class PersistentHardwareTests(unittest.TestCase):
    def test_hardware_start_checks_the_mounted_block_device(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            root_line = "29 1 259:0 / / rw,noatime - ext4 /dev/sda16 rw\n"
            files = {
                "proc/cmdline": "oneplus13.persistent=1 oneplus13.root=PARTLABEL=linux_rootfs",
                "run/evidence/rootfs.txt": "ONEPLUS13_PERSISTENT_ROOT_READY",
                "proc/self/mountinfo": root_line,
                "sys/dev/block/259:0/uevent": "PARTNAME=linux_rootfs\n",
                "sys/dev/block/8:15/uevent": "PARTNAME=userdata\n",
            }
            for name, body in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            hardware.validate_root(root)
            mountinfo = root / "proc/self/mountinfo"
            mountinfo.write_text(root_line.replace("259:0", "8:15"))
            with self.assertRaises(ValueError):
                hardware.validate_root(root)
            mountinfo.write_text(root_line + "30 29 8:15 / /mnt/android rw - f2fs /dev/sda15 rw\n")
            with self.assertRaises(ValueError):
                hardware.validate_root(root)


if __name__ == "__main__":
    unittest.main()
