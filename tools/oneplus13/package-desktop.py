#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host-only PRIVATE RAM desktop packaging, never flashing or producing v4 install images."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from common import boot_sections, read_cpio, sha256
from desktop import PIN, derive
from native import build_initramfs
from stock import Fdt, boot_image

REPO = Path(__file__).resolve().parents[2]


def check_native_offline_evidence(kernel,desktop,evidence,helper_sha):
    if (kernel.get('command_budget_version') != 3 or
            kernel.get('fixed_phy_version') != 1 or kernel.get('host_power_resources_version') != 1 or
            kernel.get('dsi_batch_version') != 1 or
            desktop.get('qemu_native_preflight_rejects_public_root_verified') is not True or
            desktop.get('qemu_native_preflight_runtime_guards_verified') is not True or
            desktop.get('native_desktop_helper_sha256') != helper_sha or
            evidence.get('eligible_for_offline_native_packaging') is not True or
            evidence.get('hardware_tested') is not False or evidence.get('phone_operations') != 0 or
            evidence.get('kernel_commit') != kernel.get('integration_commit') or
            evidence.get('desktop_commit') != desktop.get('integration_commit') or
            evidence.get('rootfs_sha256') != desktop.get('rootfs_sha256') or
            evidence.get('native_desktop_helper_sha256') != helper_sha or
            any(evidence.get(key) is not True for key in ['kernel_download_verified',
                'clock_te_asan_ubsan_verified','native_preflight_arm64_tests_verified',
                'desktop_download_verified','actual_dtb_opp_budget_verified'])):
        raise ValueError('Matching reviewed native offline evidence required')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock', required=True, type=Path)
    p.add_argument('--artifacts', required=True, type=Path)
    p.add_argument('--desktop', required=True, type=Path)
    p.add_argument('--ufdt-tool', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--native-xml', type=Path)
    p.add_argument('--prepare-native-offline', action='store_true',
                   help='Prepare a reviewed private candidate only; never boot or flash')
    p.add_argument('--offline-evidence', type=Path)
    a = p.parse_args()
    if a.native_xml and not a.prepare_native_offline:
        p.error('Native desktop packaging is disabled: explicit activation, DSI '
                'batch scheduling and display resource takeover remain unvalidated; '
                'existing native desktop images are not eligible for testing.')
    if a.prepare_native_offline and (not a.native_xml or not a.offline_evidence):
        p.error('--prepare-native-offline requires private XML and reviewed offline evidence')
    spec = importlib.util.spec_from_file_location('collector', REPO/'tools/oneplus13/collect-stock.py')
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    out = collector.private_destination(a.output)
    m = json.loads((a.artifacts/'manifest.json').read_text())
    d = json.loads((a.desktop/'manifest.json').read_text())
    if a.native_xml:
        check_native_offline_evidence(m,d,json.loads(a.offline_evidence.read_text()),
                                     sha256(REPO/'devices/oneplus13/native-desktop.py'))
    if (m['integration_commit'] != PIN or m['hardware_tested'] is not False or
            d.get('kernel_integration_commit') != PIN or d.get('qemu_handoff_verified') is not True or
            d.get('qemu_http_chunk_fetch_verified') is not True or d.get('qemu_deadline_expiry_verified') is not True or
            d.get('kernel_release') != m['kernel_release'] or d.get('hardware_tested') is not False):
        raise ValueError('Matching kernel and passed desktop handoff evidence required')
    for name in ['Image.gz', 'initramfs.cpio.gz', 'modules.tar.gz']:
        if sha256(a.artifacts/name) != m['files'][name]['sha256']:
            raise ValueError('Kernel artifact mismatch: '+name)
    if sha256(a.desktop/'initramfs-desktop.cpio.gz') != d['initramfs_sha256']:
        raise ValueError('Desktop initramfs differs from QEMU-tested input')
    if not re.fullmatch('[0-9a-f]{64}', d['rootfs_sha256']) or not 1048576 <= d['rootfs_bytes'] <= 3*1024**3:
        raise ValueError('Unexpected rootfs metadata')
    cmdline = ('rdinit=/init panic=15 oneplus13.desktop=1 oneplus13.rootfs.sha256='+d['rootfs_sha256']+
               ' oneplus13.rootfs.bytes='+str(d['rootfs_bytes']))
    if (len(cmdline) > 256 or (a.desktop/'desktop-cmdline.txt').read_text().strip() != cmdline):
        raise ValueError('Desktop cmdline differs from the tested rootfs')
    s = json.loads((a.stock/'manifest.json').read_text())
    if s.get('full_boot_images') is not True:
        raise ValueError('Complete private recovery references required')
    for name in ['boot.img', 'init_boot.img', 'vendor_boot.img', 'dtbo.img', 'runtime.dtb']:
        info = s['files'][name]
        if (a.stock/name).stat().st_size != info['bytes'] or sha256(a.stock/name) != info['sha256']:
            raise ValueError('Private stock evidence changed: '+name)
    geometry = boot_image((a.stock/'vendor_boot.img').read_bytes())
    suffix = '-native' if a.native_xml else ''
    dtb_name = 'sm8750-oneplus-dodge'+suffix+'.dtb'
    if sha256(a.artifacts/dtb_name) != m['files'][dtb_name]['sha256']:
        raise ValueError('DTB input mismatch')
    tree = Fdt((a.artifacts/dtb_name).read_bytes())
    tree.nodes['/chosen']['bootargs'] = cmdline.encode()+b'\0'
    original = (a.artifacts/'initramfs.cpio.gz').read_bytes()
    native_report = None
    if a.native_xml:
        helper = REPO/'devices/oneplus13/start-native-display'
        if sha256(helper) != m['phone_inputs_sha256']['start-native-display']:
            raise ValueError('Native helper changed since the compiled kernel')
        original, native_report = build_initramfs(original, (a.artifacts/'modules.tar.gz').read_bytes(),
                                                m['kernel_release'], a.native_xml.read_bytes(), helper.read_bytes())
    packed, archive_report = derive(original)
    generic = read_cpio(gzip.decompress((a.desktop/'initramfs-desktop.cpio.gz').read_bytes()))
    private = read_cpio(gzip.decompress(packed))
    if any(private[name] != value for name, value in generic.items()):
        raise ValueError('Private candidate differs from the tested public control flow')
    out.mkdir(parents=True, exist_ok=True)
    (out/'desktop-mainline-input.dtb').write_bytes(tree.to_blob())
    subprocess.run([sys.executable, str(REPO/'tools/oneplus13/calibrate.py'), '--stock', str(a.stock),
                    '--ufdt-tool', str(a.ufdt_tool), '--mainline-dtb', str(out/'desktop-mainline-input.dtb'),
                    '--use-runtime-memory', '--output', str(out/'calibration')], check=True)
    cal = out/'calibration/sm8750-oneplus-dodge-abl.dtb'
    if Fdt(cal.read_bytes()).text('/chosen', 'bootargs') != [cmdline]:
        raise ValueError('Calibrated DTB lost desktop bootargs')
    (out/'initramfs-desktop.cpio.gz').write_bytes(packed)
    pin = json.loads((REPO/'kernel/source.json').read_text())['tool_inputs']['mkbootimg.py']
    archive = REPO/pin['bundled_archive']
    if sha256(archive) != pin['archive_sha256']:
        raise ValueError('Pinned boot image tool archive mismatch')
    image = out/'boot-desktop-ram-experimental.img'
    with tempfile.TemporaryDirectory(prefix='oneplus13-desktop-pack-') as temp:
        with tarfile.open(archive) as source:
            source.extractall(temp, filter='data')
        mkboot = Path(temp)/'mkbootimg.py'
        if sha256(mkboot) != pin['sha256']:
            raise ValueError('Pinned boot image tool mismatch')
        subprocess.run([sys.executable, str(mkboot), '--header_version', '2', '--pagesize', '4096', '--base', '0',
                        '--kernel_offset', str(geometry['kernel_addr']), '--ramdisk_offset', str(geometry['ramdisk_addr']),
                        '--tags_offset', str(geometry['tags_addr']), '--dtb_offset', str(geometry['dtb_addr']),
                        '--kernel', str(a.artifacts/'Image.gz'), '--ramdisk', str(out/'initramfs-desktop.cpio.gz'),
                        '--dtb', str(cal), '--cmdline', cmdline, '--output', str(image)], check=True)
    if boot_sections(image.read_bytes()) != {'kernel': (a.artifacts/'Image.gz').read_bytes(),
                                             'ramdisk': packed, 'dtb': cal.read_bytes()}:
        raise ValueError('RAM image independent section readback failed')
    blob = image.read_bytes()
    if (blob[64:576]+blob[608:1632]).split(b'\0', 1)[0].decode() != cmdline:
        raise ValueError('RAM header cmdline differs from the calibrated DTB')
    report = {'scope': 'PRIVATE_RAM_DESKTOP_NOT_PHONE_BOOTED_NEVER_FLASH', 'hardware_tested': False,
              'kernel_integration_commit': PIN, 'desktop_integration_commit': d['integration_commit'],
              'rootfs_sha256': d['rootfs_sha256'], 'rootfs_bytes': d['rootfs_bytes'],
              'cmdline': cmdline, 'boot_image_sha256': sha256(image), 'boot_image_bytes': image.stat().st_size,
              'qemu_public_control_flow_identical': True, 'qemu_this_private_archive': False,
              'qemu_public_http_fetch_and_deadline_verified': True,
              'contains_private_spr_profile': bool(a.native_xml), 'native': native_report,
              'archive': archive_report, 'phone_operations': 0, 'partition_writes': 0,
              'actual_abl_load_and_recovery_unverified': True}
    report.update(offline_native_prepared_only=bool(a.native_xml),
                  explicit_native_activation_executed=False,hardware_test_authorized=False)
    (out/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    (out/'DO-NOT-FLASH.txt').write_text('PRIVATE RAM desktop candidate. Not phone booted. Never flash.\n')
    (out/'SHA256SUMS').write_text(''.join(f'{sha256(f)}  {f.relative_to(out).as_posix()}\n'
                                        for f in sorted(out.rglob('*')) if f.is_file() and f.name != 'SHA256SUMS'))
    print(json.dumps({k: report[k] for k in ['scope', 'boot_image_sha256', 'boot_image_bytes', 'phone_operations']}, indent=2))


if __name__ == '__main__':
    main()
