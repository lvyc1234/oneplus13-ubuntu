#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Assemble THIS checkout's fresh kernel, GNOME and Mesa, without old images.

Private firmware/calibration and owner network configuration are local inputs
to the separate RAM packaging step, never to the public CI build.
"""
import argparse, importlib.util, json, os, re, shutil, subprocess, tarfile
from pathlib import Path
from common import sha256

REPO = Path(__file__).resolve().parents[2]

def run(args, timeout=240):
    result = subprocess.run(list(map(str,args)), capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Source assembly failed: '+str(args[:2])+'\n'+result.stdout[-2000:]+result.stderr[-2000:])
    return result.stdout

def verified(directory, commit):
    value=json.loads((directory/'manifest.json').read_text())
    if value['integration_commit']!=commit or value['hardware_tested'] is not False:
        raise ValueError('Input must be built at this source commit')
    for name, item in value['files'].items():
        if Path(name).name!=name or sha256(directory/name)!=item['sha256'] or (directory/name).stat().st_size!=item['size']:
            raise ValueError('Input manifest size/hash mismatch: '+name)
    return value

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['base','kernel','mesa','output']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if os.geteuid()!=0 or os.uname().machine!='aarch64':raise ValueError('Disposable native ARM64 root build required')
    commit=run(['git','rev-parse','HEAD']).strip()
    base,kernel,mesa,out=(getattr(a,n).resolve() for n in ['base','kernel','mesa','output'])
    if out.exists():raise ValueError('Fresh output required')
    bm=verified(base,commit);km=verified(kernel,commit);release=km['kernel_release']
    if not re.fullmatch('[A-Za-z0-9._+-]{1,160}',release):raise ValueError('Invalid kernel release')
    debs=list(mesa.glob('erhai-mesa_*.deb'))
    if len(debs)!=1:raise ValueError('Exactly one freshly built Mesa package required')
    deb=debs[0]
    # Retain all artifacts of this build; no historical SHA substituted.
    out.mkdir(parents=True);root=REPO/'out/oneplus13-source-stage'
    if root.exists():raise ValueError('Fresh assembly staging tree required')
    run(['unsquashfs','-processors','2','-no-progress','-d',root,base/'rootfs.squashfs'])
    if not (root/'lib').is_symlink() or (root/'lib').readlink()!=Path('usr/lib'):raise ValueError('Merged /usr required')
    with tarfile.open(kernel/'modules.tar.gz') as tf:
        for item in tf:
            name=item.name.removeprefix('./')
            if '..' in Path(name).parts or Path(name).is_absolute():raise ValueError('Unsafe module archive')
            if name not in {'.','lib','lib/modules','lib/modules/'+release} and not name.startswith('lib/modules/'+release+'/'):
                raise ValueError('Foreign module release')
            if not (item.isfile() or item.isdir()):raise ValueError('Unexpected module archive member')
        temp=REPO/'out/source-matching-modules';temp.mkdir()
        tf.extractall(temp,filter='data')
    shutil.copytree(temp/'lib/modules',root/'usr/lib/modules',dirs_exist_ok=True)
    (root/'usr/lib/oneplus13-desktop/kernel-release').write_text(release+'\n')
    run(['dpkg-deb','-x',deb,root])
    bt=REPO/'packaging/erhai-device-support/tree/usr/sbin/erhai-bt-address'
    shutil.copyfile(bt,root/'usr/sbin/erhai-bt-address');(root/'usr/sbin/erhai-bt-address').chmod(0o755)
    # Keep radio/manual bring-up after firmware and the owner RAM guards.
    # Blacklist aliases only for previously qualified radio/touch entry points;
    # explicit bounded activation is performed by the local RAM orchestrator.
    spec=importlib.util.spec_from_file_location('policy',REPO/'tools/oneplus13/ram-module-policy.py')
    policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
    deps=(root/'usr/lib/modules'/release/'modules.dep').read_text()
    paths=['lib/modules/'+release+'/'+s.split(':')[0] for s in deps.splitlines() if s]
    blocked=set(policy.dependency_closure(policy.blocked_modules(paths,release),deps))
    allowed=set(policy.dependency_closure(
        ['ath12k_wifi7','pwrseq_qcom_wcn','pci_pwrctrl_pwrseq','nxp_nci_i2c'],
        deps,
    ))
    blocked-=allowed
    (root/'etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf').write_text(policy.configuration(sorted(blocked)))
    (root/'etc/modprobe.d/92-oneplus13-manual-bringup.conf').write_text(''.join('blacklist '+n+'\n' for n in sorted(allowed|{'dodge_s3910','hci_uart'})))
    timer=root/'usr/lib/systemd/system/oneplus13-ram-deadline.timer'
    text=timer.read_text()
    if text.count('OnBootSec=900s\n')!=1:raise ValueError('Unexpected deadline source')
    timer.write_text(text.replace('OnBootSec=900s\n','OnBootSec=300s\n'))
    checks={}
    commands={
       'gnome':['/usr/bin/gnome-shell','--version'],
       'supplicant':['/usr/sbin/wpa_supplicant','-v'],
       'bt-address':['/usr/sbin/erhai-bt-address','--help'],
       'gpu-vermagic':['/usr/sbin/modinfo','-k',release,'-F','vermagic','msm'],
       'touch-vermagic':['/usr/sbin/modinfo','-k',release,'-F','vermagic','dodge_s3910'],
       'wlan-vermagic':['/usr/sbin/modinfo','-k',release,'-F','vermagic','ath12k_wifi7'],
       'mesa-closure':['/usr/bin/env','LD_LIBRARY_PATH=/usr/lib/erhai/mesa/lib','ldd','/usr/lib/erhai/mesa/lib/libEGL_mesa.so.0'],
       'gallium-closure':['/usr/bin/env','LD_LIBRARY_PATH=/usr/lib/erhai/mesa/lib','ldd','/usr/lib/erhai/mesa/lib/dri/msm_dri.so']}
    for name,args in commands.items():
        checks[name]=run(['chroot',root,*args])
        if 'not found' in checks[name]:raise ValueError('Missing runtime dependency: '+name)
        if name.endswith('vermagic') and release not in checks[name]:raise ValueError('Module mismatch')
    for name in ['usr/bin/bluetooth-sendto','usr/libexec/bluetooth/obexd','usr/bin/gnome-control-center','usr/bin/nmcli','usr/sbin/iw']:
        if not (root/name).is_file():raise ValueError('Missing preserved feature: '+name)
    for module in ('nfc','nci','nxp_nci','nxp_nci_i2c'):
        if 'install '+module+' /bin/false\n' in (root/'etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf').read_text():
            raise ValueError('Reviewed NFC module remains denied: '+module)
    if (root/'etc/machine-id').read_bytes():raise ValueError('Machine identity in public image')
    image=out/'rootfs.squashfs'
    epoch=run(['git','show','-s','--format=%ct','HEAD']).strip()
    run(['mksquashfs',root,image,'-noappend','-comp','zstd','-processors','2','-mkfs-time',epoch,'-all-time',epoch,'-no-progress'])
    value={'scope':'FRESH_SOURCE_IMAGE_MATCHED_KERNEL','integration_commit':commit,'kernel_release':release,
        'rootfs_sha256':sha256(image),'rootfs_bytes':image.stat().st_size,
        'base_rootfs_sha256':sha256(base/'rootfs.squashfs'),'kernel_sha256':sha256(kernel/'Image'),
        'modules_sha256':sha256(kernel/'modules.tar.gz'),'mesa_sha256':sha256(deb),
        'old_actions_artifacts_used':False,'private_inputs_in_public_image':False,'hardware_tested':False,
        'runtime_deadline_seconds':300,'desktop_profile':json.loads((base/'manifest.json').read_text()).get('desktop_profile','full'),
        'features':['GNOME','settings','screen keyboard','Adreno830v1 Mesa','touch driver','WiFi userspace and driver','Bluetooth OBEX','battery','MSM Resources','compatibility blank and stereo candidates'],
        'checks':checks,'radio_activation':'explicit local RAM bring-up only',
        'private_local_inputs_required':['phone signed ADSP and graphics firmware','existing qualified radio firmware','display profile','ABL memory calibration']}
    (out/'manifest.json').write_text(json.dumps(value,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(sha256(image)+'  rootfs.squashfs\n')
    print(json.dumps({k:v for k,v in value.items() if k!='checks'},indent=2))

if __name__=='__main__':main()
