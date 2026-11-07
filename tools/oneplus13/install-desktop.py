#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install opt-in RAM desktop support into the freshly built phone rootfs."""
import argparse
from pathlib import Path
import json
import tarfile
from common import sha256
from desktop import PIN

REPO = Path(__file__).resolve().parents[2]
CONDITION = ('ConditionKernelCommandLine=oneplus13.desktop=1\n'
             'ConditionPathExists=/run/oneplus13/ram-session\n')


def mime_defaults(profile):
    if profile=='full':
        return ('[Default Applications]\nx-scheme-handler/http=org.gnome.Epiphany.desktop\n'
                'x-scheme-handler/https=org.gnome.Epiphany.desktop\ntext/html=org.gnome.Epiphany.desktop\n')
    if profile=='diagnostic':
        return '[Default Applications]\ntext/plain=org.gnome.TextEditor.desktop\n'
    raise ValueError('Unknown desktop profile')


def install(root, kernel=None, firmware=None, profile='full'):
    if not root.is_relative_to((REPO/'out').resolve()) or not (root/'etc/os-release').is_file():
        raise ValueError('Expected a fresh phone rootfs staging tree under repository out/')

    defaults=mime_defaults(profile)

    def write(name, data, mode=0o644):
        path = root/name
        if not path.resolve().is_relative_to(root):
            raise ValueError('Support path escapes staging root')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, newline='\n')
        path.chmod(mode)

    def unit(name, body, target):
        write('usr/lib/systemd/system/'+name, '[Unit]\n'+CONDITION+body)
        link = root/('etc/systemd/system/'+target+'.wants/'+name)
        link.parent.mkdir(parents=True, exist_ok=True)
        target = '/usr/lib/systemd/system/'+name
        if link.is_symlink() and str(link.readlink()) == target:
            pass  # Refreshing only this package's files in a verified base.
        elif link.exists() or link.is_symlink():
            raise ValueError('Unexpected preexisting unit link')
        else:
            link.symlink_to(target)

    for name in ['runtime-guard', 'runtime-usb', 'runtime-evidence', 'runtime-serial']:
        write('usr/libexec/oneplus13-'+name,
              (REPO/'devices/oneplus13/desktop'/name).read_text(), 0o755)
    write('usr/libexec/oneplus13-readonly-block.py',
          (REPO/'tools/oneplus13/readonly-block.py').read_text(), 0o755)
    write('usr/libexec/oneplus13-display-diagnostics.py',
          (REPO/'tools/oneplus13/display-diagnostics.py').read_text(), 0o755)
    write('usr/libexec/oneplus13-qualified-bringup',
          (REPO/'devices/oneplus13/desktop/qualified-bringup').read_text(), 0o755)
    write('usr/share/glib-2.0/schemas/90-oneplus13-mobile.gschema.override',
          (REPO/'devices/oneplus13/desktop/90-oneplus13-mobile.gschema.override').read_text())
    write('etc/gnome-initial-setup/vendor.conf',
          (REPO/'devices/oneplus13/desktop/firstboot-vendor.conf').read_text())
    for name in ['gnome-initial-setup', 'gnome-initial-setup-first-login',
                 'gnome-initial-setup-upgrade-login']:
        write('usr/lib/systemd/user/'+name+'.service.d/60-oneplus13-small-screen.conf',
              (REPO/'devices/oneplus13/desktop/60-oneplus13-small-screen.conf').read_text())
    # Do not advertise a removed browser in the lightweight diagnostic profile.
    write('etc/xdg/mimeapps.list',defaults)
    write('usr/lib/udev/rules.d/09-oneplus13-ram-readonly.rules',
          (REPO/'devices/oneplus13/desktop/09-oneplus13-ram-readonly.rules').read_text())
    write('usr/lib/oneplus13-desktop/version', '1\n')
    write('usr/libexec/oneplus13-native-display',
          (REPO/'devices/oneplus13/native-desktop.py').read_text(), 0o755)
    # kmod blacklist only suppresses aliases. The install rules also reject
    # ordinary explicit/dependency loads. Native desktop packaging stays
    # disabled until this policy is exercised with the actual ARM64 rootfs.
    write('etc/modprobe.d/90-oneplus13-display-manual.conf',
          '# Native display requires a separately validated activation path.\n'
          'blacklist panel_oneplus_aa569\ninstall panel_oneplus_aa569 /bin/false\n'
          'blacklist msm\ninstall msm /bin/false\n')
    if kernel:
        manifest = json.loads((kernel/'manifest.json').read_text())
        if (manifest['integration_commit'] != PIN or
                manifest['device'] != 'oneplus,dodge' or manifest['hardware_tested'] is not False):
            raise ValueError('Desktop requires the pinned verified phone kernel artifact')
        archive = kernel/'modules.tar.gz'
        if sha256(archive) != manifest['files']['modules.tar.gz']['sha256']:
            raise ValueError('Kernel module archive hash mismatch')
        with tarfile.open(archive) as source:
            for member in source.getmembers():
                name = member.name.removeprefix('./')
                if name not in {'.', 'lib', 'lib/modules', 'lib/modules/'+manifest['kernel_release']} and not name.startswith('lib/modules/'+manifest['kernel_release']+'/'):
                    raise ValueError('Unexpected module member')
            source.extractall(root, filter='data')
        write('usr/lib/oneplus13-desktop/kernel-release', manifest['kernel_release']+'\n')
    if firmware:
        from firmware import verify
        pin = verify(firmware)
        files = {}
        for item in pin['files']:
            if item['partition'] != 'vendor':
                continue
            name = Path(item['output']).name
            path = ('qcom/sm8750/oneplus/dodge/'+name if name.endswith('_zap.mbn') else 'qcom/'+name)
            dest = root/'usr/lib/firmware'/path
            if not dest.resolve().is_relative_to(root):
                raise ValueError('Firmware path escapes staging root')
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((firmware/item['output']).read_bytes())
            dest.chmod(0o644)
            files[path] = {'bytes': item['size'], 'sha256': item['sha256']}
        write('usr/lib/oneplus13-desktop/graphics-firmware.json',
              json.dumps({'hardware_tested': False, 'version': pin['version'], 'files': files}, indent=2)+'\n')
    write('etc/NetworkManager/conf.d/80-oneplus13-ram-usb.conf',
          '[keyfile]\nunmanaged-devices=mac:02:13:00:00:00:01\n')
    write('usr/lib/udev/rules.d/10-oneplus13-ram-storage.rules',
          'ACTION=="add", SUBSYSTEM=="block", KERNEL!="loop*|ram*|zram*", '
          'TEST=="/run/oneplus13/ram-session", ENV{UDISKS_IGNORE}="1", '
          'RUN+="/usr/sbin/blockdev --setro %N"\n')
    unit('oneplus13-ram-guard.service',
         'Description=Protect physical storage during the RAM desktop\n'
         'DefaultDependencies=no\nBefore=sysinit.target systemd-udevd.service systemd-udev-trigger.service\n'
         'IgnoreOnIsolate=yes\nFailureAction=reboot-force\n[Service]\nType=simple\n'
         'ExecStartPre=/usr/libexec/oneplus13-runtime-guard --once\n'
         'ExecStart=/usr/libexec/oneplus13-runtime-guard\n', 'sysinit.target')
    unit('oneplus13-ram-deadline.timer',
         'Description=Bound the complete RAM desktop boot\nDefaultDependencies=no\n'
         'Before=sysinit.target\nIgnoreOnIsolate=yes\n[Timer]\nOnBootSec=900s\n'
         'AccuracySec=1s\nUnit=oneplus13-ram-deadline.service\n', 'sysinit.target')
    write('usr/lib/systemd/system/oneplus13-ram-deadline.service',
          '[Unit]\n'+CONDITION+'Description=End the RAM session\nDefaultDependencies=no\n'
          '[Service]\nType=oneshot\nExecStart=/usr/bin/busybox reboot -f -n\n')
    unit('oneplus13-ram-evidence.service',
         'Description=RAM desktop liveness evidence\nAfter=local-fs.target\n'
         '[Service]\nExecStart=/usr/libexec/oneplus13-runtime-evidence\n', 'multi-user.target')
    unit('oneplus13-firstboot-hardware.service',
         'Description=Initialize qualified phone hardware before first-user setup\n'
         'ConditionKernelCommandLine=!oneplus13.desktop.qemu=1\n'
         'Requires=oneplus13-ram-guard.service oneplus13-ram-deadline.timer oneplus13-ram-usb.service\n'
         'After=oneplus13-ram-guard.service oneplus13-ram-deadline.timer oneplus13-ram-usb.service NetworkManager.service\n'
         'Before=display-manager.service gdm.service gdm3.service\n'
         '[Service]\nType=oneshot\nRemainAfterExit=yes\nTimeoutStartSec=300s\n'
         'ExecStart=/usr/libexec/oneplus13-qualified-bringup --firstboot\n', 'multi-user.target')
    write('etc/systemd/system/gdm.service.d/60-oneplus13-hardware.conf',
          '[Unit]\nRequires=oneplus13-firstboot-hardware.service\nAfter=oneplus13-firstboot-hardware.service\n')
    unit('oneplus13-ram-usb.service',
         'Description=Retain the phone RAM USB network\nAfter=NetworkManager.service\n'
         'ConditionPathExists=/sys/kernel/config/usb_gadget/oneplus13/UDC\n'
         '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
         'ExecStart=/usr/libexec/oneplus13-runtime-usb\n', 'multi-user.target')
    for name, command in {
        'dhcp': '/usr/bin/busybox udhcpd -f /run/oneplus13/udhcpd.conf',
        'http': '/usr/bin/busybox httpd -f -p 172.16.13.1:8080 -h /run/evidence',
        'shell': '/usr/bin/busybox telnetd -F -b 172.16.13.1:23 -l /bin/sh',
        'serial': '/usr/libexec/oneplus13-runtime-serial',
    }.items():
        unit('oneplus13-ram-'+name+'.service',
             'Description=Explicit RAM USB development '+name+'\n'
             'Requires=oneplus13-ram-usb.service\nAfter=oneplus13-ram-usb.service\n'
             'ConditionPathExists=/sys/kernel/config/usb_gadget/oneplus13/UDC\n'
             '[Service]\nExecStart='+command+'\n', 'multi-user.target')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--kernel', type=Path)
    p.add_argument('--firmware', type=Path)
    p.add_argument('--profile', choices=['full','diagnostic'],default='full')
    a = p.parse_args()
    install(a.root.resolve(), a.kernel.resolve() if a.kernel else None,
            a.firmware.resolve() if a.firmware else None,a.profile)
