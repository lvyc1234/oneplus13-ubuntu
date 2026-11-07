#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""QEMU-only handoff of exact SquashFS with separately pinned matching modules."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import tarfile
import threading
from common import make_cpio,read_cpio,sha256
from desktop import derive

REPO=Path(__file__).resolve().parents[2]
EXPECTED_KERNEL_COMMIT='1f5c1128d42a62244bef9974e58cfe07adf29f7d'
EXPECTED_ROOTFS='f9140e5c98d70bca6a04b8934dc9b1937bbf28b1eb0218411eaa0a9939a6cced'
EXPECTED_RELEASE='7.2.0-oneplus13-g7a81d184e309-c963dc1c909ec-p4f8ae6f76730'

def reserve_emulator_time(helper):
    """Budget the added sandbox ONLY in the explicit dummy-virt observer."""
    if b'grep -qx linux,dummy-virt' not in helper or b'[ "${1:-}" = /newroot ]' not in helper:
        raise ValueError('Not the emulator-only observer installer')
    if b'oneplus13.desktop.qemu-http=1' not in helper or helper.count(b'deadline=180 ;; esac')!=1:
        raise ValueError('Unexpected emulator deadline fixture')
    return helper.replace(b'deadline=180 ;; esac',b'deadline=300 ;; esac',1)

