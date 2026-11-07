#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify a freshly downloaded desktop artifact, without executing its software."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    out = args.directory.resolve()
    for line in (out / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        if Path(name).name != name or name in {'.', '..'}:
            raise SystemExit('Unsafe checksum filename')
        if digest(out / name) != expected:
            raise SystemExit(f'Downloaded artifact checksum mismatch: {name}')
    manifest = json.loads((out / 'manifest.json').read_text())
    if manifest.get('hardware_tested') is not False:
        raise SystemExit('Unexpected hardware support claim')
    for name, expected in manifest['files'].items():
        if Path(name).name != name or name in {'.', '..'}:
            raise SystemExit('Unsafe manifest filename')
        path = out / name
        if path.stat().st_size != expected['size'] or digest(path) != expected['sha256']:
            raise SystemExit(f'Manifest size/hash mismatch: {name}')
    packages = {}
    for line in (out / 'packages.tsv').read_text().splitlines():
        package, version, arch = line.split('\t')
        if arch not in {'arm64', 'all'}:
            raise SystemExit(f'Wrong target package architecture: {package} / {arch}')
        packages[package.split(':')[0]] = version
    # Require Ubuntu's full desktop metapackage alongside the device additions.
    repo=Path(__file__).resolve().parents[2]
    profile=manifest.get('desktop_profile','full')
    if profile not in ('full','diagnostic'):raise SystemExit('Unknown rootfs profile')
    seed=repo/('devices/oneplus13/ubuntu-diagnostic.json' if profile=='diagnostic' else 'devices/oneplus13/ubuntu.json')
    package_seed=json.loads(seed.read_text())['packages']
    for package in package_seed:
        if package not in packages:
            raise SystemExit(f'Missing required desktop package: {package}')
    for package in (['ubuntu-desktop','ubuntu-desktop-minimal'] if profile=='full' else [])+['gnome-initial-setup','resources','gnome-system-monitor']:
        if package not in packages:
            raise SystemExit(f'Missing standard Ubuntu desktop package: {package}')
    image = out / 'rootfs.squashfs'
    read = lambda name: subprocess.check_output(['unsquashfs', '-cat', str(image), name])
    if read('etc/machine-id').strip():
        raise SystemExit('Machine identity was embedded into image')
    root = next(line for line in read('etc/shadow').decode().splitlines() if line.startswith('root:'))
    if not root.split(':')[1].startswith(('!', '*')):
        raise SystemExit('Root account is not locked')
    passwd = read('etc/passwd').decode().splitlines()
    if any(1000 <= int(line.split(':')[2]) < 60000 for line in passwd if line):
        raise SystemExit('A regular user was precreated in the rootfs')
    initial_setup=read('usr/libexec/gnome-initial-setup')
    if initial_setup[:5] != b'\x7fELF\x02' or struct.unpack_from('<H', initial_setup, 18)[0] != 183:
        raise SystemExit('GNOME first-user setup binary missing or wrong architecture')
    gdm_config=read('etc/gdm3/custom.conf').decode()
    if '[daemon]' not in gdm_config or 'InitialSetupEnable=true' not in gdm_config:
        raise SystemExit('GDM first-user setup is not explicitly enabled')
    environment=read('etc/environment').decode().splitlines()
    if [line for line in environment if line.startswith('GSK_RENDERER=')] != ['GSK_RENDERER=gl']:
        raise SystemExit('Full desktop must select the measured GTK GL renderer')
    if [line for line in environment if line.startswith('CLUTTER_PAINT=')] != ['CLUTTER_PAINT=disable-clipped-redraws']:
        raise SystemExit('Phone display must avoid clipped damage rectangles')
    if any(line.startswith(('GTK_IM_MODULE=','QT_IM_MODULE=','XMODIFIERS=')) for line in environment):
        raise SystemExit('A global IM override bypasses the native Wayland input path')
    wifi_identity = read('etc/NetworkManager/conf.d/90-oneplus13-wifi-identity.conf')
    if wifi_identity != (repo / 'devices/oneplus13/desktop/90-oneplus13-wifi-identity.conf').read_bytes():
        raise SystemExit('Persistent WiFi identity policy differs from source')
    mobile=read('usr/share/glib-2.0/schemas/90-oneplus13-mobile.gschema.override').decode()
    if '[org.gnome.desktop.session]\nidle-delay=uint32 180\n' not in mobile:
        raise SystemExit('Phone compatibility blank policy missing')
    if 'power-button-action=\'nothing\'' not in mobile or 'sleep-inactive-battery-type=\'nothing\'' not in mobile:
        raise SystemExit('Unqualified system suspend must remain disabled')
    if '[org.gnome.desktop.a11y.applications]\nscreen-keyboard-enabled=true\n' not in mobile:
        raise SystemExit('Mobile first-user screen keyboard default missing')
    if '[org.gnome.desktop.interface]\ntoolkit-accessibility=true\n' not in mobile:
        raise SystemExit('Mobile accessibility bridge default missing')
    for name in ['gnome-initial-setup', 'gnome-initial-setup-first-login',
                 'gnome-initial-setup-upgrade-login']:
        if 'Environment=GIS_SMALL_SCREEN=1' not in read(
                'usr/lib/systemd/user/'+name+'.service.d/60-oneplus13-small-screen.conf').decode():
            raise SystemExit('Initial Setup small-screen layout missing')
    defaults=read('etc/xdg/mimeapps.list').decode()
    if profile=='full' and 'x-scheme-handler/https=org.gnome.Epiphany.desktop' not in defaults:
        raise SystemExit('Usable full-profile browser default missing')
    if profile=='diagnostic' and ('org.gnome.Epiphany.desktop' in defaults or
            'text/plain=org.gnome.TextEditor.desktop' not in defaults):
        raise SystemExit('Diagnostic profile advertises a removed application')
    import configparser
    vendor=configparser.ConfigParser();vendor.read_string(read('etc/gnome-initial-setup/vendor.conf').decode())
    if vendor.get('pages','skip',fallback='') != 'timezone':
        raise SystemExit('Phone first-user timezone policy missing or other setup pages suppressed')
    if read('etc/timezone').strip() != b'Asia/Shanghai':
        raise SystemExit('Preconfigured first-user timezone differs')
    binaries=['usr/bin/gnome-shell', 'usr/sbin/gdm3', 'usr/sbin/NetworkManager',
                 'usr/sbin/wpa_supplicant','usr/sbin/iw','usr/bin/bluetoothctl',
                 'usr/bin/bluetooth-sendto','usr/libexec/bluetooth/obexd',
                 'usr/bin/resources','usr/bin/gnome-system-monitor']
    if profile=='full':binaries+=['usr/bin/epiphany','usr/bin/gnome-software']
    if profile=='diagnostic' and 'ubuntu-desktop' in packages:
        raise SystemExit('Diagnostic profile unexpectedly contains full desktop metapackage')
    if packages['resources']!='1.10.2-0ubuntu3.1+op13.1':
        raise SystemExit('MSM-aware Resources build missing')
    if profile=='diagnostic':
        dock=json.loads(read('usr/share/gnome-shell/extensions/ubuntu-dock@ubuntu.com/metadata.json'))
        if dock.get('uuid')!='ubuntu-dock@ubuntu.com':
            raise SystemExit('Ubuntu extension bundle lacks the actual Dock extension')
    for name in binaries:
        binary = read(name)
        if binary[:5] != b'\x7fELF\x02' or struct.unpack_from('<H', binary, 18)[0] != 183:
            raise SystemExit(f'Wrong executable architecture: {name}')
    for name,marker in [
        ('usr/share/dbus-1/system-services/fi.w1.wpa_supplicant1.service',b'Name=fi.w1.wpa_supplicant1'),
        ('usr/lib/systemd/system/wpa_supplicant.service',b'BusName=fi.w1.wpa_supplicant1'),
        ('usr/share/dbus-1/services/org.bluez.obex.service',b'Name=org.bluez.obex'),
        ('usr/lib/systemd/user/obex.service',b'BusName=org.bluez.obex')]:
        if marker not in read(name):
            raise SystemExit('Missing or mismatched D-Bus activation contract: '+name)
    if 'VERSION_ID="26.04"' not in read('etc/os-release').decode():
        raise SystemExit('Unexpected Ubuntu release')
    result = {
        'status': 'DOWNLOADED_ROOTFS_OFFLINE_VERIFIED_NOT_DEVICE_TESTED',
        'integration_commit': manifest['integration_commit'],
        'rootfs_sha256': digest(image), 'rootfs_bytes': image.stat().st_size,
        'gnome': (out / 'gnome-version.txt').read_text().strip(),
        'packages': len(packages), 'desktop_profile':profile,'hardware_tested': False,
        'checks': ['all downloaded SHA256SUMS', 'manifest sizes/hashes', 'all package architectures',
                   ('full Ubuntu Desktop metapackages and required runtime packages' if profile=='full'
                    else 'diagnostic runtime packages and actual Dock files; full desktop omitted'),
                   'locked root, no precreated desktop user, enabled GDM first-user setup, and empty machine-id',
                   'actual desktop/WiFi/Bluetooth ARM64 ELF binaries',
                   'supplicant and OBEX D-Bus/systemd activation contracts', 'actual Ubuntu os-release']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
