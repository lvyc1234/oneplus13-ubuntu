#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Derive a separate desktop initramfs; preserve the diagnostic build untouched."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import threading
from common import make_cpio, read_cpio, sha256

REPO = Path(__file__).resolve().parents[2]
PIN = 'e5c694473d738f582056d5c35af96e4d8ecfe0ff'

# Files which make a clean image reach a touch-operated, account-free welcome.
FIRSTBOOT_SOURCE_FILES = {
    'etc/gnome-initial-setup/vendor.conf': 'devices/oneplus13/desktop/firstboot-vendor.conf',
    'usr/libexec/oneplus13-qualified-bringup': 'devices/oneplus13/desktop/qualified-bringup',
    'usr/libexec/oneplus13-display-diagnostics.py': 'tools/oneplus13/display-diagnostics.py',
    'usr/share/glib-2.0/schemas/90-oneplus13-mobile.gschema.override':
        'devices/oneplus13/desktop/90-oneplus13-mobile.gschema.override',
}
for _unit in ['gnome-initial-setup', 'gnome-initial-setup-first-login',
              'gnome-initial-setup-upgrade-login']:
    FIRSTBOOT_SOURCE_FILES['usr/lib/systemd/user/'+_unit+'.service.d/60-oneplus13-small-screen.conf'] = (
        'devices/oneplus13/desktop/60-oneplus13-small-screen.conf')
FIRSTBOOT_GENERATED_FILES = (
    'usr/lib/systemd/system/oneplus13-firstboot-hardware.service',
    'etc/systemd/system/gdm.service.d/60-oneplus13-hardware.conf',
    'etc/xdg/mimeapps.list',
    'usr/share/glib-2.0/schemas/gschemas.compiled',
)


