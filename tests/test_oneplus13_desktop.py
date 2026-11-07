# SPDX-License-Identifier: GPL-2.0-only
import gzip
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]/'tools/oneplus13'
sys.path.insert(0, str(TOOLS))
from common import make_cpio, read_cpio
from desktop import derive
spec = importlib.util.spec_from_file_location('display_diagnostics', TOOLS/'display-diagnostics.py')
display_diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display_diagnostics)
spec = importlib.util.spec_from_file_location('install_desktop', TOOLS/'install-desktop.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class DesktopTests(unittest.TestCase):
    def test_profile_mime_defaults_only_advertise_included_applications(self):
        out=TOOLS.parents[1]/'out';out.mkdir(exist_ok=True)
        for profile in ['full','diagnostic']:
            with tempfile.TemporaryDirectory(dir=out) as temporary:
                root=Path(temporary).resolve();(root/'etc').mkdir()
                (root/'etc/os-release').write_text('ID=ubuntu\n')
                with patch.object(Path,'symlink_to'):installer.install(root,profile=profile)
                defaults=(root/'etc/xdg/mimeapps.list').read_text()
                seed='ubuntu.json' if profile=='full' else 'ubuntu-diagnostic.json'
                packages=json.loads((TOOLS.parents[1]/'devices/oneplus13'/seed).read_text())['packages']
                if profile=='full':
                    self.assertIn('epiphany-browser',packages)
                    self.assertIn('x-scheme-handler/https=org.gnome.Epiphany.desktop',defaults)
                else:
                    self.assertIn('gnome-text-editor',packages)
                    self.assertIn('text/plain=org.gnome.TextEditor.desktop',defaults)
                    self.assertNotIn('Epiphany',defaults)
        with self.assertRaises(ValueError):installer.mime_defaults('unknown')

    def test_firstboot_service_orders_native_activation_before_initial_setup(self):
        out=TOOLS.parents[1]/'out';out.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=out) as temporary:
            root=Path(temporary).resolve();(root/'etc').mkdir()
            (root/'etc/os-release').write_text('ID=ubuntu\n')
            with patch.object(Path,'symlink_to'):installer.install(root)
            unit=(root/'usr/lib/systemd/system/oneplus13-firstboot-hardware.service').read_text()
            self.assertIn('Before=display-manager.service gdm.service gdm3.service',unit)
            self.assertIn('ExecStart=/usr/libexec/oneplus13-qualified-bringup --firstboot',unit)
            drop=(root/'etc/systemd/system/gdm.service.d/60-oneplus13-hardware.conf').read_text()
            self.assertIn('Requires=oneplus13-firstboot-hardware.service',drop)
            keyboard=(root/'usr/share/glib-2.0/schemas/90-oneplus13-mobile.gschema.override').read_text()
            self.assertIn('screen-keyboard-enabled=true',keyboard)
        script=(TOOLS.parents[1]/'devices/oneplus13/desktop/qualified-bringup').read_text()
        branch=script[script.index('if [ "$mode" = --firstboot ]; then'):script.index('getent passwd a')]
        self.assertIn('exit 0',branch)
        self.assertNotIn('useradd',branch)
        self.assertNotIn('chpasswd',branch)

    def test_display_diagnostics_samples_without_retaining_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            connector=root/'sys/class/drm/card0-DSI-1'
            connector.mkdir(parents=True)
            (connector/'status').write_text('connected\n')
            (connector/'modes').write_text('1440x3168\n')
            state=root/'sys/kernel/debug/dri/0/state'
            state.parent.mkdir(parents=True)
            state.write_text('crtc[0]: enable=1\nplane[0]: fb=12\n')
            fb=root/'sys/class/graphics/fb0'
            fb.mkdir(parents=True)
            for name,value in {'name':'simple','virtual_size':'1440,3168',
                               'bits_per_pixel':'32','stride':'5760','blank':'0'}.items():
                (fb/name).write_text(value+'\n')
            device=root/'dev/fb0'
            device.parent.mkdir()
            with device.open('wb') as stream:
                stream.write(bytes([0,1,2,3])*4096)
                stream.truncate(1440*3168*4)
            proc=root/'proc/1'
            proc.mkdir(parents=True)
            (proc/'comm').write_text('gnome-shell\n')
            setup_proc=root/'proc/2'
            setup_proc.mkdir(parents=True)
            (setup_proc/'comm').write_text('gnome-initial-s\n')
            report=display_diagnostics.collect(root)
            self.assertEqual(report['drm_connectors']['card0-DSI-1']['status']['value'],'connected')
            self.assertIn('crtc[0]',report['drm_debug_state']['0']['value'])
            self.assertEqual(report['display_process_counts'],
                             {'gnome-initial-s':1,'gnome-shell':1})
            pixels=report['framebuffer']['pixel_samples']
            self.assertEqual(pixels['sample_count'],16)
            self.assertFalse(pixels['pixel_values_retained'])
            self.assertNotIn('data',pixels)

    def test_rootfs_uses_full_ubuntu_desktop_and_first_user_setup(self):
        repo=TOOLS.parents[1]
        packages=json.loads((repo/'devices/oneplus13/ubuntu.json').read_text())['packages']
        self.assertIn('ubuntu-desktop',packages)
        self.assertIn('gnome-initial-setup',packages)
        rootfs=(TOOLS/'rootfs.sh').read_text()
        diagnostic=json.loads((repo/'devices/oneplus13/ubuntu-diagnostic.json').read_text())['packages']
        self.assertNotIn('ubuntu-desktop',diagnostic)
        self.assertIn('gnome-initial-setup',diagnostic)
        self.assertIn('resources',diagnostic)
        self.assertIn('nautilus',diagnostic)
        self.assertIn('gnome-terminal',diagnostic)
        self.assertIn('recommend_args=(--install-recommends)',rootfs)
        self.assertIn('if [[ $profile == diagnostic ]]',rootfs)
        self.assertIn('--no-install-recommends',rootfs)
        self.assertIn('GNOME Initial Setup to create the first user',rootfs)
        self.assertIn('Refusing to ship a precreated desktop user',rootfs)
        self.assertIn('usr/libexec/gnome-initial-setup',rootfs)
        self.assertIn('InitialSetupEnable=true',rootfs)
        self.assertIn('sed -i -E',rootfs)
        ssh=(rootfs.split('PasswordAuthentication yes',1)[1].split('EOF',1)[0])
        self.assertIn('PermitRootLogin no',ssh)
        self.assertNotIn('AllowUsers',ssh)

    def fixture(self):
        init = (TOOLS.parents[1]/'devices/oneplus13/init').read_bytes().replace(b'\r\n', b'\n')
        return gzip.compress(make_cpio([
            ('bin', stat.S_IFDIR|0o755, b'', 0, 0),
            ('init', stat.S_IFREG|0o755, init, 0, 0),
            ('dev/console', stat.S_IFCHR|0o600, b'', 5, 1),
        ]))

    def test_preserves_device_nodes_and_unrelated_entries(self):
        original = self.fixture()
        packed, report = derive(original)
        old, new = read_cpio(gzip.decompress(original)), read_cpio(gzip.decompress(packed))
        self.assertEqual(new['dev/console'], old['dev/console'])
        self.assertEqual(new['bin'], old['bin'])
        self.assertEqual(report['original_entries_preserved_except_explicit_init'], 2)
        self.assertIn(b'watchdog_pid=$!', new['init']['data'])
        self.assertIn(b'sleep 300', old['init']['data'])
        self.assertNotIn(b'sleep 300', new['init']['data'])

    def test_changed_diagnostic_init_rejected(self):
        entries = read_cpio(gzip.decompress(self.fixture()))
        entries['init']['data'] += b'\necho unexpected\n'
        packed = gzip.compress(make_cpio([(n, p['mode'], p['data'], p['major'], p['minor'])
                                         for n, p in entries.items()]))
        with self.assertRaises(ValueError):
            derive(packed)

    def test_cannot_install_support_into_host_root(self):
        with self.assertRaises(ValueError):
            installer.install(Path('/').resolve())

    def test_installer_blocks_display_alias_and_ordinary_explicit_loads(self):
        out = TOOLS.parents[1]/'out'
        out.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=out) as temporary:
            root = Path(temporary).resolve()
            (root/'etc').mkdir()
            (root/'etc/os-release').write_text('ID=ubuntu\n')
            # Windows needs privileges for Linux absolute symlinks. Unit files
            # are still written; this test exercises the actual policy writer.
            with patch.object(Path,'symlink_to'):
                installer.install(root)
            self.assertTrue((root/'usr/libexec/oneplus13-display-diagnostics.py').is_file())
            policy = (root/'etc/modprobe.d/90-oneplus13-display-manual.conf').read_text()
            for module in ['panel_oneplus_aa569','msm']:
                self.assertIn('blacklist '+module+'\n',policy)
                self.assertIn('install '+module+' /bin/false\n',policy)

    def exercise_runtime_guard(self, already_read_only, setro_failures=0,
                               expect_success=True, mountinfo='', nonblock_success=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            block = root/'sys/class/block/sda1'
            block.mkdir(parents=True)
            (block/'dev').write_text('8:17\n')
            device = root/'dev/sda1'
            device.parent.mkdir()
            device.touch()
            binary = root/'bin'
            binary.mkdir()
            calls = root/'blockdev-calls'
            state = root/'read-only-state'
            failures = root/'setro-failures'
            state.write_text('1\n' if already_read_only else '0\n')
            failures.write_text(str(setro_failures)+'\n')
            (root/'mountinfo').write_text(mountinfo)
            fake = binary/'blockdev'
            fake.write_text(
                '#!/bin/sh\n'
                'printf "%s\\n" "$*" >> "$BLOCKDEV_CALLS"\n'
                'if [ "$1" = --getro ]; then IFS= read -r value < "$BLOCKDEV_STATE"; echo "$value"; exit 0; fi\n'
                'if [ "$1" = --setro ]; then IFS= read -r failures < "$SETRO_FAILURES"; '
                'if [ "$failures" -gt 0 ]; then echo "$((failures-1))" > "$SETRO_FAILURES"; exit 1; fi; '
                'echo 1 > "$BLOCKDEV_STATE"; exit 0; fi\n'
                'exit 1\n'
            )
            fake.chmod(0o755)
            script = (TOOLS.parents[1]/'devices/oneplus13/desktop/runtime-guard').read_text()
            script = script.replace('/sys/class/block', str(root/'sys/class/block'))
            script = script.replace('/proc/self/mountinfo', str(root/'mountinfo'))
            fallback = root/'readonly-block.py'
            fallback.write_text('import sys\nsys.exit('+('0' if nonblock_success else '1')+')\n')
            script = script.replace('/usr/libexec/oneplus13-readonly-block.py',str(fallback))
            script = script.replace('/run/oneplus13/readonly-block-errors.log',str(root/'fallback-errors'))
            script = script.replace('[ -b "/dev/$name" ]', '[ -e "/dev/$name" ]')
            script = script.replace('/dev/', str(root/'dev')+'/')
            script_path = root/'runtime-guard'
            script_path.write_text(script)
            result = subprocess.run(['/bin/sh', str(script_path), '--once'],
                env={'PATH':str(binary)+':/usr/bin:/bin', 'BLOCKDEV_CALLS':str(calls),
                     'BLOCKDEV_STATE':str(state), 'SETRO_FAILURES':str(failures)},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0 if expect_success else 1,
                             result.stderr)
            commands = calls.read_text().splitlines()
            expected = ['--getro '+str(device)]
            if not already_read_only:
                for attempt in range(min(setro_failures + 1, 3)):
                    if attempt:
                        expected.append('--getro '+str(device))
                    expected += ['--setro '+str(device), '--getro '+str(device)]
            self.assertEqual(commands, expected)

    def test_runtime_guard_does_not_rewrite_read_only_mounted_partition(self):
        self.exercise_runtime_guard(True)

    def test_runtime_guard_sets_read_only_on_new_writable_partition(self):
        self.exercise_runtime_guard(False)

    def test_runtime_guard_retries_transient_hotplug_setro_failure(self):
        self.exercise_runtime_guard(False, setro_failures=1)

    def test_runtime_guard_stays_fail_closed_after_bounded_retries(self):
        self.exercise_runtime_guard(False, setro_failures=3,
                                    expect_success=False)

    def test_runtime_guard_uses_confirmed_nonblocking_fallback_after_cli_failure(self):
        self.exercise_runtime_guard(False,setro_failures=3,nonblock_success=True)

    def test_runtime_guard_accepts_failed_block_flag_for_readonly_mount(self):
        self.exercise_runtime_guard(False, setro_failures=3,
            mountinfo='41 25 8:17 / /media/usb ro,nosuid,nodev - ntfs3 /dev/sda1 ro\n')

    def test_runtime_guard_rejects_writable_mount_when_block_flag_fails(self):
        self.exercise_runtime_guard(False, setro_failures=3, expect_success=False,
            mountinfo='41 25 8:17 / /media/usb rw,nosuid,nodev - ntfs3 /dev/sda1 rw\n')
