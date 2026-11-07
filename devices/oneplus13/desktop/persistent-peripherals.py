#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
"""Initialize the qualified WiFi and SSC paths on the installed ext4 system."""
import runpy
import subprocess
import time
from pathlib import Path


def run(*args, timeout=20):
    return subprocess.run(args, check=True, timeout=timeout, capture_output=True,
                          text=True).stdout.strip()


def wifi():
    run('systemctl', 'start', 'wpa_supplicant.service')
    run('modprobe', 'pwrseq_qcom_wcn')
    time.sleep(12)
    for module in ('mhi', 'pci_pwrctrl_pwrseq', 'ath12k_wifi7'):
        run('modprobe', module, timeout=40)
    for attempt in range(20):
        if 'Interface ' in run('/usr/sbin/iw', 'dev'):
            print('ONEPLUS13_PERSISTENT_WIFI_READY', flush=True)
            return
        time.sleep(1)
    raise RuntimeError('WiFi interface did not appear')


def sensors():
    run('modprobe', '--ignore-install', 'fastrpc')
    run('udevadm', 'settle', '--timeout=5')
    if not Path('/dev/fastrpc-adsp-secure').is_char_device():
        raise RuntimeError('ADSP secure FastRPC device missing')
    # The phone firmware currently exposes the accelerometer and compass but
    # does not publish a default gyroscope UID. Keep gyro out of the persistent
    # SensorProxy claim set so the available accelerometer can still drive
    # orientation.
    source_rule = Path('/etc/udev/rules.d/10-oneplus13-fastrpc.rules')
    if source_rule.is_file():
        rule = source_rule.read_text().replace(
            'ssc-accel ssc-compass ssc-gyro', 'ssc-accel ssc-compass')
        override = Path('/run/udev/rules.d/99-oneplus13-persistent-sensors.rules')
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text(rule)
        run('udevadm', 'control', '--reload-rules')
        run('udevadm', 'trigger', '--subsystem-match=misc')
    for name in ('setup', 'fileserver', 'proxy'):
        directory = Path('/run/systemd/system') / f'oneplus13-sensor-{name}.service.d'
        directory.mkdir(parents=True, exist_ok=True)
        text = ('[Unit]\nConditionPathExists=\n'
                'ConditionPathExists=/run/evidence/rootfs.txt\n'
                'ConditionKernelCommandLine=oneplus13.persistent=1\n')
        if name == 'setup':
            text += ('[Service]\nExecStart=\nExecStart=/usr/libexec/'
                     'oneplus13-ram-sensor-desktop.py setup --persistent\n')
        (directory / '90-persistent.conf').write_text(text)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'start', 'oneplus13-sensor-fileserver.service', timeout=45)
    time.sleep(2)
    run('systemctl', 'start', 'oneplus13-sensor-proxy.service', timeout=45)
    for attempt in range(20):
        value = run('busctl', '--system', 'get-property', 'net.hadess.SensorProxy',
                    '/net/hadess/SensorProxy', 'net.hadess.SensorProxy',
                    'HasAccelerometer')
        if value == 'b true':
            print('ONEPLUS13_PERSISTENT_ACCELEROMETER_READY', flush=True)
            return
        time.sleep(1)
    raise RuntimeError('SSC accelerometer did not appear')


def main():
    hardware = runpy.run_path('/run/oneplus13/persistent/persistent-hardware.py')
    hardware['validate_root'](Path('/'))
    failures = []
    for start in (wifi, sensors):
        try:
            start()
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f'{start.__name__}: {error}', flush=True)
            if isinstance(error, subprocess.CalledProcessError):
                print(error.stdout, error.stderr, flush=True)
            failures.append(start.__name__)
    if failures:
        raise RuntimeError('Peripheral startup failed for ' + ', '.join(failures))


if __name__ == '__main__':
    main()
