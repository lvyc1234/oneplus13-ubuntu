#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Assemble a public RAM rootfs from pinned userspace, matching modules and Mesa.

No firmware, user accounts, per-unit registry or persistent installer is added.
Run only in a disposable native ARM64 CI VM with root permissions.
"""
import argparse,hashlib,importlib.util,json,os,shutil,subprocess,tarfile,tempfile
from pathlib import Path
from common import sha256
spec=importlib.util.spec_from_file_location('ram_module_policy',Path(__file__).with_name('ram-module-policy.py'))
policy_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy_module)

BASE_SHA='f9140e5c98d70bca6a04b8934dc9b1937bbf28b1eb0218411eaa0a9939a6cced'
BASE_BYTES=426221568
KERNEL_COMMIT='1f5c1128d42a62244bef9974e58cfe07adf29f7d'
RELEASE='7.2.0-oneplus13-g7a81d184e309-c963dc1c909ec-p4f8ae6f76730'
MESA_SHA='e53465ff50986cda9da206089e8cf787047745f21aaa5b54579e891c7fd65eba'
REPO=Path(__file__).resolve().parents[2]

def run(args):
    result=subprocess.run([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180)
    if result.returncode:
        raise RuntimeError('Offline assembly command failed: '+str(args[-3:])+'\n'+result.stdout[-4000:])
    return result.stdout

def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True)
    p.add_argument('--kernel',type=Path,required=True);p.add_argument('--mesa',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert os.geteuid()==0 and os.uname().machine=='aarch64'
    base=a.base.resolve();kernel=a.kernel.resolve();mesa=a.mesa.resolve();out=a.output.resolve()
    assert sha256(base)==BASE_SHA and base.stat().st_size==BASE_BYTES
    manifest=json.loads((kernel/'manifest.json').read_text())
    assert manifest['integration_commit']==KERNEL_COMMIT and manifest['kernel_release']==RELEASE
    archive=kernel/'modules.tar.gz';assert sha256(archive)==manifest['files']['modules.tar.gz']['sha256']
    assert sha256(mesa)==MESA_SHA
    assert not out.exists(),'Preserve an existing assembled rootfs'
    out.mkdir(parents=True)
    logs={}
    with tempfile.TemporaryDirectory(prefix='oneplus13-public-ram-rootfs-') as tmp:
        tmp=Path(tmp);root=tmp/'root';modules=tmp/'modules';modules.mkdir()
        logs['unsquashfs']=run(['unsquashfs','-processors','2','-d',root,base])
        assert (root/'lib').is_symlink() and os.readlink(root/'lib')=='usr/lib'
        pin=root/'usr/lib/oneplus13-desktop/kernel-release';assert not pin.exists()
        with tarfile.open(archive) as tf:
            blocked=policy_module.blocked_modules(tf.getnames(),RELEASE)
            deps=next(x for x in tf.getmembers() if x.name.endswith('/modules.dep'))
            blocked=policy_module.dependency_closure(blocked,tf.extractfile(deps).read().decode())
            for item in tf.getmembers():
                name=item.name.removeprefix('./')
                assert name in ('.','lib','lib/modules','lib/modules/'+RELEASE) or name.startswith('lib/modules/'+RELEASE+'/')
                assert not name.startswith('/') and '..' not in Path(name).parts
                assert item.isdir() or item.isfile() or item.issym()
                if item.issym():
                    # Build/source links in module archives refer to CI source
                    # paths and are unnecessary on the target; do not extract.
                    assert Path(name).name in ('build','source')
            members=[x for x in tf.getmembers() if not x.issym()]
            tf.extractall(modules,members=members,filter='data')
        shutil.copytree(modules/'lib/modules',root/'usr/lib/modules',dirs_exist_ok=True)
        # Preinstalling modules changes coldplug behavior. Keep unreviewed
        # peripheral aliases, explicit loads and dependency loads closed.
        assert {'ath12k','ath12k_wifi7','pwrseq_qcom_wcn','pci_pwrctrl_pwrseq',
                'snd_soc_sc8280xp','fastrpc','qcom_iris'}.issubset(blocked)
        assert not {'qcom_cpucp_mbox','socinfo','dodge_s3910','msm','panel_oneplus_aa569',
                    'libcomposite','usb_f_ncm','u_ether','dwc3'}.intersection(blocked)
        module_policy=root/'etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf'
        module_policy.write_text(policy_module.configuration(blocked))
        module_policy_sha=sha256(module_policy)
        for module in blocked:
            dry=run(['chroot',root,'/usr/sbin/modprobe','-S',RELEASE,'--show-depends',module])
            assert dry.strip() and all(line.strip()=='install /bin/false' for line in dry.splitlines()),(module,dry[:1800])
            result=subprocess.run(['chroot',str(root),'/usr/sbin/modprobe','-S',RELEASE,module],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=5)
            assert result.returncode!=0,'Unreviewed peripheral load was not denied: '+module
            assert 'Error running install command' in result.stdout or 'install' in result.stdout,(module,result.stdout[:1800])
        logs['unreviewed-module-loads-rejected']=len(blocked)
        pin.write_text(RELEASE+'\n')
        deadline=root/'usr/lib/systemd/system/oneplus13-ram-deadline.timer'
        policy=deadline.read_text()
        assert policy.count('OnBootSec=900s\n')==1
        deadline.write_text(policy.replace('OnBootSec=900s\n','OnBootSec=600s\n'))
        logs['mesa-control']=run(['dpkg-deb','-f',mesa,'Package','Version','Architecture','Depends'])
        assert 'arm64' in logs['mesa-control']
        logs['mesa-extract']=run(['dpkg-deb','-x',mesa,root])
        bt=REPO/'packaging/erhai-device-support/tree/usr/sbin/erhai-bt-address'
        target=root/'usr/sbin/erhai-bt-address'
        target.write_bytes(bt.read_bytes().replace(b'\r\n',b'\n'));target.chmod(0o755)
        checks={
            'gnome':['/usr/bin/gnome-shell','--version'],
            'supplicant':['/usr/sbin/wpa_supplicant','-v'],
            'bt-address-help':['/usr/sbin/erhai-bt-address','--help'],
            'panel-module':['/usr/sbin/modinfo','-k',RELEASE,'-F','vermagic','panel_oneplus_aa569'],
            'gpu-module':['/usr/sbin/modinfo','-k',RELEASE,'-F','vermagic','msm'],
            'mesa-dependencies':['/usr/bin/env','LD_LIBRARY_PATH=/usr/lib/erhai/mesa/lib','/usr/bin/ldd','/usr/lib/erhai/mesa/lib/libEGL_mesa.so.0'],
            'gallium-dependencies':['/usr/bin/env','LD_LIBRARY_PATH=/usr/lib/erhai/mesa/lib','/usr/bin/ldd','/usr/lib/erhai/mesa/lib/dri/msm_dri.so'],
        }
        for name,command in checks.items():
            value=run(['chroot',root,*command]);logs[name]=value
            assert 'not found' not in value,name
        assert RELEASE in logs['panel-module'] and RELEASE in logs['gpu-module']
        assert (root/'lib').is_symlink() and os.readlink(root/'lib')=='usr/lib'
        assert not (root/'var/lib/erhai/bt-address').exists()
        assert (root/'etc/machine-id').read_bytes()==b''
        assert not (root/'var/lib/erhai/sensors/persist/registry').exists()
        assert (root/'etc/systemd/system/suspend.target').is_symlink()
        assert os.readlink(root/'etc/systemd/system/suspend.target')=='/dev/null'
        assert not any(line.startswith('a:') for line in (root/'etc/passwd').read_text().splitlines())
        shadow=next(s.split(':')[1] for s in (root/'etc/shadow').read_text().splitlines() if s.startswith('root:'))
        assert shadow.startswith(('!','*'))
        image=out/'rootfs.squashfs'
        logs['mksquashfs']=run(['mksquashfs',root,image,'-noappend','-comp','zstd',
            '-Xcompression-level','15','-processors','2','-mkfs-time','0','-all-time','0','-no-progress'])
    result={'scope':'PUBLIC_RAM_ROOTFS_MATCHED_KERNEL','base_rootfs_sha256':BASE_SHA,
        'kernel_integration_commit':KERNEL_COMMIT,'kernel_release':RELEASE,'modules_sha256':sha256(archive),
        'mesa_sha256':MESA_SHA,'rootfs_sha256':sha256(image),'rootfs_bytes':image.stat().st_size,
        'runtime_absolute_deadline_seconds':600,
        'rootfs_matching_modules_preinstalled':True,'merged_usr_preserved':True,
        'unreviewed_peripheral_module_policy_sha256':module_policy_sha,
        'unreviewed_peripheral_modules_denied':blocked,'builtin_driver_dt_review_required':True,
        'mesa_dependencies_resolved':True,'locked_root_no_default_account':True,
        'bluetooth_address_generated':False,'private_firmware_or_calibration_included':False,
        'phone_operations':0,'hardware_tested':False,'installer_included':False}
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'assembly.log.json').write_text(json.dumps(logs,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(sha256(image)+'  rootfs.squashfs\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()

