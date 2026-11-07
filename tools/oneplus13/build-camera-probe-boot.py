#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a separate manual camera chip-ID RAM boot; preserve existing images."""
import argparse,hashlib,importlib,json,secrets,subprocess,sys,tarfile,tempfile
from pathlib import Path
from common import boot_sections,sha256
from stock import Fdt
REPO=Path(__file__).resolve().parents[2]
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel',required=True,type=Path);p.add_argument('--image',required=True,type=Path)
    a=p.parse_args();kernel=a.kernel.resolve();out=a.image.resolve()
    original=json.loads((out/'manifest.json').read_text())
    payload=original.get('experimental_microphone_files',{})
    expected={'usr/libexec/oneplus13-ram-microphone-check.py','usr/libexec/oneplus13-ram-acoustic-check.py',
              'usr/lib/firmware/qcom/sm8750/OnePlus13-Audio-Probe-tplg.bin'}
    if set(payload)!=expected or original.get('microphone_auto_started') is not False:
        raise ValueError('Missing source-built manual microphone payload')
    camera_runtime=original.get('camera_probe_runtime_files',{})
    if set(camera_runtime)!={'usr/libexec/oneplus13-ram-camera-chip-id.py'} or original.get('camera_probe_auto_started') is not False:
        raise ValueError('Manual source-built camera helper missing')
    for name,digest in camera_runtime.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(out/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Camera runtime changed')
    policy=subprocess.check_output(['unsquashfs','-cat',str(out/'rootfs.squashfs'),
        'etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf'],text=True)
    for module in ('s5kjn5','i2c_qcom_cci'):
        if 'install '+module+' /bin/false\n' not in policy:
            raise ValueError('Manual camera module policy missing: '+module)
    with tarfile.open(kernel/'modules.tar.gz') as archive:
        for suffix in ('/kernel/drivers/media/i2c/s5kjn5.ko','/kernel/drivers/i2c/busses/i2c-qcom-cci.ko'):
            if sum(name.endswith(suffix) for name in archive.getnames())!=1:
                raise ValueError('Required source-built camera module missing')
    for name,digest in payload.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(out/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Changed microphone runtime input')
    km=json.loads((kernel/'manifest.json').read_text())
    if original['source_commit']!=km['integration_commit']:raise ValueError('Different source components')
    for path,key in [('rootfs.squashfs','rootfs_sha256'),('boot-desktop-ram-experimental.img','boot_image_sha256')]:
        if sha256(out/path)!=original[key]:raise ValueError('Changed complete image')
    sections=boot_sections((out/'boot-desktop-ram-experimental.img').read_bytes())
    if sections['kernel']!=(kernel/'Image.gz').read_bytes():raise ValueError('Changed original kernel')
    audio=importlib.import_module('camera-probe-dtb')
    dtb,report=audio.derive((REPO/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    nonce=secrets.token_hex(12)
    cmdline=original['cmdline'].replace('oneplus13.session='+original['session_identity_nonce'],'oneplus13.session='+nonce)
    cmdline+=' oneplus13.audio_probe=1 oneplus13.camera_probe=1'
    if len(cmdline.encode())>1536:raise ValueError('Probe bootargs exceed header')
    tree=Fdt(dtb);tree.nodes['/chosen']['bootargs']=(cmdline+'\0').encode();dtb=tree.to_blob()
    paths={name:out/name for name in ('boot-camera-chip-id-ram-experimental.img','sm8750-oneplus13-camera-chip-id-probe.dtb','camera-probe-manifest.json','camera-probe-dtb-review.json')}
    if any(path.exists() for path in paths.values()):raise ValueError('Preserve previous microphone candidate')
    dpath=paths['sm8750-oneplus13-camera-chip-id-probe.dtb'];dpath.write_bytes(dtb)
    image=paths['boot-camera-chip-id-ram-experimental.img']
    lock=json.loads((REPO/'kernel/source.json').read_text())['tool_inputs']['mkbootimg.py']
    archive=REPO/lock['bundled_archive']
    if sha256(archive)!=lock['archive_sha256']:raise ValueError('Boot packer archive changed')
    geo=json.loads((REPO/'devices/oneplus13/qualified-data/boot-geometry.json').read_text())
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive) as tf:tf.extractall(tmp,filter='data')
        mk=Path(tmp)/'mkbootimg.py'
        if sha256(mk)!=lock['sha256']:raise ValueError('Boot packer changed')
        ramdisk=Path(tmp)/'ramdisk.cpio.gz';ramdisk.write_bytes(sections['ramdisk'])
        subprocess.run([sys.executable,str(mk),'--header_version','2','--pagesize','4096','--base','0',
            '--kernel_offset',str(geo['kernel_addr']),'--ramdisk_offset',str(geo['ramdisk_addr']),
            '--tags_offset',str(geo['tags_addr']),'--dtb_offset',str(geo['dtb_addr']),
            '--kernel',str(kernel/'Image.gz'),'--ramdisk',str(ramdisk),'--dtb',str(dpath),
            '--cmdline',cmdline,'--output',str(image)],check=True)
    if boot_sections(image.read_bytes())!={**sections,'dtb':dtb}:raise ValueError('Independent microphone boot readback failed')
    result={**original,'scope':'SEPARATE_MANUAL_CAMERA_CHIP_ID_RAM_BOOT_NEVER_FLASH',
        'cmdline':cmdline,'session_identity_nonce':nonce,'boot_image_sha256':sha256(image),
        'boot_image_bytes':image.stat().st_size,'boot_image_file':image.name,'dtb_file':dpath.name,
        'dtb_sha256':sha256(dpath),'audio_probe_only':False,'camera_chip_id_probe_only':True,'camera_capture_qualified':False,'default_boot_image_sha256':original['boot_image_sha256'],
        'microphone_hardware_qualified':False,'speaker_audibility_qualified':False}
    paths['camera-probe-dtb-review.json'].write_text(json.dumps(report,indent=2)+'\n')
    paths['camera-probe-manifest.json'].write_text(json.dumps(result,indent=2)+'\n')
    current={**original,'camera_probe_artifacts':{n:sha256(p) for n,p in paths.items()}}
    (out/'manifest.json').write_text(json.dumps(current,indent=2)+'\n')
    print(json.dumps({'scope':result['scope'],'source_commit':result['source_commit'],
        'boot_image_sha256':result['boot_image_sha256'],'default_speaker_boot_preserved':True}))
if __name__=='__main__':main()
