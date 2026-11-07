#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a separate manual Iris video RAM boot from the complete image."""
import argparse, hashlib, importlib, json, secrets, subprocess, sys, tarfile, tempfile
from pathlib import Path
from common import boot_sections, sha256
from stock import Fdt

REPO = Path(__file__).resolve().parents[2]
VIDEO_FIRMWARE = 'usr/lib/firmware/qcom/sm8750/oneplus/dodge/vpu35_4v.mbn'
VIDEO_FIRMWARE_SHA256 = 'e1b2cb6de867893537fe6a1ffba312c2a2949402ca9892d393e2f40de255690a'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', required=True, type=Path)
    parser.add_argument('--image', required=True, type=Path)
    args = parser.parse_args()
    kernel = args.kernel.resolve()
    out = args.image.resolve()
    original = json.loads((out / 'manifest.json').read_text())
    if len(original.get('video_probe_runtime_files', {})) != 5 or original.get('video_probe_auto_started') is not False:
        raise ValueError('Missing manual source-built video runtime')
    firmware = json.loads(subprocess.check_output([
        'unsquashfs', '-cat', str(out / 'rootfs.squashfs'),
        'usr/lib/oneplus13-desktop/private-firmware.json'], text=True))
    if firmware.get('qcom/sm8750/oneplus/dodge/vpu35_4v.mbn') != VIDEO_FIRMWARE_SHA256:
        raise ValueError('Phone Iris firmware is absent or changed')
    raw = subprocess.check_output(['unsquashfs', '-cat', str(out / 'rootfs.squashfs'), VIDEO_FIRMWARE])
    if len(raw) != 2417352 or hashlib.sha256(raw).hexdigest() != VIDEO_FIRMWARE_SHA256:
        raise ValueError('Phone Iris firmware readback mismatch')
    policy = subprocess.check_output(['unsquashfs', '-cat', str(out / 'rootfs.squashfs'),
        'etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf'], text=True)
    if 'install qcom_iris /bin/false\n' not in policy:
        raise ValueError('Iris module must require explicit activation')
    with tarfile.open(kernel / 'modules.tar.gz') as modules:
        for suffix in ('/kernel/drivers/media/platform/qcom/iris/qcom-iris.ko',
                       '/kernel/drivers/clk/qcom/videocc-sm8750.ko'):
            if sum(name.endswith(suffix) for name in modules.getnames()) != 1:
                raise ValueError('Source-built video prerequisite missing: ' + suffix)
    km = json.loads((kernel / 'manifest.json').read_text())
    if original['source_commit'] != km['integration_commit']:
        raise ValueError('Different source components')
    for path, key in [('rootfs.squashfs', 'rootfs_sha256'),
                      ('boot-desktop-ram-experimental.img', 'boot_image_sha256')]:
        if sha256(out / path) != original[key]:
            raise ValueError('Changed complete image')
    sections = boot_sections((out / 'boot-desktop-ram-experimental.img').read_bytes())
    if sections['kernel'] != (kernel / 'Image.gz').read_bytes():
        raise ValueError('Changed original kernel')
    video = importlib.import_module('video-probe-dtb')
    dtb, report = video.derive((REPO / 'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    nonce = secrets.token_hex(12)
    cmdline = original['cmdline'].replace(
        'oneplus13.session=' + original['session_identity_nonce'],
        'oneplus13.session=' + nonce)
    cmdline += ' oneplus13.audio_probe=1 oneplus13.camera_probe=1 oneplus13.video_probe=1'
    if len(cmdline.encode()) > 1536:
        raise ValueError('Video probe bootargs exceed header')
    tree = Fdt(dtb)
    tree.nodes['/chosen']['bootargs'] = (cmdline + '\0').encode()
    dtb = tree.to_blob()
    names = ('boot-video-ram-experimental.img', 'sm8750-oneplus13-video-probe.dtb',
             'video-probe-manifest.json', 'video-probe-dtb-review.json')
    paths = {name: out / name for name in names}
    if any(path.exists() for path in paths.values()):
        raise ValueError('Preserve previous image candidates')
    dpath = paths['sm8750-oneplus13-video-probe.dtb']
    dpath.write_bytes(dtb)
    image = paths['boot-video-ram-experimental.img']
    lock = json.loads((REPO / 'kernel/source.json').read_text())['tool_inputs']['mkbootimg.py']
    archive = REPO / lock['bundled_archive']
    if sha256(archive) != lock['archive_sha256']:
        raise ValueError('Boot packer archive changed')
    geo = json.loads((REPO / 'devices/oneplus13/qualified-data/boot-geometry.json').read_text())
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive) as tf:
            tf.extractall(tmp, filter='data')
        mk = Path(tmp) / 'mkbootimg.py'
        if sha256(mk) != lock['sha256']:
            raise ValueError('Boot packer changed')
        ramdisk = Path(tmp) / 'ramdisk.cpio.gz'
        ramdisk.write_bytes(sections['ramdisk'])
        subprocess.run([sys.executable, str(mk), '--header_version', '2', '--pagesize', '4096',
                        '--base', '0', '--kernel_offset', str(geo['kernel_addr']),
                        '--ramdisk_offset', str(geo['ramdisk_addr']), '--tags_offset', str(geo['tags_addr']),
                        '--dtb_offset', str(geo['dtb_addr']), '--kernel', str(kernel / 'Image.gz'),
                        '--ramdisk', str(ramdisk), '--dtb', str(dpath), '--cmdline', cmdline,
                        '--output', str(image)], check=True)
    if boot_sections(image.read_bytes()) != {**sections, 'dtb': dtb}:
        raise ValueError('Independent video boot readback failed')
    result = {**original, 'scope': 'SEPARATE_MANUAL_IRIS_VIDEO_RAM_BOOT_NEVER_FLASH',
              'cmdline': cmdline, 'session_identity_nonce': nonce,
              'boot_image_sha256': sha256(image), 'boot_image_bytes': image.stat().st_size,
              'boot_image_file': image.name, 'dtb_file': dpath.name, 'dtb_sha256': sha256(dpath),
              'default_boot_image_sha256': original['boot_image_sha256'],
              'video_firmware': VIDEO_FIRMWARE, 'video_firmware_sha256': VIDEO_FIRMWARE_SHA256,
              'firmware_authentication_qualified': False, 'hardware_decode_qualified': False,
              'camera_capture_qualified': False}
    paths['video-probe-dtb-review.json'].write_text(json.dumps(report, indent=2) + '\n')
    paths['video-probe-manifest.json'].write_text(json.dumps(result, indent=2) + '\n')
    current = {**original, 'video_probe_artifacts': {name: sha256(path) for name, path in paths.items()},
               'video_probe_auto_started': False, 'video_hardware_decode_qualified': False}
    (out / 'manifest.json').write_text(json.dumps(current, indent=2) + '\n')
    print(json.dumps({'scope': result['scope'], 'source_commit': result['source_commit'],
                      'boot_image_sha256': result['boot_image_sha256'],
                      'default_boot_image_preserved': True}))


if __name__ == '__main__':
    main()
