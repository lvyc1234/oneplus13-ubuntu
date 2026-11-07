#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Create an UNTESTED Android-v4 mainline boot triplet on the host only.

Requires the owner's private full stock reads and a passed offline calibration.
Does not connect to a phone, sign/modify vbmeta, write partitions, or install.
Original Android images and command lines are not included in the output.
"""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile

from common import boot_sections, make_cpio, read_cpio, sha256
from stock import Fdt, boot_image, bounded

REPO = Path(__file__).resolve().parents[2]


def verify_triplet(out, kernel, ramdisk, dtb, capacities):
    parsed = {name: boot_image((out / (name + '.img')).read_bytes())
              for name in ('boot', 'init_boot', 'vendor_boot')}
    for name, info in parsed.items():
        if info['header_version'] != 4 or info['page_size'] != 4096:
            raise ValueError('Incorrect phone boot-chain format')
        if info['partition_bytes'] > capacities[name]:
            raise ValueError('Candidate exceeds actual phone partition: ' + name)
    expected = [('boot', 'kernel', kernel), ('vendor_boot', 'vendor_ramdisk', ramdisk),
                ('vendor_boot', 'dtb', dtb)]
    for image, section, value in expected:
        blob = (out / (image + '.img')).read_bytes()
        off, size = parsed[image]['sections'][section]
        if bounded(blob, off, size) != value:
            raise ValueError('Candidate section differs from input: ' + image + ':' + section)
    if parsed['boot']['sections']['ramdisk'][1] or parsed['init_boot']['sections']['kernel'][1]:
        raise ValueError('Wrong kernel/generic ramdisk placement')
    init_image = (out / 'init_boot.img').read_bytes()
    off, size = parsed['init_boot']['sections']['ramdisk']
    if read_cpio(gzip.decompress(bounded(init_image, off, size))):
        raise ValueError('Generic ramdisk must not override the diagnostic initramfs')
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', required=True, type=Path)
    parser.add_argument('--calibration', required=True, type=Path)
    parser.add_argument('--artifacts', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--native-xml', type=Path,
                        help='PRIVATE AA569 XML: derive a separate manual-load native RAM candidate')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('collector', Path(__file__).with_name('collect-stock.py'))
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    out = collector.private_destination(args.output)
    stock_manifest = json.loads((args.stock / 'manifest.json').read_text())
    if stock_manifest.get('full_boot_images') is not True:
        raise ValueError('Full current-slot Android images are required; header reads are insufficient')
    capacities = {}
    observed = stock_manifest.get('partition_sizes')
    if observed is None:
        sizes_text = (args.stock / 'partition-sizes.txt').read_text().splitlines()
        if len(sizes_text) % 2:
            raise ValueError('Truncated legacy partition capacity record')
        observed = dict(zip(sizes_text[::2], map(int, sizes_text[1::2])))
    for name in ('boot', 'init_boot', 'vendor_boot'):
        image = args.stock / (name + '.img')
        expected = stock_manifest['files'][image.name]
        if image.stat().st_size != expected['bytes'] or sha256(image) != expected['sha256']:
            raise ValueError('Private Android backup changed: ' + name)
        capacities[name] = observed[stock_manifest['partition_map'][name]]
        if image.stat().st_size != capacities[name] or boot_image(image.read_bytes())['header_version'] != 4:
            raise ValueError('Incomplete or incompatible phone backup: ' + name)
    report = json.loads((args.calibration / 'calibration.json').read_text())
    dtb_path = args.calibration / 'sm8750-oneplus-dodge-abl.dtb'
    if report.get('status') != 'OFFLINE_STOCK_CONTROL_ONLY' or not report.get('stock_control'):
        raise ValueError('Missing positive stock control')
    if sha256(dtb_path) != report['candidate']['sha256']:
        raise ValueError('Calibrated DTB differs from verified output')
    if len(report['candidate']['all_overlay_merged_sha256']) != report['overlay_count']:
        raise ValueError('Not every stock overlay passed offline isolation')
    if sha256(args.stock / 'runtime.dtb') != report['runtime_sha256']:
        raise ValueError('Calibration belongs to another stock snapshot')
    manifest = json.loads((args.artifacts / 'manifest.json').read_text())
    if manifest.get('device') != 'oneplus,dodge' or manifest.get('hardware_tested') is not False:
        raise ValueError('Unexpected kernel artifact identity')
    for name in ('Image.gz', 'initramfs.cpio.gz'):
        if sha256(args.artifacts / name) != manifest['files'][name]['sha256']:
            raise ValueError('Kernel/initramfs artifact hash mismatch')
    kernel = (args.artifacts / 'Image.gz').read_bytes()
    image = gzip.decompress(kernel)
    if len(image) < 64 or image[56:60] != b'ARM\x64':
        raise ValueError('Not an ARM64 Image')
    ramdisk = (args.artifacts / 'initramfs.cpio.gz').read_bytes()
    init = read_cpio(gzip.decompress(ramdisk))
    if 'init' not in init or 'dev/console' not in init:
        raise ValueError('Missing diagnostic init/console')
    dtb = dtb_path.read_bytes()
    Fdt(dtb)
    native_report = None
    if args.native_xml:
        from native import build_initramfs
        spec = importlib.util.spec_from_file_location('native_dtb_check', Path(__file__).with_name('check-native-dtb.py'))
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        native_tree_report = check.inspect(dtb)
        if manifest.get('native_display_candidate_version') != 1 or manifest.get('spr_runtime_version') != 1:
            raise ValueError('Native candidate requires the corresponding compiled runtime/DTB build')
        if report['candidate'].get('mainline_input_sha256') != manifest['files']['sm8750-oneplus-dodge-native.dtb']['sha256']:
            raise ValueError('Native calibration must originate from this build\'s native DTB')
        module_archive = args.artifacts / 'modules.tar.gz'
        if sha256(module_archive) != manifest['files']['modules.tar.gz']['sha256']:
            raise ValueError('Native module archive hash mismatch')
        helper = REPO/'devices/oneplus13/start-native-display'
        if sha256(helper) != manifest['phone_inputs_sha256'].get('start-native-display'):
            raise ValueError('Native helper differs from the compiled integration inputs')
        ramdisk, native_report = build_initramfs(ramdisk, module_archive.read_bytes(),
            manifest['kernel_release'], args.native_xml.read_bytes(),
            helper.read_bytes())
        native_report['dtb_checks'] = native_tree_report
    stock_geometry = boot_image((args.stock / 'vendor_boot.img').read_bytes())
    pin = json.loads((REPO / 'kernel/source.json').read_text())['tool_inputs']['mkbootimg.py']
    archive = REPO / pin['bundled_archive']
    if sha256(archive) != pin['archive_sha256']:
        raise ValueError('mkbootimg archive differs from the pinned tool')
    out.mkdir(parents=True, exist_ok=True)
    ramdisk_path = args.artifacts / 'initramfs.cpio.gz'
    if native_report:
        ramdisk_path = out / 'initramfs.cpio.gz'
        ramdisk_path.write_bytes(ramdisk)
        (out/'native-initramfs-verification.json').write_text(json.dumps(native_report, indent=2)+'\n')
    cmdline = 'rdinit=/init panic=15 oneplus13.watchdog=300'
    with tempfile.TemporaryDirectory(prefix='oneplus13-pack-') as temp:
        tmp = Path(temp)
        with tarfile.open(archive) as package:
            package.extractall(tmp / 'mkbootimg', filter='data')
        mkboot = tmp / 'mkbootimg/mkbootimg.py'
        if sha256(mkboot) != pin['sha256']:
            raise ValueError('mkbootimg script hash mismatch')
        (tmp / 'empty.cpio.gz').write_bytes(gzip.compress(make_cpio([]), mtime=0))
        tool = [sys.executable, str(mkboot), '--header_version', '4']
        subprocess.run(tool + ['--kernel', str(args.artifacts / 'Image.gz'), '--cmdline', cmdline,
                              '--output', str(out / 'boot.img')], check=True)
        subprocess.run(tool + ['--ramdisk', str(tmp / 'empty.cpio.gz'),
                              '--output', str(out / 'init_boot.img')], check=True)
        subprocess.run(tool + ['--pagesize', '4096', '--base', '0',
                              '--kernel_offset', str(stock_geometry['kernel_addr']),
                              '--ramdisk_offset', str(stock_geometry['ramdisk_addr']),
                              '--tags_offset', str(stock_geometry['tags_addr']),
                              '--dtb_offset', str(stock_geometry['dtb_addr']),
                              '--vendor_ramdisk', str(ramdisk_path),
                              '--dtb', str(dtb_path), '--vendor_boot', str(out / 'vendor_boot.img')], check=True)
        # Separate single-image RAM-format candidate. Its actual ABL load and
        # hypervisor behavior still require an authorized temporary boot test.
        subprocess.run([sys.executable, str(mkboot), '--header_version', '2',
                        '--pagesize', '4096', '--base', '0',
                        '--kernel_offset', str(stock_geometry['kernel_addr']),
                        '--ramdisk_offset', str(stock_geometry['ramdisk_addr']),
                        '--tags_offset', str(stock_geometry['tags_addr']),
                        '--dtb_offset', str(stock_geometry['dtb_addr']),
                        '--kernel', str(args.artifacts / 'Image.gz'),
                        '--ramdisk', str(ramdisk_path),
                        '--dtb', str(dtb_path), '--cmdline', cmdline,
                        '--output', str(out / 'boot-calibrated-ram-experimental.img')], check=True)
    checked = verify_triplet(out, kernel, ramdisk, dtb, capacities)
    ram_sections = boot_sections((out / 'boot-calibrated-ram-experimental.img').read_bytes())
    if ram_sections != {'kernel': kernel, 'ramdisk': ramdisk, 'dtb': dtb}:
        raise ValueError('Calibrated RAM image section roundtrip failed')
    (out / 'DO-NOT-FLASH.txt').write_text(
        'OFFLINE CANDIDATE ONLY. Not booted on the phone. No AVB signature or vbmeta changes.\n'
        'This is a diagnostic RAM environment, not an installed GNOME system.\n'
        'Bootloader acceptance, recovery and native display/touch require separate hardware tests.\n', encoding='utf-8')
    (out / 'manifest.json').write_text(json.dumps({
        'status': 'UNSIGNED_V4_DIAGNOSTIC_CANDIDATE_NOT_HARDWARE_TESTED',
        'hardware_tested': False, 'kernel_integration_commit': manifest['integration_commit'],
        'native_display_candidate': bool(native_report),
        'contains_private_calibration': bool(native_report),
        'kernel_release': manifest['kernel_release'], 'cmdline': cmdline,
        'actual_partition_capacities': capacities, 'geometry': checked,
        'ram_candidate': {'sha256': sha256(out / 'boot-calibrated-ram-experimental.img'),
                          'header_version': 2, 'actual_abl_load_hypervisor_unverified': True},
        'calibration_sha256': sha256(args.calibration / 'calibration.json'),
        'mkbootimg_sha256': pin['sha256']}, indent=2) + '\n', encoding='utf-8')
    (out / 'SHA256SUMS').write_text(''.join(f'{sha256(p)}  {p.name}\n'
        for p in sorted(out.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'), encoding='utf-8')
    print('Offline v4 triplet packed and independently read back. NOT hardware tested; no device operation.')


if __name__ == '__main__':
    main()
