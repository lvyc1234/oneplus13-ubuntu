#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Staged, bounded OnePlus 13 phone-only USB VBUS proof."""
import importlib.util
import json
import math
import tempfile
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

BASE = Path('/sys/bus/auxiliary/devices/pmic_glink.power-supply.0')
LEASE = BASE / 'oneplus13_otg_supply_lease'
ARM_CLOCK = Path('/run/oneplus13/combined/r224-arm-clock.private.json')
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')
NAMES = ('oneplus13_otg_ap_enable', 'oneplus13_otg_switch',
         'oneplus13_otg_vbus_enable', 'oneplus13_otg_boost_current')
NEEDED = {'oneplus13.usb_host_probe=1',
          'qcom_battmgr.oneplus13_otg_supply_read=1',
          'qcom_battmgr.oneplus13_otg_supply_lease=1'}


def load_usb():
    spec = importlib.util.spec_from_file_location(
        'r222_usb', '/usr/libexec/oneplus13-ram-usb-role-check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lease_status():
    raw = LEASE.read_text()
    pattern = (r'state=([0-5]) used=([01]) saved_ap=([01]) '
               r'saved_switch=([01]) saved_vbus=([01]) '
               r'saved_current=([0-9]{1,10}) dirty_ap=([01]) '
               r'dirty_switch=([01]) dirty_vbus=([01]) '
               r'dirty_current=([01])\n?')
    match = re.fullmatch(pattern, raw)
    if match is None:
        raise RuntimeError('Supply lease status invalid')
    keys = ('state', 'used', 'saved_ap', 'saved_switch', 'saved_vbus',
            'saved_current', 'dirty_ap', 'dirty_switch', 'dirty_vbus',
            'dirty_current')
    return dict(zip(keys, map(int, match.groups())))


def command(name):
    if name not in {'arm', 'start', 'restore'}:
        raise ValueError('Invalid supply command')
    if os.geteuid() or not Path('/run/oneplus13/ram-session').is_file():
        raise RuntimeError('Protected RAM session required')
    if not NEEDED <= set(Path('/proc/cmdline').read_text().split()):
        raise RuntimeError('Exact supply opt-in absent')
    LEASE.write_text(name + '\n')


def property_values():
    values = {}
    for name in NAMES:
        path = BASE / name
        if not stat.S_ISREG(path.stat().st_mode):
            raise RuntimeError('Supply property is not regular')
        raw = subprocess.run(['cat', str(path)], capture_output=True, text=True,
                             timeout=3, check=False)
        if raw.returncode or re.fullmatch(r'(0|[1-9][0-9]{0,9})\n?', raw.stdout) is None:
            raise RuntimeError('Supply property read failed')
        values[name] = int(raw.stdout)
    return values


def arm_receipt():
    usb = load_usb()
    before_facts = usb.inventory()
    before_role = usb.typec_inventory()
    before_status = usb.firmware_lease_status()
    if before_status != dict(state=0, used=0, dirty_ap=0, dirty_switch=0):
        raise RuntimeError('Old lease is not unused')
    if property_values() != {'oneplus13_otg_ap_enable': 0,
                             'oneplus13_otg_switch': 0,
                             'oneplus13_otg_vbus_enable': 0,
                             'oneplus13_otg_boost_current': 0}:
        raise RuntimeError('Supply baseline is not exact0')
    observation = {
        'role': 'device' if before_facts.get('udc') and
                not usb.host_controller_ready(before_facts) else 'not_device',
        'unused_status': before_status,
        'firmware_baseline': {'oneplus13_otg_ap_enable': 0,
                              'oneplus13_otg_switch': 0},
        'mutation_started': False,
        'typec': before_role,
    }
    with ARM_CLOCK.open('x', encoding='utf-8') as stream:
        os.chmod(ARM_CLOCK, 0o600)
        json.dump(dict(boot_id=BOOT_ID.read_text().strip(), arm_begin_boottime=time.clock_gettime(time.CLOCK_BOOTTIME)), stream)
    command('arm')
    status = lease_status()
    values = property_values()
    if status['state'] != 5 or values['oneplus13_otg_ap_enable'] != 1 or \
            values['oneplus13_otg_switch'] != 1 or \
            values['oneplus13_otg_vbus_enable'] != 0 or \
            values['oneplus13_otg_boost_current'] != 0:
        raise RuntimeError('Supply arm verification failed')
    return {'schema': 'r222-usb-vbus-arm-v1', 'stage': 'arm',
            'preconnected_observation': observation,
            'status': status, 'values': values, 'arm_pass': True,
            'vbus_enabled_before_attachment': False}


def require_armed():
    clock = json.loads(ARM_CLOCK.read_text())
    begin = clock.get('arm_begin_boottime')
    elapsed = time.clock_gettime(time.CLOCK_BOOTTIME) - begin if type(begin) in (int, float) and math.isfinite(begin) else -1
    if (clock.get('boot_id') != BOOT_ID.read_text().strip()
            or not 0 <= elapsed < 105 or lease_status()['state'] != 5):
        raise RuntimeError('This boot arm eligibility expired; no start or re-arm allowed')


def load_native():
    spec = importlib.util.spec_from_file_location('r224_native', '/usr/libexec/oneplus13-ram-usb-native-host-check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def host_receipt():
    usb = load_usb()
    status = lease_status()
    if status['state'] != 5:
        raise RuntimeError('Supply lease is not armed')
    facts = usb.inventory()
    typec = usb.typec_inventory()
    attach_end = time.monotonic() + 90
    while not usb.typec_host_source(typec) and time.monotonic() < attach_end:
        require_armed()
        time.sleep(.5)
        typec = usb.typec_inventory()
    if not usb.typec_host_source(typec):
        raise RuntimeError('Attached OTG sink has no host/source role')
    require_armed()
    mode = usb.mode_paths()
    usb.write(mode, 'host')
    samples = []
    host_end = time.monotonic() + 45
    while True:
        require_armed()
        typec = usb.typec_inventory()
        if not usb.typec_host_source(typec):
            raise RuntimeError('Host/source lost while waiting for xHCI')
        usb.reconcile_host(mode, samples)
        facts = usb.inventory()
        if usb.host_controller_ready(facts):
            break
        if time.monotonic() >= host_end:
            raise RuntimeError('Bound phone xHCI did not start')
        time.sleep(.5)
    if not usb.host_controller_ready(facts):
        raise RuntimeError('Bound phone xHCI did not start')
    typec = usb.typec_inventory()
    if not usb.typec_host_source(typec):
        raise RuntimeError('Type-C host/source was lost before VBUS enable')
    require_armed()
    command('start')
    status = lease_status()
    values = property_values()
    if status['state'] != 1 or values != {
            'oneplus13_otg_ap_enable': 1,
            'oneplus13_otg_switch': 1,
            'oneplus13_otg_vbus_enable': 1,
            'oneplus13_otg_boost_current': 1250}:
        raise RuntimeError('Supply active verification failed')
    end = time.monotonic() + 30
    partitions = []
    while time.monotonic() < end:
        typec = usb.typec_inventory()
        if not usb.typec_host_source(typec):
            raise RuntimeError('Host/source role was lost during storage wait')
        if usb.read(mode) != 'host':
            usb.write(mode, 'host')
        if lease_status()['state'] != 1:
            raise RuntimeError('Supply lease expired during enumeration')
        facts = usb.inventory()
        partitions = load_native().block_candidates(usb)
        if partitions:
            break
        time.sleep(.5)
    proof = None
    block = None
    if partitions:
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()),
            'read-block-child', json.dumps(partitions[0], sort_keys=True)],
            capture_output=True, text=True, timeout=8, check=False)
        if child.returncode == 0 and len(child.stdout) <= 512:
            candidate = json.loads(child.stdout, object_pairs_hook=load_native().unique_object)
            expected = dict(read_bytes=4096, descriptor_read_only=True, block_identity_verified=True, cleanup_complete=True)
            if candidate == expected and all(type(candidate[k]) is type(v) for k, v in expected.items()):
                block = candidate
        if block:
            proof = pinned_storage_probe(usb, load_native(), partitions[0])
    return {
        'schema': 'r222-usb-vbus-host-v1', 'stage': 'host',
        'status': status, 'values': values,
        'host_controller_ready': usb.host_controller_ready(facts),
        'typec': typec,
        'host_wait_samples': samples,
        'usb_devices': facts.get('usb_devices', []),
        'usb_peripherals': facts.get('usb_peripherals', []),
        'usb_storage_bound': facts.get('usb_storage_bound') is True,
        'block_candidates': partitions,
        'storage_read_only': proof, 'block_read_only': block,
        'external_enumeration_pass': bool(facts.get('usb_peripherals')),
        'read_only_io_pass': bool(proof and proof.get('read_only_io_pass')),
    }



