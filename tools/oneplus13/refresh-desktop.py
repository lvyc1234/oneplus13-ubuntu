#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Refresh only phone support in a previously verified GNOME artifact on Actions."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from common import sha256

REPO = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', required=True, type=Path)
    p.add_argument('--kernel', required=True, type=Path)
    p.add_argument('--firmware', required=True, type=Path)
    a = p.parse_args()
    base, kernel, firmware = a.base.resolve(), a.kernel.resolve(), a.firmware.resolve()
    root, dest = REPO/'out/oneplus13-rootfs-stage', REPO/'out/oneplus13-rootfs-artifacts'
    for path in [base, kernel, firmware, root, dest]:
        if not path.is_relative_to((REPO/'out').resolve()):
            raise ValueError('All refresh inputs/outputs must be inside repository out/')
    if root.exists() or dest.exists():
        raise ValueError('Fresh refresh outputs required')
    subprocess.run(['python3', str(REPO/'tools/oneplus13/verify-rootfs.py'), str(base),
                    '--report', str(REPO/'out/base-rootfs-verification.json')], check=True)
    old = json.loads((base/'manifest.json').read_text())
    subprocess.run(['unsquashfs', '-no-progress', '-d', str(root), str(base/'rootfs.squashfs')], check=True)
    spec = importlib.util.spec_from_file_location('install_desktop', REPO/'tools/oneplus13/install-desktop.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    installer.install(root, kernel, firmware)
    subprocess.run(['chroot', str(root), '/usr/bin/systemd-analyze', 'verify',
                    *sorted(p.name for p in (root/'usr/lib/systemd/system').glob('oneplus13-ram-*'))], check=True)
    rule = root/'usr/lib/udev/rules.d/10-oneplus13-ram-storage.rules'
    if not rule.resolve().is_relative_to(root) or not rule.is_file():
        raise ValueError('Installed udev rule missing or outside staging root')
    print('Installed RAM storage rule verified inside staging root', flush=True)
    # v255 forbids --root with FILE arguments. Parse this exact absolute file;
    # verify performs no RUN command or device event and needs no target mounts.
    subprocess.run(['udevadm', '--version'], check=True)
    subprocess.run(['udevadm', 'verify', str(rule)], check=True)
    dest.mkdir()
    for name in ['packages.tsv', 'gnome-version.txt', 'os-release', 'ubuntu-source.json', 'README.md']:
        shutil.copy2(base/name, dest/name)
    epoch = subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], text=True).strip()
    subprocess.run(['mksquashfs', str(root), str(dest/'rootfs.squashfs'), '-noappend', '-comp', 'zstd',
                    '-processors', '4', '-mkfs-time', epoch, '-all-time', epoch, '-no-progress'], check=True)
    info = subprocess.check_output(['unsquashfs', '-s', str(dest/'rootfs.squashfs')])
    (dest/'squashfs-info.txt').write_bytes(info)
    manifest = {**old, 'integration_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'base_rootfs_sha256': sha256(base/'rootfs.squashfs'),
                'base_rootfs_integration_commit': old['integration_commit'],
                'matching_kernel_release': (kernel/'kernel.release').read_text().strip(),
                'matching_graphics_firmware_files': 4,
                'files': {p.name: {'sha256': sha256(p), 'size': p.stat().st_size}
                          for p in sorted(dest.iterdir()) if p.is_file()}}
    (dest/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (dest/'SHA256SUMS').write_text(''.join(f'{sha256(p)}  {p.name}\n' for p in sorted(dest.iterdir()) if p.is_file()))


if __name__ == '__main__':
    main()
