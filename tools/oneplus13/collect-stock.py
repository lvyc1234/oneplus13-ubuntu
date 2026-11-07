#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read-only Android evidence collector; run only when the owner is ready.

Requires adb and stock Android root. Never unlocks, reboots, mounts, flashes,
formats or writes a device partition. Outputs must remain private.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


def adb_read(adb, serial, *command, timeout=30):
    return subprocess.check_output([adb, '-s', serial, *command], timeout=timeout)


def private_destination(path):
    path = path.resolve()
    for parent in [path, *path.parents]:
        if (parent / '.git').exists():
            probe = subprocess.run(['git', '-C', str(parent), 'check-ignore', '-q', str(path)],
                                   capture_output=True)
            if probe.returncode:
                raise ValueError('Evidence contains private data: use a directory outside Git or an ignored private directory')
            break
    if path.exists() and any(path.iterdir()):
        raise ValueError('Output directory must be empty')
    return path


def resolve_partition(base, slot, exists):
    """Prefer the verified current slot; discover unslotted metadata explicitly."""
    if base not in {'boot', 'init_boot', 'vendor_boot', 'dtbo', 'vbmeta'} or slot not in {'_a', '_b'}:
        raise ValueError('Unexpected partition identity')
    names = [base + slot]
    if base in {'dtbo', 'vbmeta'}:
        names.append(base)
    for name in names:
        if exists(name):
            return name
    raise ValueError(f'No current-slot or unslotted partition found for {base}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True, help='Explicit adb serial; never selects an implicit device')
    parser.add_argument('--output', required=True, type=Path, help='Private local directory')
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--include-boot-images', action='store_true',
                        help='Read complete current-slot boot/init_boot/vendor_boot/dtbo/vbmeta for local recovery evidence')
    args = parser.parse_args()
    if not args.serial.strip() or any(char.isspace() for char in args.serial) or '\0' in args.serial:
        parser.error('Use one explicit, non-empty adb serial')
    out = private_destination(args.output)
    prop = lambda key: adb_read(args.adb, args.serial, 'shell', 'getprop', key).decode().strip()
    metadata = {key: prop(key) for key in [
        'ro.product.manufacturer', 'ro.product.model', 'ro.product.device',
        'ro.product.name', 'ro.build.fingerprint', 'ro.build.version.release',
        'ro.build.version.security_patch', 'ro.boot.slot_suffix', 'ro.boot.project_name',
        'ro.boot.verifiedbootstate', 'ro.boot.flash.locked', 'ro.soc.model']}
    model = metadata['ro.product.model'].lower().replace(' ', '')
    product = metadata['ro.product.device'].lower()
    known_models = {'pjz110', 'cph2649', 'cph2653', 'cph2655'}
    if not (model in known_models or model == 'oneplus13' or product == 'dodge'):
        raise SystemExit('Phone identity is not a recognized OnePlus 13; review before extending the allowlist')
    soc = metadata['ro.soc.model'].lower()
    if soc and '8750' not in soc:
        raise SystemExit('SoC identity does not match SM8750')
    slot = metadata['ro.boot.slot_suffix']
    if slot not in {'_a', '_b'}:
        raise SystemExit('Unexpected active slot; refusing to guess partition names')
    root_read = lambda command, timeout=30: adb_read(
        args.adb, args.serial, 'exec-out', 'su -c ' + shlex.quote(command), timeout=timeout)
    uid = root_read('id -u').decode().strip()
    if uid != '0':
        raise SystemExit('Stock root is required for FDT and boot evidence; no root changes are performed')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'android.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    fdt = root_read('cat /sys/firmware/fdt')
    if len(fdt) < 40 or fdt[:4] != bytes.fromhex('d00dfeed'):
        raise SystemExit('No valid runtime FDT returned; do not substitute a different device dump')
    size = int.from_bytes(fdt[4:8], 'big')
    if size > len(fdt) or size < 40:
        raise SystemExit('Runtime FDT is truncated')
    (out / 'runtime.dtb').write_bytes(fdt[:size])
    # Outputs remain private: bootconfig and logs can contain unique identifiers.
    for filename, command in [('cmdline.txt', 'cat /proc/cmdline'),
                              ('bootconfig.txt', 'cat /proc/bootconfig'),
                              ('meminfo.txt', 'cat /proc/meminfo'),
                              ('power-supply.txt', 'cat /sys/class/power_supply/battery/uevent'),
                              ('partitions.txt', 'ls -l /dev/block/by-name')]:
        (out / filename).write_bytes(root_read(command))
    partition_map = {}
    partition_sizes = {}
    exists = lambda name: root_read(f'test -e /dev/block/by-name/{name} && echo yes || echo no').decode().strip() == 'yes'
    for base in ['boot', 'init_boot', 'vendor_boot', 'dtbo', 'vbmeta']:
        name = resolve_partition(base, slot, exists)
        partition_map[base] = name
        assert re.fullmatch(r'[a-z_]+', name)
        capacity = root_read(f'blockdev --getsize64 /dev/block/by-name/{name}').decode().strip()
        if not re.fullmatch(r'[0-9]+', capacity) or int(capacity) < 8192:
            raise SystemExit(f'Invalid read-only partition capacity: {name}')
        partition_sizes[name] = int(capacity)
        count = '' if args.include_boot_images else ' count=2'
        image = root_read(f'dd if=/dev/block/by-name/{name} bs=4096{count} status=none', timeout=180)
        if not image:
            raise SystemExit(f'Empty partition evidence: {base}')
        if args.include_boot_images and len(image) != partition_sizes[name]:
            raise SystemExit(f'Incomplete partition image: {name}')
        (out / (base + ('.img' if args.include_boot_images else '.header.bin'))).write_bytes(image)
    (out / 'partition-sizes.txt').write_text(''.join(
        f'{name}\n{size}\n' for name, size in partition_sizes.items()), encoding='utf-8')
    files = {p.name: {'bytes': p.stat().st_size,
                     'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(out.iterdir()) if p.is_file()}
    (out / 'manifest.json').write_text(json.dumps({
        'scope': 'Read-only stock Android evidence; private, not for public upload',
        'full_boot_images': args.include_boot_images,
        'partition_map': partition_map,
        'partition_sizes': partition_sizes,
        'files': files}, indent=2) + '\n', encoding='utf-8')
    print('Private stock evidence collected. No device partition was written.')


if __name__ == '__main__':
    main()