def derive(original):
    entries = read_cpio(gzip.decompress(original))
    before = entries['init']['data']
    if before != (REPO/'devices/oneplus13/init').read_bytes().replace(b'\r\n', b'\n'):
        raise ValueError('Expected the unchanged pinned diagnostic init')
    text = before.decode()

    def replace(old, new):
        nonlocal text
        if text.count(old) != 1:
            raise ValueError('Unexpected diagnostic anchor: '+old[:40])
        text = text.replace(old, new)

    replace("(\n  sleep 300\n  echo 'RAM diagnostic deadline reached; rebooting to the installed OS'\n  reboot -f\n) &",
            "( timer_pid=\n  trap 'kill \"$timer_pid\" 2>/dev/null; wait \"$timer_pid\" 2>/dev/null; exit 0' TERM\n"
            "  sleep 900 &\n  timer_pid=$!\n  wait \"$timer_pid\"\n  reboot -f -n\n) &\nwatchdog_pid=$!")
    replace(') &\nuname -a', ') &\nstorage_pid=$!\nuname -a')
    replace('# Composite USB NCM',
            '. /bin/oneplus13-ram-launch\n'
            'case " $(cat /proc/cmdline) " in\n'
            "  *' oneplus13.desktop.qemu=1 '*) desktop_main ;;\n"
            'esac\n# Composite USB NCM')
    replace('  udhcpd /run/udhcpd.conf', '  setsid udhcpd -f /run/udhcpd.conf &\n  dhcp_pid=$!')
    replace('  httpd -p 8080 -h /run/evidence',
            '  setsid httpd -f -p 172.16.13.1:8080 -h /run/evidence &\n  http_pid=$!')
    replace('  telnetd -b 172.16.13.1:23 -l /bin/sh',
            '  setsid telnetd -F -b 172.16.13.1:23 -l /bin/sh &\n  telnet_pid=$!')
    # The runtime serial service takes over after handoff. Do not leave an
    # initramfs session leader or its child shell using the deleted old root.
    replace('  (while :; do setsid cttyhack sh < /dev/ttyGS0 > /dev/ttyGS0 2>&1; sleep 1; done) &\n', '')
    replace('while :; do\n  dmesg',
            'case " $(cat /proc/cmdline) " in\n'
            "  *' oneplus13.desktop=1 '*) [ -n \"$udc\" ] && desktop_main ;;\n"
            'esac\nwhile :; do\n  dmesg')
    entries['init']['data'] = text.encode()
    for source, target in [('prepare', 'ram-prepare'), ('launch', 'ram-launch'),
                           ('physical-platform', 'physical-platform'), ('qemu-install', 'qemu-install'),
                           ('native-handoff', 'native-handoff'),
                           ('native-handoff-selftest', 'native-handoff-selftest')]:
        name = 'bin/oneplus13-'+target
        if name in entries:
            raise ValueError('Desktop helper already exists')
        entries[name] = {'mode': stat.S_IFREG|0o755, 'major': 0, 'minor': 0,
                         'data': (REPO/'devices/oneplus13/desktop'/source).read_bytes().replace(b'\r\n', b'\n')}
    native_sha = hashlib.sha256((REPO/'devices/oneplus13/native-desktop.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    entries['bin/oneplus13-native-display-sha256'] = {
        'mode': stat.S_IFREG|0o600, 'major':0, 'minor':0, 'data':(native_sha+'\n').encode()}
    packed = gzip.compress(make_cpio([(n, p['mode'], p['data'], p['major'], p['minor'])
                                     for n, p in sorted(entries.items())]), mtime=0)
    check = read_cpio(gzip.decompress(packed))
    original_entries = read_cpio(gzip.decompress(original))
    if any(check[n] != v for n, v in original_entries.items() if n != 'init'):
        raise ValueError('Desktop derivation changed an unrelated diagnostic entry')
    return packed, {'original_entries_preserved_except_explicit_init': len(original_entries)-1,
                    'native_desktop_helper_sha256':native_sha,
                    'original_init_sha256': hashlib.sha256(before).hexdigest(),
                    'desktop_init_sha256': hashlib.sha256(check['init']['data']).hexdigest()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel', required=True, type=Path)
    p.add_argument('--rootfs', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--qemu', action='store_true')
    p.add_argument('--qemu-http', action='store_true', help='Exercise the production chunk fetch via an emulator-only virtual link')
    p.add_argument('--source-build', action='store_true', help='Require a fresh kernel compiled at this exact checkout commit')
    a = p.parse_args()
    if a.qemu_http and not a.qemu:
        p.error('--qemu-http requires --qemu')
    kernel, rootfs, out = a.kernel.resolve(), a.rootfs.resolve(), a.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((kernel/'manifest.json').read_text())
    expected_pin = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip() if a.source_build else PIN
    if manifest['integration_commit'] != expected_pin or manifest['hardware_tested'] is not False:
        raise ValueError('Wrong pinned kernel integration')
    for name in ['Image', 'initramfs.cpio.gz']:
        if sha256(kernel/name) != manifest['files'][name]['sha256']:
            raise ValueError('Kernel input hash mismatch: '+name)
    packed, report = derive((kernel/'initramfs.cpio.gz').read_bytes())
    ramdisk = out/'initramfs-desktop.cpio.gz'
    ramdisk.write_bytes(packed)
    cmdline = ('rdinit=/init panic=15 oneplus13.desktop=1 oneplus13.rootfs.sha256='+sha256(rootfs)+
               ' oneplus13.rootfs.bytes='+str(rootfs.stat().st_size))
    (out/'desktop-cmdline.txt').write_text(cmdline+'\n')
    report.update(scope='RAM_DESKTOP_INITRAMFS_NOT_DEVICE_TESTED', hardware_tested=False,
                  kernel_integration_commit=expected_pin, kernel_release=manifest['kernel_release'],
                  integration_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                  rootfs_sha256=sha256(rootfs), rootfs_bytes=rootfs.stat().st_size,
                  initramfs_sha256=sha256(ramdisk), absolute_boot_deadline_seconds=900,
                  default_diagnostic_artifact_modified=False, qemu_handoff_verified=False)
    (out/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    if a.qemu:
        command = ['qemu-system-aarch64', '-machine', 'virt', '-cpu', 'cortex-a76',
                   '-m', '6144', '-smp', '2', '-nographic', '-no-reboot', '-nic', 'none',
                   '-kernel', str(kernel/'Image'), '-initrd', str(ramdisk),
                   '-drive', 'file='+str(rootfs)+',format=raw,if=none,readonly=on,id=rootfs',
                   '-device', 'virtio-blk-device,drive=rootfs',
                   '-append', 'console=ttyAMA0 '+cmdline+' oneplus13.desktop.qemu=1']
        server = None
        stats = {'bytes': 0, 'parts': []}
        if a.qemu_http:
            spec = importlib.util.spec_from_file_location('rootfs_server', REPO/'tools/oneplus13/serve-rootfs.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            server = module.ThreadingHTTPServer(('127.0.0.1', 8000), module.handler(rootfs, stats))
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            command[-1] += ' oneplus13.desktop.qemu-http=1'
            command += ['-netdev', 'user,id=rootfsnet,net=172.16.13.0/24,host=172.16.13.2',
                        '-device', 'virtio-net-device,netdev=rootfsnet,mac=02:13:00:00:00:01']
        try:
            proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, timeout=300)
        except subprocess.TimeoutExpired as error:
            output = error.stdout or b''
            if isinstance(output, bytes):
                output = output.decode(errors='replace')
            (out/'qemu-desktop.log').write_text(output)
            print(output[-12000:])
            raise SystemExit('QEMU handoff exceeded 300 seconds; partial log retained') from None
        finally:
            if server:
                server.shutdown()
                server.server_close()
                thread.join()
        (out/'qemu-desktop.log').write_text(proc.stdout)
        markers = ['ONEPLUS13_DISPLAY_AUTOLOAD_BLOCKED', 'ONEPLUS13_NATIVE_RUNTIME_GUARDS_OFFLINE_OK',
                   'ONEPLUS13_NATIVE_PREFLIGHT_OFFLINE_OK',
                   'ONEPLUS13_NATIVE_HANDOFF_OFFLINE_OK',
                   'ONEPLUS13_DESKTOP_HANDOFF_OK',
                   'ONEPLUS13_QEMU_DEADLINE_FIRED', 'reboot: Restarting system']
        if (proc.returncode or any(m not in proc.stdout for m in markers) or
                not all(proc.stdout.index(left) < proc.stdout.index(right)
                        for left,right in zip(markers,markers[1:]))):
            print(proc.stdout[-12000:])
            raise SystemExit('Actual GNOME rootfs QEMU handoff failed')
        report['qemu_handoff_verified'] = True
        report['qemu_display_module_policy_verified'] = True
        report['qemu_native_preflight_rejects_public_root_verified'] = True
        report['qemu_native_preflight_runtime_guards_verified'] = True
        report['qemu_native_payload_data_handoff_verified'] = True
        report['native_display_activated'] = False
        report['qemu_deadline_expiry_verified'] = True
        report['qemu_deadline_test_seconds'] = 180 if a.qemu_http else 120
        report['physical_deadline_exercised'] = False
        if a.qemu_http:
            count = (rootfs.stat().st_size+module.CHUNK-1)//module.CHUNK
            if stats['bytes'] != rootfs.stat().st_size or stats['parts'] != list(range(count)):
                raise ValueError('HTTP path did not consume exactly the selected complete image')
            report['qemu_http_chunk_fetch_verified'] = True
            report['qemu_http_served_bytes'] = stats['bytes']
            report['qemu_http_served_parts'] = stats['parts']
        report['qemu_scope'] = 'systemd, actual GNOME userspace binary, storage guard, NetworkManager and D-Bus; no display/input/radio emulation'
    (out/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(f'{sha256(f)}  {f.name}\n' for f in sorted(out.iterdir())
                                        if f.is_file() and f.name != 'SHA256SUMS'))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