def mount_entry(mountpoint):
    rows = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    matches = [row for row in rows if len(row) > 6 and row[4] == str(mountpoint)]
    if len(matches) > 1:
        raise RuntimeError('Ambiguous USB proof mount')
    return matches[0] if matches else None


def pinned_storage_probe(usb, native, expected):
    """Keep one verified USB block FD alive through mount/read/unmount."""
    result = dict(attempts=[], cleanup_complete=False, read_only_io_pass=False)
    if expected not in native.block_candidates(usb):
        raise RuntimeError('USB block identity changed before filesystem probe')
    fd = os.open(expected['device'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    mountpoint = None
    attempted = mounted = False
    attempt = dict(read_bytes=0, read_pass=False, cleanup_complete=False)
    try:
        info = os.fstat(fd)
        actual = str(os.major(info.st_rdev)) + ':' + str(os.minor(info.st_rdev))
        if not stat.S_ISBLK(info.st_mode) or actual != expected['major_minor'] or expected not in native.block_candidates(usb):
            raise RuntimeError('USB mount descriptor identity differs')
        mountpoint = Path(tempfile.mkdtemp(dir='/run/oneplus13', prefix='usb-pinned-'))
        attempted = True
        identified = subprocess.run(['blkid', '-p', '-s', 'TYPE', '-o', 'value',
            '/proc/self/fd/' + str(fd)], pass_fds=(fd,), capture_output=True,
            text=True, timeout=4, check=False)
        fs_type = identified.stdout.strip() if identified.returncode == 0 else ''
        allowed = {'vfat': 'vfat', 'exfat': 'exfat', 'ntfs': 'ntfs3'}
        attempt['filesystem_type'] = fs_type if fs_type in allowed else 'unsupported'
        if fs_type not in allowed:
            raise RuntimeError('External filesystem type unsupported')
        response = subprocess.run(['mount', '--no-canonicalize', '-t', allowed[fs_type], '-o', 'ro,nosuid,nodev,noexec',
            '/proc/self/fd/' + str(fd), str(mountpoint)], pass_fds=(fd,),
            capture_output=True, text=True, timeout=12, check=False)
        mounted = response.returncode == 0
        attempt['mount_returncode'] = response.returncode
        if response.returncode:
            attempt['mount_error_private'] = response.stderr[:1024]
        row = mount_entry(mountpoint)
        def same_source(row):
            return bool(row and row[2] == expected['major_minor'] and 'ro' in row[5].split(',')
                        and expected in native.block_candidates(usb))
        if mounted and same_source(row):
            script = ("import runpy,sys,json; m=runpy.run_path(sys.argv[1]); "
                      "p=m['bounded_regular_files'](sys.argv[2]); "
                      "b=m['read_regular_prefix'](p[0]) if p else b''; "
                      "print(json.dumps({'read_bytes':len(b)}))")
            readback = subprocess.run([sys.executable, '-c', script,
                '/usr/libexec/oneplus13-ram-usb-role-check.py', str(mountpoint)],
                capture_output=True, text=True, timeout=8, check=False)
            if readback.returncode == 0 and len(readback.stdout) <= 128:
                value = json.loads(readback.stdout, object_pairs_hook=native.unique_object)
                if (type(value.get('read_bytes')) is int and 0 < value['read_bytes'] <= 4096
                        and same_source(mount_entry(mountpoint))):
                    attempt.update(read_bytes=value['read_bytes'], source_identity_verified=True)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        attempt['error_class'] = type(error).__name__
    finally:
        try:
            if attempted:
                response = subprocess.run(['umount', str(mountpoint)], capture_output=True,
                    text=True, timeout=12, check=False)
                attempt['unmount_returncode'] = response.returncode
                attempt['cleanup_complete'] = (mount_entry(mountpoint) is None
                    and (response.returncode == 0 or not mounted))
            else:
                attempt['cleanup_complete'] = True
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            attempt['cleanup_complete'] = False
        finally:
            os.close(fd)
        if mountpoint is not None and attempt['cleanup_complete']:
            try:
                os.rmdir(mountpoint)
            except OSError:
                attempt['mountpoint_retained'] = True
    attempt['read_pass'] = (attempt.get('source_identity_verified') is True
                            and attempt['cleanup_complete'] is True)
    result.update(attempts=[attempt], cleanup_complete=attempt['cleanup_complete'],
                  read_only_io_pass=attempt['read_pass'])
    return result


def restore_receipt(mode=None):
    usb = load_usb()
    error = None
    try:
        command('restore')
    except Exception as exc:
        error = type(exc).__name__
    status = lease_status()
    values = property_values()
    role_restored = False  # Physical PC reconnection precedes the separate role stage.
    clean = (error is None and status['used'] == 1
             and status['state'] in (2, 4) and status['dirty_ap'] == 0
             and status['dirty_switch'] == 0 and status['dirty_vbus'] == 0
             and status['dirty_current'] == 0
             and all(value == 0 for value in values.values())
             and status['saved_ap'] == 0 and status['saved_switch'] == 0
             and status['saved_vbus'] == 0 and status['saved_current'] == 0)
    return {'schema': 'r222-usb-vbus-restore-v1', 'stage': 'restore',
            'status': status, 'values': values, 'role_restored': role_restored,
            'cleanup_complete': clean, 'restore_error_class': error}


def role_receipt():
    usb = load_usb()
    before = lease_status()
    saved = dict(zip(NAMES, (before['saved_ap'], before['saved_switch'], before['saved_vbus'], before['saved_current'])))
    if (before['state'] != 2 or before['used'] != 1
            or any(before[key] for key in ('dirty_ap', 'dirty_switch', 'dirty_vbus', 'dirty_current'))
            or property_values() != saved):
        raise RuntimeError('Terminal firmware saved-value restore must precede device-role restore')
    def is_device_sink(typec):
        ports = typec.get('ports', [])
        return (len(ports) == 1 and ports[0].get('partner_present') is True
            and '[device]' in ports[0].get('data_role', '').split()
            and '[sink]' in ports[0].get('power_role', '').split())
    return_deadline = time.monotonic() + 170
    while not is_device_sink(usb.typec_inventory()):
        if time.monotonic() >= return_deadline or lease_status() != before:
            raise RuntimeError('Actual device/sink cable return not observed within bound')
        time.sleep(.5)
    mode = usb.mode_paths()
    usb.write(mode, 'device')
    deadline = time.monotonic() + 10
    restored = False
    while time.monotonic() < deadline:
        if usb.read(mode) == 'device' and usb.inventory().get('udc'):
            restored = True
            break
        time.sleep(.25)
    after, values, typec = lease_status(), property_values(), usb.typec_inventory()
    ports = typec.get('ports', [])
    attached_device = (len(ports) == 1 and ports[0].get('partner_present') is True
        and '[device]' in ports[0].get('data_role', '').split()
        and '[sink]' in ports[0].get('power_role', '').split())
    clean = restored and attached_device and after == before and values == saved
    return dict(stage='role', role_restored=restored, cleanup_complete=clean,
                status=after, values=values, typec=typec)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == 'read-block-child':
        if os.geteuid() or not Path('/run/oneplus13/ram-session').is_file() or not NEEDED <= set(Path('/proc/cmdline').read_text().split()):
            raise RuntimeError('Protected RAM session required')
        native, usb = load_native(), load_usb()
        if lease_status()['state'] != 1:
            raise RuntimeError('Supply is not active')
        native.host_guard(usb, usb.mode_paths())
        print(json.dumps(native.read_block(usb, json.loads(sys.argv[2], object_pairs_hook=native.unique_object)), sort_keys=True))
        return
    stage = sys.argv[1] if len(sys.argv) == 2 else ''
    try:
        if stage == 'arm':
            result = arm_receipt()
        elif stage == 'host':
            result = host_receipt()
        elif stage == 'restore':
            result = restore_receipt()
        elif stage == 'role':
            result = role_receipt()
        else:
            raise ValueError('Expected arm, host or restore')
    except Exception as exc:
        result = {'schema': 'r222-usb-vbus-probe-v1', 'stage': stage,
                  'error_class': type(exc).__name__,
                  'error_detail': str(exc)[:160],
                  'cleanup_complete': False}
    print('RAM_USB_VBUS_RESULT ' + json.dumps(result, sort_keys=True), flush=True)
    raise SystemExit(0 if result.get('cleanup_complete', True) is not False else 1)


if __name__ == '__main__':
    main()