def main():
    p=argparse.ArgumentParser();p.add_argument('--kernel',type=Path,required=True)
    p.add_argument('--rootfs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--assembled-manifest',type=Path,help='Public matched-rootfs assembly provenance')
    p.add_argument('--rootfs-manifest',type=Path,help='Exact owner-tested public rootfs artifact manifest')
    p.add_argument('--rpc-selftest-archive',type=Path,help='Public native ARM64 mock-test archive, never phone data')
    p.add_argument('--rpc-selftest-manifest',type=Path)
    p.add_argument('--ssc-probe-archive',type=Path,help='Public SSC guard fixtures; QRTR queries forbidden in QEMU')
    p.add_argument('--ssc-probe-manifest',type=Path)
    a=p.parse_args();kernel=a.kernel.resolve();rootfs=a.rootfs.resolve();out=a.output.resolve()
    shared_cec_test=False
    mock_tests=bool(a.rpc_selftest_archive or a.rpc_selftest_manifest)
    ssc_tests=bool(a.ssc_probe_archive or a.ssc_probe_manifest)
    if mock_tests and not (a.rpc_selftest_archive and a.rpc_selftest_manifest and a.assembled_manifest):
        raise ValueError('Mock tests require both public archive inputs and matched rootfs provenance')
    if ssc_tests and not (a.ssc_probe_archive and a.ssc_probe_manifest and a.assembled_manifest):
        raise ValueError('SSC fixtures require both public archive inputs and matched rootfs provenance')
    manifest=json.loads((kernel/'manifest.json').read_text())
    assert manifest['integration_commit']==EXPECTED_KERNEL_COMMIT
    assert manifest['kernel_release']==EXPECTED_RELEASE
    for name in ['Image','initramfs.cpio.gz','modules.tar.gz']:
        assert sha256(kernel/name)==manifest['files'][name]['sha256'],name
    expected_sha=EXPECTED_ROOTFS;expected_bytes=426221568
    if a.rootfs_manifest:
        exact=json.loads(a.rootfs_manifest.read_text())
        assert not a.assembled_manifest
        assert exact['integration_commit']=='de8eef9ca4d94940e6edb63f26a1dcd9d0bed7b2'
        expected_sha=exact['files']['rootfs.squashfs']['sha256']
        expected_bytes=exact['files']['rootfs.squashfs']['size']
        assembly={
            'kernel_integration_commit':EXPECTED_KERNEL_COMMIT,
            'kernel_release':EXPECTED_RELEASE,
            'rootfs_sha256':expected_sha,
            'rootfs_bytes':expected_bytes,
        }
    if a.assembled_manifest:
        assembly=json.loads(a.assembled_manifest.read_text())
        assert assembly['scope']=='PUBLIC_RAM_ROOTFS_MATCHED_KERNEL'
        assert assembly['base_rootfs_sha256']==EXPECTED_ROOTFS
        assert assembly['kernel_integration_commit']==EXPECTED_KERNEL_COMMIT
        assert assembly['kernel_release']==EXPECTED_RELEASE and assembly['rootfs_matching_modules_preinstalled']
        assert assembly['private_firmware_or_calibration_included'] is False
        expected_sha=assembly['rootfs_sha256'];expected_bytes=assembly['rootfs_bytes']
        shared_cec_test=bool(assembly.get('unreviewed_peripheral_module_policy_sha256'))
    assert sha256(rootfs)==expected_sha and rootfs.stat().st_size==expected_bytes
    with tarfile.open(kernel/'modules.tar.gz') as archive:
        assert all(not x.name.startswith('/') and '..' not in Path(x.name).parts for x in archive)
    packed,_=derive((kernel/'initramfs.cpio.gz').read_bytes())
    files=read_cpio(gzip.decompress(packed))
    # The generic userspace artifact deliberately has no kernel-release pin.
    # Supply the separately verified matching kernel in the emulator's RAM
    # overlay before prepare's normal release check, never by editing SquashFS.
    prepare=files['bin/oneplus13-ram-prepare']['data']
    release_check=b'[ "$(cat /newroot/usr/lib/oneplus13-desktop/kernel-release)" = "$(uname -r)" ]\n'
    assert prepare.count(release_check)==1
    bind=(b'[ "$qemu" = 1 ]\n'+
          b'tr \'\\000\' \'\\n\' < /sys/firmware/devicetree/base/compatible | grep -qx linux,dummy-virt\n'+
          b'[ "$(uname -r)" = '+EXPECTED_RELEASE.encode()+b' ]\n'+
          (b'' if a.assembled_manifest else
           b'[ ! -e /newroot/usr/lib/oneplus13-desktop/kernel-release ]\n'+
           b'printf \'%s\\n\' '+EXPECTED_RELEASE.encode()+b' > /newroot/usr/lib/oneplus13-desktop/kernel-release\n'))
    files['bin/oneplus13-ram-prepare']['data']=prepare.replace(release_check,bind+release_check)
    helper=files['bin/oneplus13-qemu-install']['data']
    anchor=b'[ "${1:-}" = /newroot ]\n'
    assert helper.count(anchor)==1
    install=(b'mkdir -p /run/qemu-modules /newroot/usr/lib/modules\n'+
             b'/bin/busybox tar xzf /qemu-matching-modules.tar.gz -C /run/qemu-modules\n'+
             b'cp -a /run/qemu-modules/lib/modules/. /newroot/usr/lib/modules/\n')
    files['bin/oneplus13-qemu-install']['data']=helper.replace(anchor,anchor+install)
    if shared_cec_test:
        cec_sha='69c2bafd5315b52d253d0f26c0f8a5351d7bf940f61ee5a242b637483ca42659'
        ubwc_sha='96c8350913e8e271aed91a6586726d2061eaea6d23b03922c6050a969c685a1d'
        with tarfile.open(kernel/'modules.tar.gz') as archive:
            entry=next(x for x in archive if x.name.endswith('/kernel/drivers/media/cec/core/cec.ko'))
            import hashlib
            assert hashlib.sha256(archive.extractfile(entry).read()).hexdigest()==cec_sha
            entry=next(x for x in archive if x.name.endswith('/kernel/drivers/soc/qcom/ubwc_config.ko'))
            assert hashlib.sha256(archive.extractfile(entry).read()).hexdigest()==ubwc_sha
        files['bin/ram-display-prerequisites.py']={'mode':stat.S_IFREG|0o600,'major':0,'minor':0,
            'data':(REPO/'tools/oneplus13/ram-display-prerequisites.py').read_bytes()}
        helper=files['bin/oneplus13-qemu-install']['data']
        copy=b'cp /bin/ram-display-prerequisites.py /newroot/run/oneplus13/display-prerequisites.py\n'
        helper=helper.replace(anchor,anchor+copy,1)
        marker=b'echo ONEPLUS13_DISPLAY_AUTOLOAD_BLOCKED\n'
        assert helper.count(marker)==1
        checks=(b'# Reproduce the shared CEC dependency denial without loading display.\n'
            b'modprobe -n -v --ignore-install msm > /run/oneplus13/msm-before-cec\n'
            b'grep -q "^install /bin/false" /run/oneplus13/msm-before-cec\n'
            b'[ ! -d /sys/module/cec ]\n'
            b'[ ! -d /sys/module/ubwc_config ]\n'
            b'python3 /run/oneplus13/display-prerequisites.py --release "$(uname -r)" --sha256 '+cec_sha.encode()+
            b' --ubwc-sha256 '+ubwc_sha.encode()+b' --policy-sha256 '+assembly['unreviewed_peripheral_module_policy_sha256'].encode()+b'\n'
            b'modprobe -n -v --ignore-install msm > /run/oneplus13/msm-after-cec\n'
            b'if grep "^install " /run/oneplus13/msm-after-cec | grep -v "^install /bin/true *$"; then cat /run/oneplus13/msm-after-cec; exit 1; fi\n'
            b'grep -q "insmod .*msm.ko" /run/oneplus13/msm-after-cec\n'
            b'[ ! -d /sys/module/panel_oneplus_aa569 ]\n[ ! -d /sys/module/msm ]\n'
            b'echo ONEPLUS13_SHARED_CEC_CORE_PREREQUISITE_OK\n')
        files['bin/oneplus13-qemu-install']['data']=helper.replace(marker,marker+checks,1)
        for name in ['ram-sensor-fileserver.py','qemu-sensor-fileserver-selftest.py']:
            files['bin/'+name]={'mode':stat.S_IFREG|0o600,'major':0,'minor':0,
                               'data':(REPO/'tools/oneplus13'/name).read_bytes()}
        helper=files['bin/oneplus13-qemu-install']['data']
        copies=b''.join(b'cp /bin/'+n.encode()+b' /newroot/run/oneplus13/'+n.encode()+b'\n'
                        for n in ['ram-sensor-fileserver.py','qemu-sensor-fileserver-selftest.py'])
        helper=helper.replace(anchor,anchor+copies,1)
        handoff=b'echo ONEPLUS13_DESKTOP_HANDOFF_OK\n'
        assert helper.count(handoff)==1
        files['bin/oneplus13-qemu-install']['data']=helper.replace(handoff,
            b'python3 /run/oneplus13/qemu-sensor-fileserver-selftest.py\n'+handoff,1)
        files['bin/oneplus13-qemu-install']['data']=reserve_emulator_time(files['bin/oneplus13-qemu-install']['data'])
    if mock_tests:
        assert shared_cec_test and not out.exists(),'Use a fresh emulator-only output'
        test_helper=REPO/'tools/oneplus13/ram-rpc-selftest.py'
        spec=importlib.util.spec_from_file_location('rpc_mock_contract',test_helper)
        mock=importlib.util.module_from_spec(spec);spec.loader.exec_module(mock)
        test_manifest=json.loads(a.rpc_selftest_manifest.read_text())
        mock.archive_entries(a.rpc_selftest_archive,test_manifest)
        fixture={'ram-rpc-selftest.py':test_helper,'rpc-selftests.tar.gz':a.rpc_selftest_archive,
                 'rpc-selftests.json':a.rpc_selftest_manifest}
        for name,path in fixture.items():
            files['bin/'+name]={'mode':stat.S_IFREG|0o600,'major':0,'minor':0,'data':path.read_bytes()}
        helper=files['bin/oneplus13-qemu-install']['data']
        copies=b''.join(b'cp /bin/'+n.encode()+b' /newroot/run/oneplus13/'+n.encode()+b'\n' for n in fixture)
        helper=helper.replace(anchor,anchor+copies,1)
        checks=(b'python3 /run/oneplus13/ram-rpc-selftest.py --archive /run/oneplus13/rpc-selftests.tar.gz '
                b'--manifest /run/oneplus13/rpc-selftests.json --archive-sha256 '+sha256(a.rpc_selftest_archive).encode()+
                b' --manifest-sha256 '+sha256(a.rpc_selftest_manifest).encode()+b'\n'
                b'echo ONEPLUS13_RPC_MOCK_SELFTEST_QEMU_OK\n')
        files['bin/oneplus13-qemu-install']['data']=helper.replace(handoff,checks+handoff,1)
    if ssc_tests:
        assert shared_cec_test and not out.exists(),'Use a fresh emulator-only output'
        ssc_helper=REPO/'tools/oneplus13/ram-ssc-metadata.py'
        ssc_fixture=REPO/'tools/oneplus13/qemu-ssc-metadata-selftest.py'
        spec=importlib.util.spec_from_file_location('ssc_probe_contract',ssc_helper)
        ssc=importlib.util.module_from_spec(spec);spec.loader.exec_module(ssc)
        ssc.entries(a.ssc_probe_archive,json.loads(a.ssc_probe_manifest.read_text()))
        fixtures={'ram-ssc-metadata.py':ssc_helper,'qemu-ssc-metadata-selftest.py':ssc_fixture,
                  'ssc-probe.tar.gz':a.ssc_probe_archive,'ssc-probe.json':a.ssc_probe_manifest,
                  'ram-sensor-discovery.py':REPO/'tools/oneplus13/ram-sensor-discovery.py',
                  'qemu-sensor-discovery-selftest.py':REPO/'tools/oneplus13/qemu-sensor-discovery-selftest.py'}
        for name,path in fixtures.items():
            files['bin/'+name]={'mode':stat.S_IFREG|0o600,'major':0,'minor':0,'data':path.read_bytes()}
        helper=files['bin/oneplus13-qemu-install']['data']
        copies=b''.join(b'cp /bin/'+n.encode()+b' /newroot/run/oneplus13/'+n.encode()+b'\n' for n in fixtures)
        helper=helper.replace(anchor,anchor+copies,1)
        checks=(b'python3 /run/oneplus13/qemu-ssc-metadata-selftest.py --archive-sha256 '+sha256(a.ssc_probe_archive).encode()+
                b' --manifest-sha256 '+sha256(a.ssc_probe_manifest).encode()+b'\n'
                b'python3 /run/oneplus13/qemu-sensor-discovery-selftest.py\n'
                b'echo ONEPLUS13_SENSOR_DISCOVERY_COMPOSITE_QEMU_OK\n'
                b'echo ONEPLUS13_SSC_GUARD_SANDBOX_QEMU_OK\n')
        files['bin/oneplus13-qemu-install']['data']=helper.replace(handoff,checks+handoff,1)
    files['qemu-matching-modules.tar.gz']={'mode':stat.S_IFREG|0o600,'major':0,'minor':0,
                                           'data':(kernel/'modules.tar.gz').read_bytes()}
    out.mkdir(parents=True,exist_ok=True);ramdisk=out/'initramfs-qemu-only.cpio.gz'
    ramdisk.write_bytes(gzip.compress(make_cpio([(n,v['mode'],v['data'],v['major'],v['minor'])
                                                for n,v in sorted(files.items())]),mtime=0))
    spec=importlib.util.spec_from_file_location('server',REPO/'tools/oneplus13/serve-rootfs.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    stats={'bytes':0,'parts':[]}
    use_http=not bool(a.rootfs_manifest)
    server=thread=None
    if use_http:
        server=module.ThreadingHTTPServer(('127.0.0.1',8000),module.handler(rootfs,stats));server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    cmdline=('console=ttyAMA0 rdinit=/init panic=15 oneplus13.desktop=1 '
             'oneplus13.desktop.qemu=1 '+('oneplus13.desktop.qemu-http=1 ' if use_http else '')+
             f'oneplus13.rootfs.sha256={expected_sha} oneplus13.rootfs.bytes={rootfs.stat().st_size}')
    command=['qemu-system-aarch64','-machine','virt','-cpu','cortex-a76','-m','6144','-smp','2',
             '-nographic','-no-reboot','-nic','none','-kernel',str(kernel/'Image'),'-initrd',str(ramdisk),
             '-drive',f'file={rootfs},format=raw,if=none,readonly=on,id=rootfs',
             '-device','virtio-blk-device,drive=rootfs','-append',cmdline,
             '-netdev','user,id=rootfsnet,net=172.16.13.0/24,host=172.16.13.2',
             '-device','virtio-net-device,netdev=rootfsnet,mac=02:13:00:00:00:01']
    try:
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=360)
        log=result.stdout;rc=result.returncode
    except subprocess.TimeoutExpired as exc:
        log=exc.stdout or b'';log=log.decode(errors='replace') if isinstance(log,bytes) else log;rc=124
    finally:
        if use_http:
            server.shutdown();server.server_close();thread.join()
    (out/'qemu.log').write_text(log)
    markers=['ONEPLUS13_DISPLAY_AUTOLOAD_BLOCKED','ONEPLUS13_NATIVE_RUNTIME_GUARDS_OFFLINE_OK',
             'ONEPLUS13_NATIVE_PREFLIGHT_OFFLINE_OK','ONEPLUS13_NATIVE_HANDOFF_OFFLINE_OK',
             'ONEPLUS13_DESKTOP_HANDOFF_OK','ONEPLUS13_QEMU_DEADLINE_FIRED','reboot: Restarting system']
    if shared_cec_test:markers.insert(1,'ONEPLUS13_SHARED_CEC_CORE_PREREQUISITE_OK')
    if shared_cec_test:markers.insert(markers.index('ONEPLUS13_DESKTOP_HANDOFF_OK'),
                                    'ONEPLUS13_SENSOR_FILESERVER_SANDBOX_QEMU_OK')
    if mock_tests:markers.insert(markers.index('ONEPLUS13_DESKTOP_HANDOFF_OK'),'ONEPLUS13_RPC_MOCK_SELFTEST_QEMU_OK')
    if ssc_tests:
        markers.insert(markers.index('ONEPLUS13_DESKTOP_HANDOFF_OK'),'ONEPLUS13_SENSOR_DISCOVERY_COMPOSITE_QEMU_OK')
        markers.insert(markers.index('ONEPLUS13_DESKTOP_HANDOFF_OK'),'ONEPLUS13_SSC_GUARD_SANDBOX_QEMU_OK')
    passed=rc==0 and all(x in log for x in markers)
    if passed:passed=all(log.index(x)<log.index(y) for x,y in zip(markers,markers[1:]))
    if use_http:
        count=(rootfs.stat().st_size+module.CHUNK-1)//module.CHUNK
        passed=passed and stats['bytes']==rootfs.stat().st_size and stats['parts']==list(range(count))
    report={'scope':'QEMU_EXACT_ROOTFS_HANDOFF_ONLY','phone_operations':0,'hardware_tested':False,
            'rootfs_sha256':expected_sha,'rootfs_bytes':rootfs.stat().st_size,
            'kernel_integration_commit':EXPECTED_KERNEL_COMMIT,'kernel_release':EXPECTED_RELEASE,
            'kernel_image_sha256':sha256(kernel/'Image'),'modules_sha256':sha256(kernel/'modules.tar.gz'),
            'qemu_only_module_delivery':True,'rootfs_bytes_unchanged':sha256(rootfs)==expected_sha,
            'assembled_rootfs':bool(a.assembled_manifest),
            'shared_cec_core_prerequisite_tested':shared_cec_test,
            'shared_ubwc_configuration_prerequisite_tested':shared_cec_test,
            'synthetic_sensor_fileserver_sandbox_tested':shared_cec_test,
            'synthetic_rpc_mock_tests_sandboxed':mock_tests,
            'synthetic_ssc_guard_tests_sandboxed':ssc_tests,
            'synthetic_concurrent_metadata_and_reverse_units_tested':ssc_tests,
            'ssc_qrtr_discovery_executed':False,
            'qemu_only_deadline_seconds':300 if shared_cec_test else 180,
            'phone_recovery_deadline_modified':False,
            'returncode':rc,'required_markers':{x:x in log for x in markers},
            'http_bytes':stats['bytes'],'http_parts':stats['parts'],'handoff_and_deadline_pass':passed,
            'physical_display_input_radio_or_audio_tested':False}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if not passed:print(log[-9000:]);raise SystemExit(1)

if __name__=='__main__':main()
