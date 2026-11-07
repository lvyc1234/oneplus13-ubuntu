#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the exact complete RAM image under QEMU; no physical device operations."""
import argparse,json,subprocess,time
from pathlib import Path
from common import sha256

USB_LEASE_FLAG = 'qcom_battmgr.oneplus13_otg_lease=1'
USB_SUPPLY_READ_FLAG = 'qcom_battmgr.oneplus13_otg_supply_read=1'
USB_SUPPLY_LEASE_FLAG = 'qcom_battmgr.oneplus13_otg_supply_lease=1'
USB_AUTO_FLAG = 'qcom_battmgr.oneplus13_otg_auto=1'


def reject_parent_usb_lease(cmdline):
    keys = {flag.split('=', 1)[0] for flag in
            (USB_LEASE_FLAG, USB_SUPPLY_READ_FLAG, USB_SUPPLY_LEASE_FLAG, USB_AUTO_FLAG)}
    if any(token.split('=', 1)[0] in keys for token in cmdline.split()):
        raise ValueError('USB firmware lease leaked into default/parent boot')


def verify_targets_boot_arguments(camera_gnss, targets):
    """Bind the manual lease capability; capability never means auto-start."""
    reject_parent_usb_lease(camera_gnss['cmdline'])
    expected = camera_gnss['cmdline'].replace(
        'oneplus13.session=' + camera_gnss['session_identity_nonce'],
        'oneplus13.session=' + targets['session_identity_nonce'])
    expected += (' oneplus13.ir_probe=1 oneplus13.usb_host_probe=1 ' + USB_LEASE_FLAG
                 + ' ' + USB_SUPPLY_READ_FLAG + ' ' + USB_SUPPLY_LEASE_FLAG)
    if (targets['cmdline'] != expected
            or targets.get('usb_firmware_lease_available') is not True
            or targets.get('usb_firmware_lease_auto_started') is not False
            or targets.get('usb_supply_read_available') is not True
            or targets.get('usb_supply_read_auto_started') is not False
            or targets.get('usb_supply_setters_available') is not True
            or targets.get('usb_supply_setters_auto_started') is not False):
        raise ValueError('Infrared/USB-role boot arguments or manual lease policy changed')


def verify_automatic_boot_arguments(camera_gnss, automatic):
    reject_parent_usb_lease(camera_gnss['cmdline'])
    expected=camera_gnss['cmdline'].replace(
        'oneplus13.session='+camera_gnss['session_identity_nonce'],
        'oneplus13.session='+automatic['session_identity_nonce'])
    expected+=' oneplus13.usb_host_probe=1 '+USB_AUTO_FLAG+' oneplus13.first_user_audio=1'
    if (automatic['cmdline']!=expected
            or automatic.get('scope')!='AUTOMATIC_OTG_DESKTOP_RAM_CANDIDATE'
            or automatic.get('automatic_usb_service_enabled') is not True
            or automatic.get('automatic_usb_hardware_qualified') is not False
            or automatic.get('automatic_usb_watchdog_seconds')!=15
            or automatic.get('automatic_usb_heartbeat_seconds')!=3
            or automatic.get('usb_firmware_lease_available') is not False
            or automatic.get('usb_supply_setters_available') is not False):
        raise ValueError('Automatic USB policy or owner exclusivity changed')


