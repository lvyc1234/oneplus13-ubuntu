#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Package a complete RAM image from fresh builds and verified hardware data.

--qualified-data uses the owner-authorized, scrubbed repository dataset in CI.
--private-inputs preserves local-only packaging for unreviewed owner datasets.
Neither mode contacts a phone or consumes an old kernel/rootfs/script overlay.
"""
import argparse,gzip,importlib,json,os,secrets,shutil,struct,subprocess,sys,tarfile,tempfile
from pathlib import Path
from common import boot_sections,read_cpio,sha256
from native import build_initramfs
from desktop import derive, FIRSTBOOT_SOURCE_FILES, FIRSTBOOT_GENERATED_FILES
from stock import Fdt

REPO=Path(__file__).resolve().parents[2]

def capture_diagnostic_bindings(stage):
    """Detach validated metadata while the temporary root still exists."""
    path=stage/'usr/lib/oneplus13-desktop/diagnostic-runtime.json'
    diagnostics=json.loads(path.read_text())
    if any(diagnostics.get(key) is not False for key in
           ['hardware_tested','native_dpms_qualified','system_suspend_enabled']):
        raise ValueError('Unexpected diagnostic hardware or suspend claim')
    files=diagnostics.get('files',{})
    if not files:raise ValueError('Diagnostic runtime bindings missing')
    for name,digest in files.items():
        target=stage/name
        if Path(name).is_absolute() or '..' in Path(name).parts or not target.resolve().is_relative_to(stage.resolve()):
            raise ValueError('Diagnostic runtime path escapes staging')
        if sha256(target)!=digest:raise ValueError('Diagnostic runtime changed before packaging: '+name)
    return dict(diagnostic_runtime_files=files,diagnostic_runtime_hardware_qualified=False,
                diagnostic_runtime_manifest_sha256=sha256(path))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['kernel','desktop','rootfs','output']:p.add_argument('--'+n,required=True,type=Path)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--private-inputs',type=Path)
    group.add_argument('--qualified-data',type=Path)
    p.add_argument('--runtime-checks',choices=['qemu','static'],default='qemu',help='static is the owner-approved default for unchanged qualified runtime inputs in CI')
    a=p.parse_args();kernel=a.kernel.resolve();desktop=a.desktop.resolve();rootfs=a.rootfs.resolve();private=(a.qualified_data or a.private_inputs).resolve();out=a.output.resolve()
    public=a.qualified_data is not None
    if os.geteuid()!=0:raise ValueError('Root required for filesystem packing')
    if out.exists() or (not public and out.is_relative_to(REPO)):raise ValueError('Fresh output required; unreviewed private output must stay outside checkout')
    manifest_name='manifest.json' if public else 'manifest.private.json'
    pin=json.loads((private/manifest_name).read_text())
    expected='ONEPLUS13_QUALIFIED_DATA_V1' if public else 'ONEPLUS13_PRIVATE_DATA_V1'
    if pin['format']!=expected:raise ValueError('Wrong data manifest format')
    if public:
        if private!=(REPO/'devices/oneplus13/qualified-data').resolve():raise ValueError('CI accepts only the reviewed repository dataset')
        from verify_qualified_data import verify
        verify(private)
    for name,item in pin['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or (private/name).is_symlink():raise ValueError('Unsafe private data path')
        if sha256(private/name)!=item['sha256'] or (private/name).stat().st_size!=item['bytes']:raise ValueError('Changed private input')
    km=json.loads((kernel/'manifest.json').read_text());dm=json.loads((desktop/'manifest.json').read_text());rm=json.loads((rootfs/'manifest.json').read_text())
    profile=rm.get('desktop_profile','full')
    if profile not in ('full','diagnostic'):raise ValueError('Unknown desktop profile')
    if len({km['integration_commit'],dm['integration_commit'],rm['integration_commit']})!=1:raise ValueError('Public components must share one source commit')
    if a.runtime_checks=='static' and not public:raise ValueError('Static-only packaging is limited to the reviewed repository dataset')
    if a.runtime_checks=='qemu' and not all(dm.get(k) is True for k in ['qemu_handoff_verified','qemu_deadline_expiry_verified','qemu_native_preflight_runtime_guards_verified']):raise ValueError('Fresh complete-rootfs QEMU handoff/recovery required')
    for name in ['Image','Image.gz','initramfs.cpio.gz','modules.tar.gz','sm8750-oneplus-dodge-native.dtb']:
        if sha256(kernel/name)!=km['files'][name]['sha256']:raise ValueError('Kernel artifact changed')
    if sha256(rootfs/'rootfs.squashfs')!=rm['rootfs_sha256'] or sha256(rootfs/'rootfs.squashfs')!=dm['rootfs_sha256']:raise ValueError('QEMU rootfs differs')
    if sha256(kernel/'sm8750-oneplus-dodge-native.dtb')!=pin['compatible_public_native_dtb_sha256']:raise ValueError('Private calibration must be re-reviewed for this new DTB')
    if km['kernel_release']!=rm['kernel_release']:raise ValueError('Kernel/rootfs module mismatch')
    release=km['kernel_release'];out.mkdir(parents=True)
    # Ubuntu contains case-distinct paths (for example Perl Sys and sys).
    # Never unpack a Linux rootfs onto a case-insensitive Windows/DrvFS tree.
    staging=tempfile.TemporaryDirectory(prefix='oneplus13-private-rootfs-',dir='/var/tmp')
    stage=Path(staging.name)/'rootfs'
    subprocess.run(['unsquashfs','-no-progress','-processors','2','-d',str(stage),str(rootfs/'rootfs.squashfs')],check=True)
    storage_guard_sources={
        'usr/libexec/oneplus13-runtime-guard':'devices/oneplus13/desktop/runtime-guard',
        'usr/libexec/oneplus13-readonly-block.py':'tools/oneplus13/readonly-block.py',
        'usr/lib/udev/rules.d/09-oneplus13-ram-readonly.rules':'devices/oneplus13/desktop/09-oneplus13-ram-readonly.rules'}
    storage_guard_hashes={}
    for name,source in storage_guard_sources.items():
        if (stage/name).read_bytes()!=(REPO/source).read_bytes():
            raise ValueError('Storage guard rootfs differs from current source')
        storage_guard_hashes[name]=sha256(stage/name)
    firstboot_hashes={}
    for name,source in FIRSTBOOT_SOURCE_FILES.items():
        if (stage/name).read_bytes() != (REPO/source).read_bytes():
            raise ValueError('First-user runtime differs from current source: '+name)
        firstboot_hashes[name]=sha256(stage/name)
    for name in FIRSTBOOT_GENERATED_FILES:
        firstboot_hashes[name]=sha256(stage/name)
    for name,item in pin['files'].items():
        if not name.startswith('firmware/'):continue
        dest=stage/'usr/lib/firmware'/name.removeprefix('firmware/')
        if not dest.resolve().is_relative_to(stage):raise ValueError('Firmware target escapes private staging')
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(private/name,dest);dest.chmod(0o644)
    firmware_hashes={name.removeprefix('firmware/'):item['sha256'] for name,item in pin['files'].items() if name.startswith('firmware/')}
    (stage/'usr/lib/oneplus13-desktop/private-firmware.json').write_text(json.dumps(firmware_hashes,indent=2)+'\n')
    bindings={'kernel_release':release,'firmware_sha256':firmware_hashes}
    for key,name in {'CPUCP_MODULE_SHA256':'mailbox/qcom-cpucp-mbox.ko','SOCINFO_MODULE_SHA256':'soc/qcom/socinfo.ko',
        'CEC_MODULE_SHA256':'media/cec/core/cec.ko','UBWC_MODULE_SHA256':'soc/qcom/ubwc_config.ko'}.items():
        bindings[key]=sha256(stage/'usr/lib/modules'/release/'kernel/drivers'/name)
    bindings['MODULE_POLICY_SHA256']=sha256(stage/'etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf')
    (stage/'usr/lib/oneplus13-desktop/runtime-bindings.private.json').write_text(json.dumps(bindings,indent=2)+'\n')
    dest=stage/'usr/libexec/oneplus13-qualified-bringup'
    shutil.copyfile(REPO/'devices/oneplus13/desktop/qualified-bringup',dest);dest.chmod(0o755)
    wifi_runtime_hashes={str(dest.relative_to(stage)):sha256(dest)}
    # These scripts are repository source, installed in the image rather than
    # supplied by old host overlays during the hardware test.
    for name in ['automatic-wifi-check.py','wifi-radio-report.py','wifi-band-cache.py','ram-wifi-quiescent.py','ram-kernel-health.py','ram-clock-bridge.py','ram-desktop-check.py','ram-platform-prerequisites.sh','ram-display-prerequisites.py']:
        dest=stage/'usr/libexec'/('oneplus13-'+name)
        shutil.copyfile(REPO/'tools/oneplus13'/name,dest);dest.chmod(0o755)
        wifi_runtime_hashes[str(dest.relative_to(stage))]=sha256(dest)
    # A source-bound probe is available for an explicitly reviewed metadata
    # test. It is never auto-started and contains no registry or measurement API.
    ssc=REPO/'out/phone-ssc'
    evidence=json.loads((ssc/'verification.json').read_text())
    ssc_manifest=ssc/'phone-ssc-probe-manifest.json'
    ssc_archive=ssc/'phone-ssc-probe-arm64.tar.gz'
    metadata=json.loads(ssc_manifest.read_text())
    if (evidence.get('integration_commit')!=km['integration_commit']
        or metadata.get('integration_commit')!=km['integration_commit']
        or evidence.get('native_arm64') is not True or evidence.get('host_check_only') is not False
        or evidence.get('typed_sensor_properties_regression_pass') is not True
        or evidence.get('test_counts')!={'release':5,'sanitized':5}
        or evidence.get('bounded_three_vector_sample_policy_and_decoder_tests_pass') is not True):
        raise ValueError('SSC probe must pass native checks at this source commit')
    importlib.import_module('ram-ssc-metadata').entries(ssc_archive,metadata)
    ssc_root=stage/'usr/lib/oneplus13-ssc';ssc_root.mkdir()
    ssc_hashes={}
    for path in [ssc_archive,ssc_manifest,ssc/'verification.json']:
        dest=ssc_root/path.name;shutil.copyfile(path,dest);dest.chmod(0o644)
        ssc_hashes[str(dest.relative_to(stage))]=sha256(dest)
    dest=stage/'usr/libexec/oneplus13-ram-ssc-metadata.py'
    shutil.copyfile(REPO/'tools/oneplus13/ram-ssc-metadata.py',dest);dest.chmod(0o755)
    ssc_hashes[str(dest.relative_to(stage))]=sha256(dest)
    sample_manifest=ssc/'phone-ssc-samples-manifest.json';sample_archive=ssc/'phone-ssc-samples-arm64.tar.gz'
    sample_metadata=json.loads(sample_manifest.read_text())
    if sample_metadata.get('integration_commit')!=km['integration_commit']:raise ValueError('Wrong sample source commit')
    importlib.import_module('ram-ssc-samples').entries(sample_archive,sample_metadata)
    sample_hashes={}
    for path in [sample_manifest,sample_archive]:
        dest=ssc_root/path.name;shutil.copyfile(path,dest);dest.chmod(0o644)
        sample_hashes[str(dest.relative_to(stage))]=sha256(dest)
    dest=stage/'usr/libexec/oneplus13-ram-ssc-samples.py'
    shutil.copyfile(REPO/'tools/oneplus13/ram-ssc-samples.py',dest);dest.chmod(0o755)
    sample_hashes[str(dest.relative_to(stage))]=sha256(dest)
    # Audio is delivered as a manually gated candidate, not a qualified output.
    # The generic owner-version ACF contains no per-unit registry or identities.
    from importlib import import_module
    audio=import_module('audio-dtb')
    audio_build=REPO/'out/phone-audio'
    audio_source=REPO/'devices/oneplus13/audio/aw88461_acf.bin'
    if sha256(audio_source)!='941f23e9e315820a01170a97dbb0c88382c46a548993e5d2ca98c7619b1b1847':
        raise ValueError('Phone ACF identity changed')
    audio_hashes={}
    for source,name in [(audio_source,audio.FIRMWARE),
                        (audio_build/'speaker-s16-probe.tplg',audio.TOPOLOGY)]:
        dest=stage/'usr/lib/firmware'/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest);dest.chmod(0o644)
        audio_hashes[str(dest.relative_to(stage))]=sha256(dest)
    dest=stage/'usr/libexec/oneplus13-ram-audio-check.py'
    shutil.copyfile(REPO/'tools/oneplus13/ram-audio-check.py',dest);dest.chmod(0o755)
    audio_hashes[str(dest.relative_to(stage))]=sha256(dest)
    camera_hashes={}
    dest=stage/'usr/libexec/oneplus13-ram-camera-chip-id.py'
    shutil.copyfile(REPO/'tools/oneplus13/ram-camera-chip-id.py',dest);dest.chmod(0o755)
    camera_hashes[str(dest.relative_to(stage))]=sha256(dest)
    camera_capture_hashes={}
    dest=stage/'usr/libexec/oneplus13-ram-camera-capture.py'
    shutil.copyfile(REPO/'tools/oneplus13/ram-camera-capture.py',dest);dest.chmod(0o755)
    camera_capture_hashes[str(dest.relative_to(stage))]=sha256(dest)
    peripheral_probe_hashes={}
    automatic_usb_hashes={}
    for source,name in (
        ('tools/oneplus13/auto-otg.py','usr/libexec/oneplus13-auto-otg.py'),
        ('devices/oneplus13/desktop/oneplus13-auto-otg.service','usr/lib/systemd/system/oneplus13-auto-otg.service'),
        ('devices/oneplus13/desktop/11-oneplus13-auto-usb.rules','usr/lib/udev/rules.d/11-oneplus13-auto-usb.rules'),
        ('devices/oneplus13/desktop/99-oneplus13-auto-usb-readonly.rules','usr/lib/udev/rules.d/99-oneplus13-auto-usb-readonly.rules')):
        dest=stage/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(REPO/source,dest)
        dest.chmod(0o755 if name.endswith('.py') else 0o644)
        automatic_usb_hashes[name]=sha256(dest)
    auto_wants=stage/'etc/systemd/system/multi-user.target.wants/oneplus13-auto-otg.service'
    auto_wants.parent.mkdir(parents=True,exist_ok=True)
    if auto_wants.exists() or auto_wants.is_symlink():raise ValueError('Automatic USB service link already exists')
    auto_wants.symlink_to('/usr/lib/systemd/system/oneplus13-auto-otg.service')
    for source,name in [
        ('ram-ir-check.py','usr/libexec/oneplus13-ram-ir-check.py'),
        ('ram-usb-role-check.py','usr/libexec/oneplus13-ram-usb-role-check.py'),
        ('ram-usb-native-host-check.py','usr/libexec/oneplus13-ram-usb-native-host-check.py'),
        ('ram-usb-powered-native-check.py','usr/libexec/oneplus13-ram-usb-powered-native-check.py'),
        ('ram-usb-supply-check.py','usr/libexec/oneplus13-ram-usb-supply-check.py'),
        ('usb-filesystem-modules.py','usr/libexec/oneplus13-usb-filesystem-modules.py'),
        ('ram-suspend-check.py','usr/libexec/oneplus13-ram-suspend-check.py'),
    ]:
        dest=stage/name
        shutil.copyfile(REPO/'tools/oneplus13'/source,dest)
        dest.chmod(0o755)
        peripheral_probe_hashes[name]=sha256(dest)
    nfc_hashes={}
    nfc_service=stage/'usr/lib/systemd/system/oneplus13-nfc.service'
    nfc_service.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(REPO/'devices/oneplus13/nfc/oneplus13-nfc.service',nfc_service)
    nfc_service.chmod(0o644)
    nfc_hashes[str(nfc_service.relative_to(stage))]=sha256(nfc_service)
    nfc_poll=stage/'usr/libexec/oneplus13-ram-nfc-poll.py'
    shutil.copyfile(REPO/'tools/oneplus13/ram-nfc-poll.py',nfc_poll)
    nfc_poll.chmod(0o755)
    nfc_hashes[str(nfc_poll.relative_to(stage))]=sha256(nfc_poll)
    nfc_wants=stage/'etc/systemd/system/multi-user.target.wants/oneplus13-nfc.service'
    nfc_wants.parent.mkdir(parents=True,exist_ok=True)
    if nfc_wants.exists() or nfc_wants.is_symlink():raise ValueError('Unexpected NFC service link')
    os.symlink('/usr/lib/systemd/system/oneplus13-nfc.service',nfc_wants)
    modem_service=stage/'usr/lib/systemd/system/oneplus13-modem.service'
    modem_service.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(REPO/'devices/oneplus13/modem/oneplus13-modem.service',modem_service)
    modem_service.chmod(0o644)
    modem_hashes={'usr/lib/systemd/system/oneplus13-modem.service':sha256(modem_service)}
    modem_wants=stage/'etc/systemd/system/multi-user.target.wants/oneplus13-modem.service'
    modem_wants.parent.mkdir(parents=True,exist_ok=True)
    if modem_wants.exists() or modem_wants.is_symlink():raise ValueError('Unexpected modem service link')
    os.symlink('/usr/lib/systemd/system/oneplus13-modem.service',modem_wants)
    # Explicit manual Iris diagnostic. It decodes only a generated public card.
    video_runtime=REPO/'out/phone-video'
    video_evidence=json.loads((video_runtime/'verification.json').read_text())
    if (video_evidence['integration_commit']!=km['integration_commit'] or
        video_evidence.get('native_arm64_selftest') is not True or
        video_evidence.get('pixel_validator_negative_controls_passed') is not True or
        video_evidence.get('asan_ubsan_pass') is not True or
        video_evidence['source_sha256']!=sha256(REPO/'tools/oneplus13/video-draft/decode-one-frame.c') or
        video_evidence['binary_sha256']!=sha256(video_runtime/'decode-one-frame')):
        raise ValueError('Video client needs same-source native validation')
    video_hashes={}
    for source,name in [(REPO/'tools/oneplus13/ram-video-check.py','usr/libexec/oneplus13-ram-video-check.py'),
                        (REPO/'tools/oneplus13/ram-gstreamer-frame.py','usr/libexec/oneplus13-ram-gstreamer-frame.py'),
                        (video_runtime/'decode-one-frame','usr/libexec/oneplus13-decode-one-frame'),
                        (REPO/'devices/oneplus13/video/synthetic-avc-bars.json','usr/lib/oneplus13-video/synthetic-avc-bars.json')]:
        dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest);dest.chmod(0o644 if name.endswith('.json') else 0o755)
        video_hashes[name]=sha256(dest)
    video=import_module('video-probe-dtb')
    video_blob,_=video.derive((private/'calibrated.dtb').read_bytes())
    video_tree=Fdt(video_blob)
    audio_guard=import_module('ram-audio-check')
    module_root=stage/'usr/lib/modules'/release
    ordered=audio_guard.module_order((module_root/'modules.dep').read_text(),['videocc_sm8750','qcom_iris'])
    module_paths={Path(line.split(':')[0]).stem.replace('-','_'):line.split(':')[0]
                  for line in (module_root/'modules.dep').read_text().splitlines()}
    module_hashes={module_paths[name]:sha256(module_root/module_paths[name]) for name in ordered}
    video_bindings={'kernel_release':release,'module_policy_sha256':bindings['MODULE_POLICY_SHA256'],
                    'files':dict(video_hashes),'module_files':module_hashes,
                    'live_nodes':{path:{key:value.hex() for key,value in video_tree.nodes[path].items()}
                                  for path in (video.VIDEO,video.MEMORY)}}
    dest=stage/'usr/lib/oneplus13-video/runtime-bindings.json'
    dest.write_text(json.dumps(video_bindings,indent=2)+'\n');dest.chmod(0o644)
    video_hashes[str(dest.relative_to(stage))]=sha256(dest)
    dest=stage/'etc/wireplumber/wireplumber.conf.d/90-oneplus13-manual-audio.conf'
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(REPO/'devices/oneplus13/audio/90-oneplus13-manual-audio.conf',dest)
    dest.chmod(0o644);audio_hashes[str(dest.relative_to(stage))]=sha256(dest)
    # Same-workflow native userspace and reviewed text data. The complete image
    # no longer needs a private host file-server archive for sensor diagnostics.
    sensor_runtime=REPO/'out/phone-ssc/sensor-inputs'
    runtime_manifest=json.loads((sensor_runtime/'phone-sensor-inputs-manifest.json').read_text())
    if runtime_manifest.get('integration_commit')!=km['integration_commit']:raise ValueError('Wrong sensor runtime commit')
    fs=import_module('ram-sensor-fileserver')
    fs.archive_entries(sensor_runtime/'phone-sensor-inputs.tar.gz',runtime_manifest)
    runtime_hashes={}
    for name in ('phone-sensor-inputs.tar.gz','phone-sensor-inputs-manifest.json'):
        dest=stage/'usr/lib/oneplus13-sensors'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(sensor_runtime/name,dest);dest.chmod(0o644)
        runtime_hashes[str(dest.relative_to(stage))]=sha256(dest)
    for name in ('ram-sensor-attach-only.py','ram-sensor-fileserver.py','ram-sensor-discovery.py','ram-ssc-proxy.py','image-sensors-check.py'):
        dest=stage/'usr/libexec'/('oneplus13-'+name)
        shutil.copyfile(REPO/'tools/oneplus13'/name,dest);dest.chmod(0o755)
        runtime_hashes[str(dest.relative_to(stage))]=sha256(dest)
    image_sensors=import_module('image-sensors-check')
    defaults={'format':'ONEPLUS13_IMAGE_SENSOR_INPUTS_V1','kernel_release':release,
        'module_sha256':sha256(stage/'usr/lib/modules'/release/'kernel/drivers/misc/fastrpc.ko'),
        'policy_sha256':bindings['MODULE_POLICY_SHA256'],
        'inputs':{name:{'path':path,'sha256':sha256(stage/path.lstrip('/'))} for name,path in image_sensors.PATHS.items()}}
    dest=stage/'usr/lib/oneplus13-sensors/runtime-bindings.json'
    dest.write_text(json.dumps(defaults,indent=2)+'\n');dest.chmod(0o644)
    runtime_hashes[str(dest.relative_to(stage))]=sha256(dest)
    runtime_hashes['usr/bin/strace']=sha256(stage/'usr/bin/strace')
    # The full Ubuntu metapackage also installs the distro SensorProxy unit.
    # Its BusName collides with the qualified phone SSC backend even when
    # loaded but inactive. D-Bus activation is supplied by the phone unit below.
    distro_proxy=stage/'etc/systemd/system/iio-sensor-proxy.service'
    distro_proxy.parent.mkdir(parents=True,exist_ok=True)
    if distro_proxy.exists() or distro_proxy.is_symlink():
        if not distro_proxy.is_symlink() or str(distro_proxy.readlink())!='/dev/null':
            raise ValueError('Unexpected existing distro sensor proxy override')
    else:distro_proxy.symlink_to('/dev/null')
    sensor_desktop_hashes={}
    sensor_desktop_sources=[
        ('ram-sensor-desktop.py','usr/libexec/oneplus13-ram-sensor-desktop.py',0o755),
        ('10-oneplus13-fastrpc.rules','etc/udev/rules.d/10-oneplus13-fastrpc.rules',0o644),
        ('net.hadess.SensorProxy.conf','usr/share/dbus-1/system.d/net.hadess.SensorProxy.conf',0o644),
        ('net.hadess.SensorProxy.policy','usr/share/polkit-1/actions/net.hadess.SensorProxy.policy',0o644),
        ('49-oneplus13-sensor-proxy.rules','etc/polkit-1/rules.d/49-oneplus13-sensor-proxy.rules',0o644),
        ('net.hadess.SensorProxy.service','usr/share/dbus-1/system-services/net.hadess.SensorProxy.service',0o644),
        ('oneplus13-sensor-setup.service','usr/lib/systemd/system/oneplus13-sensor-setup.service',0o644),
        ('oneplus13-sensor-fileserver.service','usr/lib/systemd/system/oneplus13-sensor-fileserver.service',0o644),
        ('oneplus13-sensor-proxy.service','usr/lib/systemd/system/oneplus13-sensor-proxy.service',0o644),
        ('oneplus13-sensor-stack.target','usr/lib/systemd/system/oneplus13-sensor-stack.target',0o644)]
    for source_name,target_name,mode in sensor_desktop_sources:
        source=(REPO/'tools/oneplus13'/source_name if source_name.endswith('.py')
                else REPO/'devices/oneplus13/sensors'/source_name)
        dest=stage/target_name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest);dest.chmod(mode)
        sensor_desktop_hashes[target_name]=sha256(dest)
    container_hashes={}
    container=import_module('container-kernel')
    with tarfile.open(kernel/'modules.tar.gz') as archive:
        container.check_payload((kernel/'kernel.config').read_text(),archive,release)
    for source,name in [(kernel/'kernel.config','usr/lib/oneplus13-container/kernel.config'),
                        (REPO/'tools/oneplus13/container-kernel.py','usr/libexec/oneplus13-container-kernel.py'),
                        (REPO/'tools/oneplus13/ram-container-check.py','usr/libexec/oneplus13-ram-container-check.py')]:
        dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest);dest.chmod(0o755 if name.endswith('.py') else 0o644)
        container_hashes[name]=sha256(dest)
    if profile=='full':
        if (stage/'etc/docker/daemon.json').read_bytes()!=(REPO/'devices/oneplus13/docker/daemon.json').read_bytes():raise ValueError('Docker RAM defaults changed')
        container_hashes['etc/docker/daemon.json']=sha256(stage/'etc/docker/daemon.json')
    elif (stage/'etc/docker/daemon.json').exists():
        raise ValueError('Docker configuration leaked into diagnostic profile')
    microphone_hashes={}
    for source,name in [(REPO/'out/phone-audio/speaker-microphone-probe.tplg','usr/lib/firmware/qcom/sm8750/OnePlus13-Audio-Probe-tplg.bin'),
                        (REPO/'tools/oneplus13/ram-microphone-check.py','usr/libexec/oneplus13-ram-microphone-check.py'),
                        (REPO/'tools/oneplus13/ram-acoustic-check.py','usr/libexec/oneplus13-ram-acoustic-check.py')]:
        dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest);dest.chmod(0o755 if name.endswith('.py') else 0o644)
        microphone_hashes[name]=sha256(dest)
    desktop_audio_hashes={}
    desktop_audio_sources=[
        (REPO/'devices/oneplus13/audio/OnePlus13-Audio-Probe.conf','usr/share/alsa/ucm2/conf.d/sm8750/OnePlus13-Audio-Probe.conf'),
        (REPO/'devices/oneplus13/audio/HiFi.conf','usr/share/alsa/ucm2/Qualcomm/sm8750/OnePlus13/HiFi.conf'),
        (REPO/'devices/oneplus13/audio/95-oneplus13-desktop-audio.conf','usr/lib/oneplus13-audio/95-oneplus13-desktop-audio.conf'),
        (REPO/'tools/oneplus13/ram-desktop-audio.py','usr/libexec/oneplus13-ram-desktop-audio.py'),
        (REPO/'tools/oneplus13/first-user-audio.py','usr/libexec/oneplus13-first-user-audio.py'),
        (REPO/'devices/oneplus13/audio/oneplus13-first-user-audio.service','usr/lib/systemd/system/oneplus13-first-user-audio.service'),
        (REPO/'tools/oneplus13/ram-desktop-volume-check.py','usr/libexec/oneplus13-ram-desktop-volume-check.py')]
    for source,name in desktop_audio_sources:
        dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest);dest.chmod(0o755 if name.endswith('.py') else 0o644)
        desktop_audio_hashes[name]=sha256(dest)
    desktop_audio_bindings={'kernel_release':release,'original_policy_sha256':bindings['MODULE_POLICY_SHA256'],
        'amplifier_module_sha256':sha256(stage/'usr/lib/modules'/release/'kernel/sound/soc/codecs/snd-soc-aw88461.ko'),
        'files':dict(desktop_audio_hashes)}
    dest=stage/'usr/lib/oneplus13-audio/desktop-bindings.json'
    dest.write_text(json.dumps(desktop_audio_bindings,indent=2)+'\n');dest.chmod(0o644)
    desktop_audio_hashes[str(dest.relative_to(stage))]=sha256(dest)
    first_audio=stage/'etc/systemd/system/user@1000.service.wants/oneplus13-first-user-audio.service'
    first_audio.parent.mkdir(parents=True,exist_ok=True)
    first_audio.symlink_to('/usr/lib/systemd/system/oneplus13-first-user-audio.service')
    # No filesystem access through stage is allowed after staging.cleanup().
    diagnostic_bindings=capture_diagnostic_bindings(stage)
    image_rootfs=out/'rootfs.squashfs'
    subprocess.run(['mksquashfs',str(stage),str(image_rootfs),'-noappend','-comp','zstd','-processors','2','-mkfs-time','0','-all-time','0','-no-progress'],check=True)
    staging.cleanup()
    native_helper=(REPO/'devices/oneplus13/start-native-display').read_bytes()
    canonical=(REPO/'devices/oneplus13/native-desktop.py').read_bytes()
    if not native_helper.startswith(b'#!/usr/bin/python3\n') or native_helper.replace(b'# kernel release: @KERNEL_RELEASE@\n',b'')!=canonical:
        raise ValueError('Complete image requires the full qualified Python display helper, not a diagnostic stub')
    packed,_=build_initramfs((kernel/'initramfs.cpio.gz').read_bytes(),(kernel/'modules.tar.gz').read_bytes(),release,None,
        native_helper,encoded_profile=(private/'display-profile.bin').read_bytes())
    packed,derive_report=derive(packed)
    from common import make_cpio
    files=read_cpio(gzip.decompress(packed))
    init=files['init']['data'];old=b'  sleep 900 &\n'
    if init.count(old)!=1:raise ValueError('Unrecognized pre-handoff watchdog')
    files['init']['data']=init.replace(old,b'  sleep 600 &\n')
    packed=gzip.compress(make_cpio([(n,e['mode'],e['data'],e['major'],e['minor']) for n,e in sorted(files.items())]),mtime=0)
    (out/'initramfs-desktop.cpio.gz').write_bytes(packed)
    nonce=secrets.token_hex(12)
    cmdline='rdinit=/init panic=15 oneplus13.desktop=1 oneplus13.rootfs.sha256='+sha256(image_rootfs)+' oneplus13.rootfs.bytes='+str(image_rootfs.stat().st_size)+' oneplus13.session='+nonce
    nfc=import_module('nfc-dtb')
    nfc_blob,nfc_report=nfc.derive((private/'calibrated.dtb').read_bytes())
    (out/'nfc-dtb-review.json').write_text(json.dumps(nfc_report,indent=2)+'\n')
    tree=Fdt(nfc_blob);tree.nodes['/chosen']['bootargs']=(cmdline+'\0').encode()
    dtb=out/'sm8750-oneplus-dodge-abl.dtb';dtb.write_bytes(tree.to_blob())
    geometry=json.loads((private/'boot-geometry.json').read_text())
    tool=json.loads((REPO/'kernel/source.json').read_text())['tool_inputs']['mkbootimg.py']
    archive=REPO/tool['bundled_archive']
    if sha256(archive)!=tool['archive_sha256']:raise ValueError('Boot packer archive mismatch')
    image=out/'boot-desktop-ram-experimental.img'
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive) as tf:tf.extractall(tmp,filter='data')
        mk=Path(tmp)/'mkbootimg.py'
        if sha256(mk)!=tool['sha256']:raise ValueError('Boot packer script mismatch')
        subprocess.run([sys.executable,str(mk),'--header_version','2','--pagesize','4096','--base','0',
            '--kernel_offset',str(geometry['kernel_addr']),'--ramdisk_offset',str(geometry['ramdisk_addr']),
            '--tags_offset',str(geometry['tags_addr']),'--dtb_offset',str(geometry['dtb_addr']),
            '--kernel',str(kernel/'Image.gz'),'--ramdisk',str(out/'initramfs-desktop.cpio.gz'),'--dtb',str(dtb),
            '--cmdline',cmdline,'--output',str(image)],check=True)
    if boot_sections(image.read_bytes())!={'kernel':(kernel/'Image.gz').read_bytes(),'ramdisk':packed,'dtb':dtb.read_bytes()}:raise ValueError('Independent boot section readback mismatch')
    result={'scope':'QUALIFIED_FRESH_SOURCE_COMPLETE_RAM_IMAGE_NEVER_FLASH' if public else 'PRIVATE_FRESH_SOURCE_COMPLETE_RAM_IMAGE_NEVER_FLASH','source_commit':km['integration_commit'],
        'kernel_release':release,'cmdline':cmdline,'session_identity_nonce':nonce,
        'boot_image_sha256':sha256(image),'boot_image_bytes':image.stat().st_size,
        'rootfs_sha256':sha256(image_rootfs),'rootfs_bytes':image_rootfs.stat().st_size,
        'firmware_files':len(firmware_hashes),'hardware_data_manifest_sha256':sha256(private/manifest_name),
        'qualified_repository_data':public,'private_owner_inputs_required':not public,
        'runtime_checks_requested':a.runtime_checks,'prerequisite_qemu_handoff_pass':dm.get('qemu_handoff_verified',False),
        'public_rootfs_sha256':rm['rootfs_sha256'],'modules_sha256':sha256(kernel/'modules.tar.gz'),
        'kernel_sha256':sha256(kernel/'Image.gz'),'profile_from_existing_qualified_data':True,
        'old_kernel_or_rootfs_used':False,'old_runtime_package_overlay_used':False,
        'full_qualified_python_display_helper':True,
        'initial_watchdog_seconds':600,'post_handoff_deadline_seconds':300,
        'initial_watchdog_survives_switch_root':False,'phone_operations':0,'partition_writes':0,
        'hardware_tested':False,'ssc_probe_files':ssc_hashes,'ssc_probe_auto_started':False,
        'ssc_sample_files':sample_hashes,'ssc_samples_auto_started':False,
        'ssc_sensor_property_types_corrected':True,
        'experimental_audio_files':audio_hashes,'audio_auto_started':False,
        'audio_hardware_qualified':False,'audio_base_dtb_sha256':audio.BASE_SHA}
    result.update(desktop_profile=profile,**diagnostic_bindings)
    result.update(camera_probe_runtime_files=camera_hashes,camera_probe_auto_started=False,camera_capture_qualified=False)
    result.update(wifi_runtime_files=wifi_runtime_hashes,
                  wifi_runtime_requires_host_overlay=False,
                  wifi_runtime_kernel_health_guard=True,
                  wifi_hardware_qualified=False)
    result.update(firstboot_runtime_files=firstboot_hashes,
                  firstboot_installer_sha256=sha256(REPO/'tools/oneplus13/install-desktop.py'),
                  firstboot_small_screen=True, firstboot_screen_keyboard_default=True,
                  firstboot_normal_users_precreated=False,
                  distro_sensor_proxy_masked=True)
    result.update(camera_capture_runtime_files=camera_capture_hashes,
                  camera_capture_auto_started=False,camera_capture_hardware_qualified=False)
    result.update(peripheral_probe_runtime_files=peripheral_probe_hashes,
                  peripheral_probe_auto_started=False)
    result.update(storage_guard_runtime_files=storage_guard_hashes,
                  storage_guard_nonblocking_readonly_ioctl=True)
    result.update(automatic_usb_runtime_files=automatic_usb_hashes,
                  automatic_usb_service_conditional=True,
                  automatic_usb_service_link='/usr/lib/systemd/system/oneplus13-auto-otg.service')
    result.update(video_probe_runtime_files=video_hashes,video_probe_auto_started=False,
                  video_hardware_decode_qualified=False)
    result.update(experimental_microphone_files=microphone_hashes,microphone_auto_started=False,microphone_hardware_qualified=False)
    result.update(nfc_files=nfc_hashes,nfc_service_auto_started=True,
                  nfc_poll_auto_started=False,nfc_poll_requires_host_overlay=False,
                  nfc_dtb_review=nfc_report,
                  nfc_controller_bind_hardware_qualified=False,
                  nfc_tag_poll_hardware_qualified=False)
    result.update(modem_files=modem_hashes,modem_service_auto_started=True,
                  modem_hardware_functional_qualified=False,
                  gnss_hardware_functional_qualified=False)
    result.update(desktop_audio_files=desktop_audio_hashes,desktop_audio_auto_started=False,
                  desktop_audio_first_user_service_available=True,
                  desktop_audio_first_user_gate='oneplus13.first_user_audio=1',
                  desktop_audio_hardware_qualified=False,desktop_audio_hardware_level=1023)
    result.update(container_files=container_hashes,container_userspace_installed=profile=='full',container_daemon_auto_started=False,
                  container_hardware_qualified=False)
    result.update(sensor_runtime_files=runtime_hashes,sensor_runtime_host_private_inputs_required=False,
                  sensor_runtime_auto_started=False,sensor_proxy_hardware_qualified=False)
    result.update(sensor_desktop_files=sensor_desktop_hashes,
                  sensor_desktop_stack_manual_start_after_adsp=True,
                  sensor_desktop_auto_rotation_enabled=True,
                  sensor_desktop_hardware_qualified=False)
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['scope','source_commit','kernel_release','boot_image_sha256','rootfs_sha256','firmware_files','old_kernel_or_rootfs_used']},indent=2))

if __name__=='__main__':main()