def validate_container_profile(manifest):
    profile=manifest.get('desktop_profile','full')
    if profile not in ('full','diagnostic'):raise ValueError('Unknown desktop profile')
    installed=profile=='full'
    required={'usr/lib/oneplus13-container/kernel.config',
              'usr/libexec/oneplus13-container-kernel.py','usr/libexec/oneplus13-ram-container-check.py'}
    if installed:required.add('etc/docker/daemon.json')
    if (set(manifest.get('container_files',{}))!=required or
            manifest.get('container_userspace_installed') is not installed or
            manifest.get('container_daemon_auto_started') is not False):
        raise ValueError('Container payload does not match the declared desktop profile')
    return installed


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel',type=Path,required=True);p.add_argument('--image',type=Path,required=True)
    p.add_argument('--static-only',action='store_true',help='Verify packaging without repeating a runtime boot')
    a=p.parse_args();root=a.image.resolve();kernel=a.kernel.resolve()
    m=json.loads((root/'manifest.json').read_text())
    reject_parent_usb_lease(m['cmdline'])
    for name,key in [('rootfs.squashfs','rootfs_sha256'),('boot-desktop-ram-experimental.img','boot_image_sha256')]:
        if sha256(root/name)!=m[key]:raise ValueError('Complete artifact changed')
    if sha256(kernel/'Image.gz')!=m['kernel_sha256']:raise ValueError('Wrong kernel')
    # Inspect the actual delivered SquashFS, rather than just staging inputs.
    release=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),'usr/lib/oneplus13-desktop/kernel-release'],text=True).strip()
    if release!=m['kernel_release']:raise ValueError('Delivered kernel/module release mismatch')
    wifi_files=m.get('wifi_runtime_files',{})
    wifi_sources={'usr/libexec/oneplus13-qualified-bringup':'devices/oneplus13/desktop/qualified-bringup'}
    for name in ['automatic-wifi-check.py','wifi-radio-report.py','wifi-band-cache.py','ram-wifi-quiescent.py','ram-kernel-health.py','ram-clock-bridge.py','ram-desktop-check.py','ram-platform-prerequisites.sh','ram-display-prerequisites.py']:
        wifi_sources['usr/libexec/oneplus13-'+name]='tools/oneplus13/'+name
    if (set(wifi_files)!=set(wifi_sources) or
            m.get('wifi_runtime_requires_host_overlay') is not False or
            m.get('wifi_runtime_kernel_health_guard') is not True):
        raise ValueError('Missing source-bound WiFi runtime')
    repo=Path(__file__).resolve().parents[2]
    import hashlib
    diagnostic=m.get('diagnostic_runtime_files',{})
    if not diagnostic or m.get('diagnostic_runtime_hardware_qualified') is not False:
        raise ValueError('Missing offline diagnostic candidate bindings')
    for name,digest in diagnostic.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Diagnostic runtime changed: '+name)
    raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),'usr/lib/oneplus13-desktop/diagnostic-runtime.json'])
    if hashlib.sha256(raw).hexdigest()!=m['diagnostic_runtime_manifest_sha256']:
        raise ValueError('Diagnostic binding manifest changed')
    for name,source in wifi_sources.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=wifi_files[name] or sha256(repo/source)!=wifi_files[name]:
            raise ValueError('Delivered WiFi runtime differs from current source')
    from desktop import FIRSTBOOT_SOURCE_FILES, FIRSTBOOT_GENERATED_FILES
    firstboot=m.get('firstboot_runtime_files',{})
    if (set(firstboot) != set(FIRSTBOOT_SOURCE_FILES) | set(FIRSTBOOT_GENERATED_FILES)
            or m.get('firstboot_installer_sha256') != sha256(repo/'tools/oneplus13/install-desktop.py')
            or m.get('firstboot_small_screen') is not True
            or m.get('firstboot_screen_keyboard_default') is not True
            or m.get('firstboot_normal_users_precreated') is not False
            or m.get('distro_sensor_proxy_masked') is not True):
        raise ValueError('Missing source-bound first-user desktop runtime')
    for name,digest in firstboot.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:
            raise ValueError('Delivered first-user runtime changed: '+name)
        if name in FIRSTBOOT_SOURCE_FILES and sha256(repo/FIRSTBOOT_SOURCE_FILES[name])!=digest:
            raise ValueError('Delivered first-user runtime differs from source: '+name)
    link=subprocess.check_output(['unsquashfs','-ll',str(root/'rootfs.squashfs'),
                                 'etc/systemd/system/iio-sensor-proxy.service'],text=True)
    if ' -> /dev/null' not in link:
        raise ValueError('Conflicting distro SensorProxy is not masked')
    data=json.loads(subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),'usr/lib/oneplus13-desktop/private-firmware.json']))
    import hashlib
    for name,digest in data.items():
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),'usr/lib/firmware/'+name])
        if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('Delivered firmware changed: '+name)
    ssc_files=m.get('ssc_probe_files',{})
    required_ssc={'usr/lib/oneplus13-ssc/phone-ssc-probe-arm64.tar.gz',
                  'usr/lib/oneplus13-ssc/phone-ssc-probe-manifest.json',
                  'usr/lib/oneplus13-ssc/verification.json',
                  'usr/libexec/oneplus13-ram-ssc-metadata.py'}
    if set(ssc_files)!=required_ssc or m.get('ssc_probe_auto_started') is not False:
        raise ValueError('Missing isolated SSC metadata probe')
    for name,digest in ssc_files.items():
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('Delivered SSC input changed')
    sample_files=m.get('ssc_sample_files',{})
    if set(sample_files)!={'usr/lib/oneplus13-ssc/phone-ssc-samples-arm64.tar.gz',
        'usr/lib/oneplus13-ssc/phone-ssc-samples-manifest.json','usr/libexec/oneplus13-ram-ssc-samples.py'} or m.get('ssc_samples_auto_started') is not False:
        raise ValueError('Missing separately gated SSC sample probe')
    for name,digest in sample_files.items():
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('Delivered sample input changed')
    runtime_files=m.get('sensor_runtime_files',{})
    required_runtime={'usr/lib/oneplus13-sensors/'+n for n in ('phone-sensor-inputs.tar.gz','phone-sensor-inputs-manifest.json','runtime-bindings.json')}
    required_runtime|={'usr/libexec/oneplus13-'+n for n in ('ram-sensor-attach-only.py','ram-sensor-fileserver.py','ram-sensor-discovery.py','ram-ssc-proxy.py','image-sensors-check.py')}
    required_runtime.add('usr/bin/strace')
    if set(runtime_files)!=required_runtime or m.get('sensor_runtime_host_private_inputs_required') is not False or m.get('sensor_runtime_auto_started') is not False:
        raise ValueError('Missing self-contained sensor runtime')
    for name,digest in runtime_files.items():
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('Delivered sensor runtime changed')
    sensor_desktop_files=m.get('sensor_desktop_files',{})
    required_sensor_desktop={
        'usr/libexec/oneplus13-ram-sensor-desktop.py',
        'etc/udev/rules.d/10-oneplus13-fastrpc.rules',
        'usr/share/dbus-1/system.d/net.hadess.SensorProxy.conf',
        'usr/share/polkit-1/actions/net.hadess.SensorProxy.policy',
        'etc/polkit-1/rules.d/49-oneplus13-sensor-proxy.rules',
        'usr/share/dbus-1/system-services/net.hadess.SensorProxy.service',
        'usr/lib/systemd/system/oneplus13-sensor-setup.service',
        'usr/lib/systemd/system/oneplus13-sensor-fileserver.service',
        'usr/lib/systemd/system/oneplus13-sensor-proxy.service',
        'usr/lib/systemd/system/oneplus13-sensor-stack.target'}
    if (set(sensor_desktop_files)!=required_sensor_desktop or
            m.get('sensor_desktop_stack_manual_start_after_adsp') is not True or
            m.get('sensor_desktop_auto_rotation_enabled') is not True or
            m.get('sensor_desktop_hardware_qualified') is not False):
        raise ValueError('Missing reviewed desktop sensor integration')
    for name,digest in sensor_desktop_files.items():
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('Delivered desktop sensor input changed')
    container_files=m.get('container_files',{})
    container_userspace=validate_container_profile(m)
    for name,digest in container_files.items():
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('Delivered container input changed')
    config=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),
                                   'usr/lib/oneplus13-container/kernel.config'],text=True)
    import importlib
    importlib.import_module('container-kernel').check_config(config)
    for name in (['usr/bin/docker','usr/bin/dockerd','usr/sbin/xtables-nft-multi','usr/sbin/xtables-legacy-multi'] if container_userspace else []):
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if blob[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(blob[18:20],'little')!=183:
            raise ValueError('Missing native container binary: '+name)
    audio_files=m.get('experimental_audio_files',{})
    for name in ['usr/bin/aplay','usr/bin/amixer']:
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if blob[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(blob[18:20],'little')!=183:
            raise ValueError('Delivered native ALSA tool missing: '+name)
    for name in ['usr/bin/qmicli','usr/bin/media-ctl','usr/bin/v4l2-ctl','usr/sbin/ModemManager']:
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if blob[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(blob[18:20],'little')!=183:
            raise ValueError('Delivered modem/camera userspace tool missing: '+name)
    if len(audio_files)!=4 or m.get('audio_auto_started') is not False or m.get('audio_hardware_qualified') is not False:
        raise ValueError('Missing gated audio candidate')
    for name,digest in audio_files.items():
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('Delivered audio input changed')
    desktop_audio_files=m.get('desktop_audio_files',{})
    required_desktop_audio={'usr/share/alsa/ucm2/conf.d/sm8750/OnePlus13-Audio-Probe.conf',
        'usr/share/alsa/ucm2/Qualcomm/sm8750/OnePlus13/HiFi.conf',
        'usr/lib/oneplus13-audio/95-oneplus13-desktop-audio.conf',
        'usr/libexec/oneplus13-ram-desktop-audio.py','usr/libexec/oneplus13-ram-desktop-volume-check.py',
        'usr/libexec/oneplus13-first-user-audio.py',
        'usr/lib/systemd/system/oneplus13-first-user-audio.service',
        'usr/lib/oneplus13-audio/desktop-bindings.json'}
    if set(desktop_audio_files)!=required_desktop_audio or m.get('desktop_audio_auto_started') is not False:
        raise ValueError('Missing explicitly gated desktop audio files')
    for name,digest in desktop_audio_files.items():
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('Delivered desktop audio changed')
    first_audio=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),
        'usr/lib/systemd/system/oneplus13-first-user-audio.service'],text=True)
    if ('ConditionKernelCommandLine=oneplus13.first_user_audio=1\n' not in first_audio or
            'BindsTo=user@1000.service\n' not in first_audio or 'ExecStopPost=' not in first_audio):
        raise ValueError('First-user RAM audio gate/cleanup changed')
    timezone=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),'etc/timezone'],text=True)
    if timezone!='Asia/Shanghai\n':raise ValueError('Wrong phone timezone')
    microphone_files=m.get('experimental_microphone_files',{})
    if set(microphone_files)!={'usr/lib/firmware/qcom/sm8750/OnePlus13-Audio-Probe-tplg.bin',
            'usr/libexec/oneplus13-ram-microphone-check.py','usr/libexec/oneplus13-ram-acoustic-check.py'} or m.get('microphone_auto_started') is not False:
        raise ValueError('Missing manually gated alternative microphone runtime')
    for name,digest in microphone_files.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Microphone runtime changed')
    raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),'usr/bin/arecord'])
    if raw[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(raw[18:20],'little')!=183:raise ValueError('Native capture tool missing')
    artifacts=m.get('microphone_probe_artifacts',{})
    if set(artifacts)!={'boot-microphone-ram-experimental.img','sm8750-oneplus13-microphone-probe.dtb',
        'microphone-probe-manifest.json','microphone-dtb-review.json'}:raise ValueError('Incomplete alternative microphone boot')
    for name,digest in artifacts.items():
        if sha256(root/name)!=digest:raise ValueError('Alternative microphone artifact changed')
    probe=json.loads((root/'microphone-probe-manifest.json').read_text())
    for key in ('source_commit','kernel_release','rootfs_sha256','modules_sha256','kernel_sha256','experimental_microphone_files'):
        if probe[key]!=m[key]:raise ValueError('Alternative image component mismatch')
    if probe['default_boot_image_sha256']!=m['boot_image_sha256'] or probe['microphone_hardware_qualified'] is not False:
        raise ValueError('Alternative image changed default speaker identity or acceptance')
    import re
    nonce=probe['session_identity_nonce']
    if not re.fullmatch('[a-f0-9]{24}',nonce) or nonce==m['session_identity_nonce']:
        raise ValueError('Alternative image requires a separate session identity')
    if probe['cmdline']!=m['cmdline'].replace('oneplus13.session='+m['session_identity_nonce'],'oneplus13.session='+nonce)+' oneplus13.audio_probe=1':
        raise ValueError('Alternative image changed qualified boot arguments')
    if sha256(root/'boot-microphone-ram-experimental.img')!=probe['boot_image_sha256'] or sha256(root/'sm8750-oneplus13-microphone-probe.dtb')!=probe['dtb_sha256']:
        raise ValueError('Alternative image manifest hashes disagree')
    from common import boot_sections
    from stock import Fdt
    default_sections=boot_sections((root/'boot-desktop-ram-experimental.img').read_bytes())
    probe_sections=boot_sections((root/'boot-microphone-ram-experimental.img').read_bytes())
    import importlib
    nfc_dtb=importlib.import_module('nfc-dtb')
    default_expected,_=nfc_dtb.derive((Path(__file__).resolve().parents[2]/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    default_tree=Fdt(default_expected);default_tree.nodes['/chosen']['bootargs']=(m['cmdline']+'\0').encode()
    if (default_sections['dtb']!=(root/'sm8750-oneplus-dodge-abl.dtb').read_bytes() or
            Fdt(default_sections['dtb']).nodes!=default_tree.nodes):
        raise ValueError('Reviewed default NFC DTB mismatch')
    for key in ('kernel','ramdisk'):
        if probe_sections[key]!=default_sections[key]:raise ValueError('Alternative kernel/initramfs changed')
    mic_dtb=importlib.import_module('microphone-dtb')
    expected,_=mic_dtb.derive((Path(__file__).resolve().parents[2]/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    expected_tree=Fdt(expected);expected_tree.nodes['/chosen']['bootargs']=(probe['cmdline']+'\0').encode()
    if probe_sections['dtb']!=(root/'sm8750-oneplus13-microphone-probe.dtb').read_bytes() or Fdt(probe_sections['dtb']).nodes!=expected_tree.nodes:
        raise ValueError('Alternative microphone DTB mismatch')
    policy=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),
        'etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf'],text=True)
    if 'install snd_soc_aw88461 /bin/false\n' not in policy:
        raise ValueError('Experimental amplifier must require manual activation')
    camera_runtime=m.get('camera_probe_runtime_files',{})
    if set(camera_runtime)!={'usr/libexec/oneplus13-ram-camera-chip-id.py'} or m.get('camera_probe_auto_started') is not False:
        raise ValueError('Camera runtime must be explicit and default-off')
    for name,digest in camera_runtime.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Camera runtime source hash changed')
    capture_runtime=m.get('camera_capture_runtime_files',{})
    if (set(capture_runtime)!={'usr/libexec/oneplus13-ram-camera-capture.py'} or
            m.get('camera_capture_auto_started') is not False or
            m.get('camera_capture_hardware_qualified') is not False):
        raise ValueError('Camera capture runtime must be explicit and default-off')
    for name,digest in capture_runtime.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Camera capture runtime hash changed')
    camera_files=m.get('camera_probe_artifacts',{})
    camera_names={'boot-camera-chip-id-ram-experimental.img',
                  'sm8750-oneplus13-camera-chip-id-probe.dtb',
                  'camera-probe-manifest.json','camera-probe-dtb-review.json'}
    if set(camera_files)!=camera_names:raise ValueError('Missing manual camera probe artifacts')
    for name,digest in camera_files.items():
        if sha256(root/name)!=digest:raise ValueError('Camera artifact changed')
    camera=json.loads((root/'camera-probe-manifest.json').read_text())
    if (camera['scope']!='SEPARATE_MANUAL_CAMERA_CHIP_ID_RAM_BOOT_NEVER_FLASH' or
            camera.get('camera_capture_qualified') is not False or
            camera['default_boot_image_sha256']!=m['boot_image_sha256'] or
            camera['rootfs_sha256']!=m['rootfs_sha256'] or
            camera['source_commit']!=m['source_commit']):
        raise ValueError('Camera alternative identity or qualification changed')
    cam_nonce=camera['session_identity_nonce']
    if not re.fullmatch('[a-f0-9]{24}',cam_nonce) or cam_nonce in (nonce,m['session_identity_nonce']):
        raise ValueError('Camera alternative needs a separate session identity')
    if camera['cmdline']!=m['cmdline'].replace('oneplus13.session='+m['session_identity_nonce'],
            'oneplus13.session='+cam_nonce)+' oneplus13.audio_probe=1 oneplus13.camera_probe=1':
        raise ValueError('Camera alternative boot arguments changed')
    if (sha256(root/'boot-camera-chip-id-ram-experimental.img')!=camera['boot_image_sha256'] or
            sha256(root/'sm8750-oneplus13-camera-chip-id-probe.dtb')!=camera['dtb_sha256']):
        raise ValueError('Camera alternative manifest hashes disagree')
    cam_sections=boot_sections((root/'boot-camera-chip-id-ram-experimental.img').read_bytes())
    for key in ('kernel','ramdisk'):
        if cam_sections[key]!=default_sections[key]:raise ValueError('Camera kernel/initramfs changed')
    camera_dtb=importlib.import_module('camera-probe-dtb')
    cam_expected,_=camera_dtb.derive((Path(__file__).resolve().parents[2]/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    cam_tree=Fdt(cam_expected);cam_tree.nodes['/chosen']['bootargs']=(camera['cmdline']+'\0').encode()
    if (cam_sections['dtb']!=(root/'sm8750-oneplus13-camera-chip-id-probe.dtb').read_bytes() or
            Fdt(cam_sections['dtb']).nodes!=cam_tree.nodes):
        raise ValueError('Camera alternative DTB changed')
    for module in ('s5kjn5','i2c_qcom_cci'):
        if 'install '+module+' /bin/false\n' not in policy:raise ValueError('Camera modules must remain manual')
    for executable in ('usr/bin/media-ctl','usr/bin/v4l2-ctl'):
        blob=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),executable])
        if blob[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(blob[18:20],'little')!=183:
            raise ValueError('Native camera userspace tool missing')
    capture_files=m.get('camera_capture_artifacts',{})
    capture_names={'boot-camera-capture-ram-experimental.img',
                   'sm8750-oneplus13-camera-capture.dtb',
                   'camera-capture-manifest.json','camera-capture-dtb-review.json'}
    if set(capture_files)!=capture_names:raise ValueError('Missing manual camera capture artifacts')
    for name,digest in capture_files.items():
        if sha256(root/name)!=digest:raise ValueError('Camera capture artifact changed')
    capture=json.loads((root/'camera-capture-manifest.json').read_text())
    if (capture['scope']!='SEPARATE_MANUAL_CAMERA_CPHY_CAPTURE_RAM_BOOT_NEVER_FLASH' or
            capture.get('camera_capture_pipeline_present') is not True or
            capture.get('camera_capture_qualified') is not False or
            capture.get('camera_capture_auto_started') is not False or
            capture['default_boot_image_sha256']!=m['boot_image_sha256'] or
            capture['rootfs_sha256']!=m['rootfs_sha256'] or
            capture['source_commit']!=m['source_commit']):
        raise ValueError('Camera capture alternative identity or qualification changed')
    capture_nonce=capture['session_identity_nonce']
    if (not re.fullmatch('[a-f0-9]{24}',capture_nonce) or
            capture_nonce in (nonce,m['session_identity_nonce'],cam_nonce)):
        raise ValueError('Camera capture needs a separate session identity')
    if capture['cmdline']!=m['cmdline'].replace('oneplus13.session='+m['session_identity_nonce'],
            'oneplus13.session='+capture_nonce)+' oneplus13.camera_capture=1':
        raise ValueError('Camera capture boot arguments changed')
    if (sha256(root/'boot-camera-capture-ram-experimental.img')!=capture['boot_image_sha256'] or
            sha256(root/'sm8750-oneplus13-camera-capture.dtb')!=capture['dtb_sha256']):
        raise ValueError('Camera capture manifest hashes disagree')
    capture_sections=boot_sections((root/'boot-camera-capture-ram-experimental.img').read_bytes())
    for key in ('kernel','ramdisk'):
        if capture_sections[key]!=default_sections[key]:raise ValueError('Camera capture kernel/initramfs changed')
    capture_dtb=importlib.import_module('camera-capture-dtb')
    capture_expected,_=capture_dtb.derive((Path(__file__).resolve().parents[2]/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    capture_tree=Fdt(capture_expected);capture_tree.nodes['/chosen']['bootargs']=(capture['cmdline']+'\0').encode()
    if (capture_sections['dtb']!=(root/'sm8750-oneplus13-camera-capture.dtb').read_bytes() or
            Fdt(capture_sections['dtb']).nodes!=capture_tree.nodes):
        raise ValueError('Camera capture DTB changed')
    modem_files=m.get('modem_probe_artifacts',{})
    modem_names={'boot-modem-gnss-ram-experimental.img','sm8750-oneplus13-modem-gnss-probe.dtb',
                 'modem-gnss-probe-manifest.json','modem-gnss-dtb-review.json',
                 'modem-firmware.tar.gz','modem-firmware-review.json'}
    if (set(modem_files)!=modem_names or m.get('modem_probe_auto_started') is not False or
            m.get('modem_hardware_functional_qualified') is not False or
            m.get('gnss_hardware_functional_qualified') is not False):
        raise ValueError('Missing manual modem/GNSS artifacts')
    for name,digest in modem_files.items():
        if sha256(root/name)!=digest:raise ValueError('Modem/GNSS artifact changed')
    modem_manifest=json.loads((root/'modem-gnss-probe-manifest.json').read_text())
    if (modem_manifest['scope']!='SEPARATE_MANUAL_MPSS_GNSS_RAM_BOOT_NEVER_FLASH' or
            modem_manifest.get('remote_processor_started') is not False or
            modem_manifest.get('modem_service_qualified') is not False or
            modem_manifest.get('gnss_service_qualified') is not False or
            modem_manifest['default_boot_image_sha256']!=m['boot_image_sha256'] or
            modem_manifest['rootfs_sha256']!=m['rootfs_sha256'] or
            modem_manifest['source_commit']!=m['source_commit']):
        raise ValueError('Modem/GNSS alternative identity or qualification changed')
    modem_nonce=modem_manifest['session_identity_nonce']
    if (not re.fullmatch('[a-f0-9]{24}',modem_nonce) or
            modem_nonce in (nonce,m['session_identity_nonce'],cam_nonce,capture_nonce)):
        raise ValueError('Modem/GNSS alternative needs a separate session identity')
    expected_modem_cmdline=m['cmdline'].replace('oneplus13.session='+m['session_identity_nonce'],
        'oneplus13.session='+modem_nonce)+' oneplus13.audio_probe=1 oneplus13.camera_probe=1 oneplus13.video_probe=1 oneplus13.modem_probe=1'
    if modem_manifest['cmdline']!=expected_modem_cmdline:
        raise ValueError('Modem/GNSS boot arguments changed')
    if (sha256(root/'boot-modem-gnss-ram-experimental.img')!=modem_manifest['boot_image_sha256'] or
            sha256(root/'sm8750-oneplus13-modem-gnss-probe.dtb')!=modem_manifest['dtb_sha256']):
        raise ValueError('Modem/GNSS manifest hashes disagree')
    modem_sections=boot_sections((root/'boot-modem-gnss-ram-experimental.img').read_bytes())
    for key in ('kernel','ramdisk'):
        if modem_sections[key]!=default_sections[key]:raise ValueError('Modem/GNSS kernel/initramfs changed')
    modem_dtb=importlib.import_module('modem-gnss-dtb')
    modem_expected,_=modem_dtb.derive((Path(__file__).resolve().parents[2]/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    modem_tree=Fdt(modem_expected);modem_tree.nodes['/chosen']['bootargs']=(modem_manifest['cmdline']+'\0').encode()
    if (modem_sections['dtb']!=(root/'sm8750-oneplus13-modem-gnss-probe.dtb').read_bytes() or
            Fdt(modem_sections['dtb']).nodes!=modem_tree.nodes):
        raise ValueError('Modem/GNSS DTB changed')
    combined_files=m.get('camera_gnss_artifacts',{})
    combined_names={'boot-camera-gnss-ram-experimental.img','sm8750-oneplus13-camera-gnss.dtb',
                    'camera-gnss-manifest.json','camera-gnss-dtb-review.json'}
    if set(combined_files)!=combined_names:
        raise ValueError('Missing camera/GNSS combined artifacts')
    for name,digest in combined_files.items():
        if sha256(root/name)!=digest:raise ValueError('Camera/GNSS combined artifact changed')
    camera_gnss=json.loads((root/'camera-gnss-manifest.json').read_text())
    if (camera_gnss['scope']!='SEPARATE_MANUAL_CAMERA_CAPTURE_AND_MPSS_GNSS_RAM_BOOT' or
            camera_gnss.get('camera_capture_pipeline_present') is not True or
            camera_gnss.get('camera_capture_qualified') is not False or
            camera_gnss.get('modem_hardware_functional_qualified') is not False or
            camera_gnss.get('gnss_hardware_functional_qualified') is not False or
            camera_gnss['default_boot_image_sha256']!=m['boot_image_sha256'] or
            camera_gnss['rootfs_sha256']!=m['rootfs_sha256'] or
            camera_gnss['source_commit']!=m['source_commit']):
        raise ValueError('Camera/GNSS combined identity or qualification changed')
    camera_gnss_nonce=camera_gnss['session_identity_nonce']
    if (not re.fullmatch('[a-f0-9]{24}',camera_gnss_nonce) or
            camera_gnss_nonce in (nonce,m['session_identity_nonce'],cam_nonce,capture_nonce,modem_nonce)):
        raise ValueError('Camera/GNSS needs a separate session identity')
    expected_combined_cmdline=m['cmdline'].replace('oneplus13.session='+m['session_identity_nonce'],
        'oneplus13.session='+camera_gnss_nonce)+' oneplus13.audio_probe=1 oneplus13.camera_probe=1 oneplus13.video_probe=1 oneplus13.modem_probe=1 oneplus13.camera_capture=1'
    if camera_gnss['cmdline']!=expected_combined_cmdline:
        raise ValueError('Camera/GNSS combined boot arguments changed')
    if (sha256(root/'boot-camera-gnss-ram-experimental.img')!=camera_gnss['boot_image_sha256'] or
            sha256(root/'sm8750-oneplus13-camera-gnss.dtb')!=camera_gnss['dtb_sha256']):
        raise ValueError('Camera/GNSS manifest hashes disagree')
    combined_sections=boot_sections((root/'boot-camera-gnss-ram-experimental.img').read_bytes())
    for key in ('kernel','ramdisk'):
        if combined_sections[key]!=default_sections[key]:raise ValueError('Camera/GNSS kernel/initramfs changed')
    combined_dtb=importlib.import_module('camera-capture-modem-dtb')
    combined_expected,_=combined_dtb.derive((Path(__file__).resolve().parents[2]/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    combined_tree=Fdt(combined_expected);combined_tree.nodes['/chosen']['bootargs']=(camera_gnss['cmdline']+'\0').encode()
    if (combined_sections['dtb']!=(root/'sm8750-oneplus13-camera-gnss.dtb').read_bytes() or
            Fdt(combined_sections['dtb']).nodes!=combined_tree.nodes):
        raise ValueError('Camera/GNSS combined DTB changed')
    peripheral_runtime=m.get('peripheral_probe_runtime_files',{})
    required_peripheral_runtime={
        'usr/libexec/oneplus13-ram-ir-check.py',
        'usr/libexec/oneplus13-ram-usb-role-check.py',
        'usr/libexec/oneplus13-ram-usb-native-host-check.py',
        'usr/libexec/oneplus13-ram-usb-powered-native-check.py',
        'usr/libexec/oneplus13-ram-usb-supply-check.py',
        'usr/libexec/oneplus13-usb-filesystem-modules.py',
        'usr/libexec/oneplus13-ram-suspend-check.py'}
    if (set(peripheral_runtime)!=required_peripheral_runtime or
            m.get('peripheral_probe_auto_started') is not False):
        raise ValueError('Missing manual peripheral probe runtime')
    for name,digest in peripheral_runtime.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Manual peripheral runtime changed')
    storage_guard_sources={
        'usr/libexec/oneplus13-runtime-guard':'devices/oneplus13/desktop/runtime-guard',
        'usr/libexec/oneplus13-readonly-block.py':'tools/oneplus13/readonly-block.py',
        'usr/lib/udev/rules.d/09-oneplus13-ram-readonly.rules':'devices/oneplus13/desktop/09-oneplus13-ram-readonly.rules'}
    storage_guard_runtime=m.get('storage_guard_runtime_files',{})
    if (set(storage_guard_runtime)!=set(storage_guard_sources)
            or m.get('storage_guard_nonblocking_readonly_ioctl') is not True):
        raise ValueError('Missing nonblocking readonly storage protection')
    for name,source in storage_guard_sources.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=storage_guard_runtime[name] or sha256(repo/source)!=storage_guard_runtime[name]:
            raise ValueError('Storage protection differs from source')
    automatic_sources={
        'usr/libexec/oneplus13-auto-otg.py':'tools/oneplus13/auto-otg.py',
        'usr/lib/systemd/system/oneplus13-auto-otg.service':'devices/oneplus13/desktop/oneplus13-auto-otg.service',
        'usr/lib/udev/rules.d/11-oneplus13-auto-usb.rules':'devices/oneplus13/desktop/11-oneplus13-auto-usb.rules',
        'usr/lib/udev/rules.d/99-oneplus13-auto-usb-readonly.rules':'devices/oneplus13/desktop/99-oneplus13-auto-usb-readonly.rules'}
    automatic_runtime=m.get('automatic_usb_runtime_files',{})
    if set(automatic_runtime)!=set(automatic_sources) or m.get('automatic_usb_service_conditional') is not True:
        raise ValueError('Missing conditional desktop automatic USB runtime')
    for name,source in automatic_sources.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=automatic_runtime[name] or sha256(repo/source)!=automatic_runtime[name]:
            raise ValueError('Automatic USB runtime differs from source')
    link=subprocess.check_output(['unsquashfs','-ll',str(root/'rootfs.squashfs'),
        'etc/systemd/system/multi-user.target.wants/oneplus13-auto-otg.service'],text=True)
    if ' -> /usr/lib/systemd/system/oneplus13-auto-otg.service' not in link:
        raise ValueError('Automatic USB service is not enabled conditionally')
    auto_files=m.get('automatic_usb_artifacts',{})
    if set(auto_files)!={'boot-automatic-usb-ram-experimental.img','sm8750-oneplus13-automatic-usb.dtb',
                        'automatic-usb-manifest.json','automatic-usb-dtb-review.json'}:
        raise ValueError('Missing automatic USB desktop alternative')
    for name,digest in auto_files.items():
        if sha256(root/name)!=digest:raise ValueError('Automatic USB artifact changed')
    automatic=json.loads((root/'automatic-usb-manifest.json').read_text())
    if ('oneplus13.first_user_audio=1' not in automatic['cmdline'].split() or
            automatic.get('desktop_audio_first_user_conditional') is not True):
        raise ValueError('Automatic desktop first-user audio gate missing')
    verify_automatic_boot_arguments(camera_gnss,automatic)
    auto_sections=boot_sections((root/automatic['boot_image_file']).read_bytes())
    auto_expected,_=importlib.import_module('camera-gnss-ir-usb-dtb').derive(
        (repo/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    auto_tree=Fdt(auto_expected)
    auto_tree.nodes['/chosen']['bootargs']=(automatic['cmdline']+'\0').encode()
    if (auto_sections['kernel']!=default_sections['kernel'] or auto_sections['ramdisk']!=default_sections['ramdisk']
            or auto_sections['dtb']!=(root/automatic['dtb_file']).read_bytes()
            or Fdt(auto_sections['dtb']).nodes!=auto_tree.nodes):
        raise ValueError('Automatic USB boot sections or role-switch DTB differ')
    target_files=m.get('targets_ir_usb_artifacts',{})
    target_names={'boot-targets-ir-usb-ram-experimental.img',
                  'sm8750-oneplus13-targets-ir-usb.dtb',
                  'targets-ir-usb-manifest.json','targets-ir-usb-dtb-review.json'}
    if (set(target_files)!=target_names or
            m.get('target_ir_usb_auto_started') is not False or
            m.get('target_ir_optical_output_qualified') is not False or
            m.get('target_usb_host_peripheral_qualified') is not False):
        raise ValueError('Missing manual infrared/USB-role alternative')
    for name,digest in target_files.items():
        if sha256(root/name)!=digest:raise ValueError('Infrared/USB-role artifact changed')
    targets_manifest=json.loads((root/'targets-ir-usb-manifest.json').read_text())
    if (targets_manifest['scope']!='MANUAL_CAMERA_GNSS_IR_USB_ROLE_RAM_BOOT' or
            targets_manifest.get('ir_probe_auto_started') is not False or
            targets_manifest.get('usb_host_probe_auto_started') is not False or
            targets_manifest.get('optical_output_qualified') is not False or
            targets_manifest.get('usb_host_peripheral_receipt') is not False or
            targets_manifest['default_boot_image_sha256']!=m['boot_image_sha256'] or
            targets_manifest['rootfs_sha256']!=m['rootfs_sha256'] or
            targets_manifest['source_commit']!=m['source_commit']):
        raise ValueError('Infrared/USB-role identity or qualification changed')
    targets_nonce=targets_manifest['session_identity_nonce']
    if (not re.fullmatch('[a-f0-9]{24}',targets_nonce) or
            targets_nonce in (nonce,m['session_identity_nonce'],cam_nonce,capture_nonce,modem_nonce,camera_gnss_nonce)):
        raise ValueError('Infrared/USB-role needs a separate session identity')
    verify_targets_boot_arguments(camera_gnss, targets_manifest)
    if (sha256(root/'boot-targets-ir-usb-ram-experimental.img')!=targets_manifest['boot_image_sha256'] or
            sha256(root/'sm8750-oneplus13-targets-ir-usb.dtb')!=targets_manifest['dtb_sha256']):
        raise ValueError('Infrared/USB-role manifest hashes disagree')
    target_sections=boot_sections((root/'boot-targets-ir-usb-ram-experimental.img').read_bytes())
    for key in ('kernel','ramdisk'):
        if target_sections[key]!=default_sections[key]:raise ValueError('Infrared/USB-role kernel/initramfs changed')
    target_dtb=importlib.import_module('camera-gnss-ir-usb-dtb')
    target_expected,_=target_dtb.derive((Path(__file__).resolve().parents[2]/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    target_tree=Fdt(target_expected);target_tree.nodes['/chosen']['bootargs']=(targets_manifest['cmdline']+'\0').encode()
    if (target_sections['dtb']!=(root/'sm8750-oneplus13-targets-ir-usb.dtb').read_bytes() or
            Fdt(target_sections['dtb']).nodes!=target_tree.nodes):
        raise ValueError('Infrared/USB-role DTB changed')
    nfc_files=m.get('nfc_files',{})
    if (set(nfc_files)!={'usr/lib/systemd/system/oneplus13-nfc.service',
                         'usr/libexec/oneplus13-ram-nfc-poll.py'} or
            m.get('nfc_service_auto_started') is not True or
            m.get('nfc_poll_auto_started') is not False or
            m.get('nfc_poll_requires_host_overlay') is not False or
            m.get('nfc_controller_bind_hardware_qualified') is not False or
            m.get('nfc_tag_poll_hardware_qualified') is not False):
        raise ValueError('Missing reviewed default NFC service')
    for name,digest in nfc_files.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        source=(Path(__file__).resolve().parents[2]/('tools/oneplus13/ram-nfc-poll.py'
                if name.endswith('ram-nfc-poll.py') else 'devices/oneplus13/nfc/oneplus13-nfc.service'))
        if hashlib.sha256(raw).hexdigest()!=digest or sha256(source)!=digest:
            raise ValueError('NFC runtime differs from manifest or checked-out source')
    nfc_review=m.get('nfc_dtb_review',{})
    if (nfc_review.get('output_sha256')!=hashlib.sha256(default_expected).hexdigest() or
            nfc_review.get('default_boot_tree_changed') is not True):
        raise ValueError('NFC DTB review binding changed')
    for module in ('nfc','nci','nxp_nci','nxp_nci_i2c'):
        if 'install '+module+' /bin/false\n' in policy:
            raise ValueError('Reviewed NFC module remains denied')
    modem_files=m.get('modem_files',{})
    if (set(modem_files)!={'usr/lib/systemd/system/oneplus13-modem.service'} or
            m.get('modem_service_auto_started') is not True or
            m.get('modem_hardware_functional_qualified') is not False or
            m.get('gnss_hardware_functional_qualified') is not False):
        raise ValueError('Missing reviewed modem service')
    for name,digest in modem_files.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Modem service changed')
    video_files=m.get('video_probe_artifacts',{})
    video_names={'boot-video-ram-experimental.img','sm8750-oneplus13-video-probe.dtb',
                 'video-probe-manifest.json','video-probe-dtb-review.json'}
    if set(video_files)!=video_names or m.get('video_probe_auto_started') is not False:
        raise ValueError('Missing or auto-started manual video probe artifacts')
    for name,digest in video_files.items():
        if sha256(root/name)!=digest:raise ValueError('Video artifact changed')
    video=json.loads((root/'video-probe-manifest.json').read_text())
    if (video['scope']!='SEPARATE_MANUAL_IRIS_VIDEO_RAM_BOOT_NEVER_FLASH' or
            video.get('firmware_authentication_qualified') is not False or
            video.get('hardware_decode_qualified') is not False or
            video['default_boot_image_sha256']!=m['boot_image_sha256'] or
            video['rootfs_sha256']!=m['rootfs_sha256'] or
            video['source_commit']!=m['source_commit']):
        raise ValueError('Video alternative identity or qualification changed')
    video_nonce=video['session_identity_nonce']
    if not re.fullmatch('[a-f0-9]{24}',video_nonce) or video_nonce in (nonce,m['session_identity_nonce'],cam_nonce):
        raise ValueError('Video alternative needs a separate session identity')
    if video['cmdline']!=m['cmdline'].replace('oneplus13.session='+m['session_identity_nonce'],
            'oneplus13.session='+video_nonce)+' oneplus13.audio_probe=1 oneplus13.camera_probe=1 oneplus13.video_probe=1':
        raise ValueError('Video alternative boot arguments changed')
    if (sha256(root/'boot-video-ram-experimental.img')!=video['boot_image_sha256'] or
            sha256(root/'sm8750-oneplus13-video-probe.dtb')!=video['dtb_sha256']):
        raise ValueError('Video alternative manifest hashes disagree')
    video_sections=boot_sections((root/'boot-video-ram-experimental.img').read_bytes())
    for key in ('kernel','ramdisk'):
        if video_sections[key]!=default_sections[key]:raise ValueError('Video kernel/initramfs changed')
    video_dtb=importlib.import_module('video-probe-dtb')
    video_expected,_=video_dtb.derive((Path(__file__).resolve().parents[2]/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes())
    video_tree=Fdt(video_expected);video_tree.nodes['/chosen']['bootargs']=(video['cmdline']+'\0').encode()
    if (video_sections['dtb']!=(root/'sm8750-oneplus13-video-probe.dtb').read_bytes() or
            Fdt(video_sections['dtb']).nodes!=video_tree.nodes):
        raise ValueError('Video alternative DTB changed')
    if (video['video_firmware']!='usr/lib/firmware/'+video_dtb.FIRMWARE or
            video['video_firmware_sha256']!=video_dtb.FIRMWARE_SHA256 or
            data.get(video_dtb.FIRMWARE)!=video_dtb.FIRMWARE_SHA256):
        raise ValueError('Video alternative must use the exact phone firmware')
    if 'install qcom_iris /bin/false\n' not in policy:
        raise ValueError('Video module must remain manual')
    video_runtime=m.get('video_probe_runtime_files',{})
    video_runtime_names={'usr/libexec/oneplus13-ram-video-check.py','usr/libexec/oneplus13-decode-one-frame',
                         'usr/libexec/oneplus13-ram-gstreamer-frame.py',
                         'usr/lib/oneplus13-video/synthetic-avc-bars.json','usr/lib/oneplus13-video/runtime-bindings.json'}
    if set(video_runtime)!=video_runtime_names:raise ValueError('Incomplete manual video runtime')
    for name,digest in video_runtime.items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Changed video runtime file')
    video_bindings=json.loads(subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),
        'usr/lib/oneplus13-video/runtime-bindings.json']))
    if video_bindings['kernel_release']!=release or video_bindings['module_policy_sha256']!=hashlib.sha256(policy.encode()).hexdigest():
        raise ValueError('Video runtime kernel/policy binding differs')
    if set(video_bindings['files'])!=video_runtime_names-{'usr/lib/oneplus13-video/runtime-bindings.json'}:
        raise ValueError('Video runtime binding inventory changed')
    for name,digest in video_bindings['files'].items():
        if video_runtime.get(name)!=digest:raise ValueError('Video runtime binding changed')
    for name,digest in video_bindings['module_files'].items():
        raw=subprocess.check_output(['unsquashfs','-cat',str(root/'rootfs.squashfs'),'usr/lib/modules/'+release+'/'+name])
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Video module binding changed')
    if set(video_bindings['live_nodes'])!={video_dtb.VIDEO,video_dtb.MEMORY}:
        raise ValueError('Video live resource inventory changed')
    for path,properties in video_bindings['live_nodes'].items():
        if {key:bytes.fromhex(value) for key,value in properties.items()}!=video_tree.nodes[path]:
            raise ValueError('Video live resource binding changed')
    static={'scope':'COMPLETE_IMAGE_STATIC_PACKAGE_VERIFICATION' ,'pass':True,'source_commit':m['source_commit'],
            'wifi_runtime_files_verified':len(wifi_files),'wifi_runtime_requires_host_overlay':False,
            'rootfs_sha256':m['rootfs_sha256'],'boot_image_sha256':m['boot_image_sha256'],
            'firmware_files_verified':len(data),'kernel_module_release_match':True,
            'container_files_verified':len(container_files),'container_userspace_installed':container_userspace,'container_daemon_auto_started':False,
            'sensor_runtime_files_verified':len(runtime_files),'sensor_runtime_host_private_inputs_required':False,
            'ssc_probe_files_verified':len(ssc_files),'ssc_probe_auto_started':False,
            'ssc_sample_files_verified':len(sample_files),'ssc_samples_auto_started':False,
            'experimental_audio_files_verified':len(audio_files),'audio_auto_started':False,
            'desktop_audio_files_verified':len(desktop_audio_files),'desktop_audio_auto_started':False,
            'phone_timezone':'Asia/Shanghai',
            'experimental_microphone_files_verified':len(microphone_files),'microphone_auto_started':False,
            'experimental_camera_files_verified':len(camera_files),'camera_auto_started':False,
            'camera_probe_runtime_files_verified':len(camera_runtime),
            'camera_capture_runtime_files_verified':len(capture_runtime),'camera_capture_auto_started':False,
            'peripheral_probe_runtime_files_verified':len(peripheral_runtime),
            'peripheral_probe_auto_started':False,
            'targets_ir_usb_files_verified':len(target_files),
            'targets_ir_usb_auto_started':False,
            'nfc_files_verified':len(nfc_files),'nfc_service_auto_started':True,
            'modem_files_verified':len(modem_files),'modem_service_auto_started':True,
            'experimental_video_files_verified':len(video_files),'video_auto_started':False,
            'video_probe_runtime_files_verified':len(video_runtime),
            'qemu_executed':not a.static_only,'hardware_tested':False,'phone_operations':0}
    (root/'complete-verification.json').write_text(json.dumps(static,indent=2)+'\n')
    if a.static_only:
        (root/'SHA256SUMS').write_text(''.join(f'{sha256(x)}  {x.name}\n' for x in sorted(root.iterdir()) if x.is_file() and x.name!='SHA256SUMS'))
        print(json.dumps(static,indent=2));return
    cmd=['qemu-system-aarch64','-machine','virt','-cpu','cortex-a76','-m','6144','-smp','2','-nographic','-no-reboot','-nic','none',
         '-kernel',str(kernel/'Image'),'-initrd',str(root/'initramfs-desktop.cpio.gz'),
         '-drive','file='+str(root/'rootfs.squashfs')+',format=raw,if=none,readonly=on,id=rootfs',
         '-device','virtio-blk-device,drive=rootfs','-append','console=ttyAMA0 '+m['cmdline']+' oneplus13.desktop.qemu=1']
    start=time.monotonic()
    with (root/'qemu-complete.log').open('wb') as log:
        try:rc=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=320).returncode
        except subprocess.TimeoutExpired:rc=124
    text=(root/'qemu-complete.log').read_text(errors='replace')
    # PRIVATE is a historical observer marker for the real calibration handoff,
    # as opposed to the synthetic public-root fixture. This data is now published.
    markers=['ONEPLUS13_DISPLAY_AUTOLOAD_BLOCKED','ONEPLUS13_NATIVE_RUNTIME_GUARDS_OFFLINE_OK',
             'ONEPLUS13_NATIVE_PREFLIGHT_OFFLINE_OK','ONEPLUS13_PRIVATE_NATIVE_HANDOFF_OK',
             'ONEPLUS13_DESKTOP_HANDOFF_OK','ONEPLUS13_QEMU_DEADLINE_FIRED','reboot: Restarting system']
    passed=rc==0 and all(v in text for v in markers) and all(text.index(x)<text.index(y) for x,y in zip(markers,markers[1:]))
    proof={'scope':'COMPLETE_IMAGE_QEMU_HANDOFF_AND_DEADLINE_NOT_HARDWARE_EMULATION','pass':passed,'returncode':rc,
           'source_commit':m['source_commit'],'rootfs_sha256':m['rootfs_sha256'],'boot_image_sha256':m['boot_image_sha256'],
           'kernel_image_sha256':sha256(kernel/'Image'),'elapsed_seconds':round(time.monotonic()-start,1),
           'markers':{v:v in text for v in markers},'phone_operations':0,'hardware_tested':False}
    (root/'qemu-complete-verification.json').write_text(json.dumps(proof,indent=2)+'\n')
    if not passed:raise SystemExit('Exact complete-rootfs QEMU handoff/deadline failed; log preserved')
    (root/'SHA256SUMS').write_text(''.join(f'{sha256(x)}  {x.name}\n' for x in sorted(root.iterdir()) if x.is_file() and x.name!='SHA256SUMS'))
    print(json.dumps(proof,indent=2))

if __name__=='__main__':main()
