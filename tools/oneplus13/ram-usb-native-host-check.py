#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Observe an attached native Type-C host and read one USB block via O_RDONLY.

No role/firmware setter, filesystem mount or block-policy change is used.
Only the byte count is recorded in private RAM evidence; no media bytes are logged.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

RAM_MARKER = Path('/run/oneplus13/ram-session')
STATE = Path('/run/oneplus13/usb-native-host')
SYS = Path('/sys')
IDLE = dict(state=0, used=0, dirty_ap=0, dirty_switch=0)
ZERO = dict(oneplus13_otg_ap_enable=0, oneplus13_otg_switch=0)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate native USB proof field')
        result[key] = value
    return result


def load_usb():
    base = Path(__file__).resolve().parent
    path = base/'oneplus13-ram-usb-role-check.py'
    if not path.is_file():
        path = base/'ram-usb-role-check.py'
    spec = importlib.util.spec_from_file_location('native_usb_inventory', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unused(usb):
    status, baseline = usb.firmware_lease_status(), usb.firmware_otg_inventory()
    if (status != IDLE or any(type(v) is not int for v in status.values())
            or baseline.get('complete') is not True or baseline.get('values') != ZERO
            or any(type(v) is not int for v in baseline.get('values', {}).values())):
        raise RuntimeError('Native USB firmware baseline is not exact unused zero')
    return dict(status=status, values=baseline['values'])


def roots(usb, root=SYS):
    result = []
    for row in usb.host_controllers(root):
        for hub in row['root_hubs']:
            path = root/'bus/usb/devices'/hub
            resolved = path.resolve(strict=True)
            if (resolved.parent.name == row['device']
                    and resolved.parent.parent.name == 'a600000.usb'):
                result.append(resolved)
    return result


def beneath(path, parents):
    resolved = path.resolve(strict=True)
    return any(parent in resolved.parents for parent in parents)


def devices(usb, root=SYS):
    parents = roots(usb, root)
    return [row for row in usb.usb_peripherals(root)
            if beneath(root/'bus/usb/devices'/row['path'], parents)]


def block_candidates(usb, root=SYS):
    parents = roots(usb, root)
    rows = []
    for path in sorted((root/'class/block').glob('sd*')):
        if not re.fullmatch(r'sd[a-z][0-9]+', path.name):
            continue
        if not (path/'partition').is_file() or not beneath(path, parents):
            continue
        resolved = path.resolve(strict=True)
        ancestors = resolved.parents
        usb_ancestors = [p for p in ancestors if usb.USB_DEVICE.fullmatch(p.name)
                         and (p/'subsystem').resolve().name == 'usb']
        if not usb_ancestors:
            continue
        # The existing RAM guard sets all physical block nodes read-only.
        # Observe its result without changing block policy ourselves.
        if not (path/'ro').is_file() or (path/'ro').read_text().strip() != '1':
            continue
        number = (path/'dev').read_text().strip()
        size = (path/'size').read_text().strip()
        if not re.fullmatch(r'[0-9]+:[0-9]+', number) or not size.isdecimal() or int(size) < 8:
            continue
        rows.append(dict(name=path.name, device='/dev/'+path.name,
                         major_minor=number, size_sectors=int(size),
                         sysfs_path=str(resolved), usb_ancestor=str(usb_ancestors[0]),
                         root_hub=str(next(p for p in parents if p in ancestors)),
                         sysfs_inode=resolved.stat().st_ino, block_read_only=True))
    return rows[:1]


def read_block(usb, expected, root=SYS):
    """Child-only bounded reader; identity is checked before and after open/read."""
    if expected not in block_candidates(usb, root):
        raise RuntimeError('USB block ancestry changed')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open(expected['device'], flags)
    try:
        info = os.fstat(fd)
        actual = str(os.major(info.st_rdev))+':'+str(os.minor(info.st_rdev))
        if not stat.S_ISBLK(info.st_mode) or actual != expected['major_minor']:
            raise RuntimeError('USB block descriptor identity differs')
        if expected not in block_candidates(usb, root):
            raise RuntimeError('USB block disappeared after open')
        data = os.pread(fd, 4096, 0)
        if len(data) != 4096 or expected not in block_candidates(usb, root):
            raise RuntimeError('USB block read incomplete or detached')
        return dict(read_bytes=len(data), descriptor_read_only=True,
                    block_identity_verified=True, cleanup_complete=True)
    finally:
        os.close(fd)


def host_guard(usb, mode):
    if usb.read(mode) != 'host' or not usb.typec_host_source(usb.typec_inventory()):
        raise RuntimeError('Native host/source attachment absent')
    if not roots(usb):
        raise RuntimeError('Exact DWC3 xHCI/root hub absent')


def probe(usb, mode, seconds, run=subprocess.run):
    result = dict(scope='ONEPLUS13_RAM_NATIVE_USB_READ_ONLY',
                  firmware_or_role_write_requested=False, cleanup_complete=False,
                  functional_pass=False, host_peripheral_receipt=False,
                  host_peripheral_i_o_qualified=False)
    try:
        result['firmware_before'] = unused(usb)
        host_guard(usb, mode)
        result['native_host_controller_ready'] = True
        result['wait_begin_uptime'] = round(time.monotonic(), 3)
        deadline = time.monotonic()+seconds
        while True:
            host_guard(usb, mode)
            result['peripherals'] = devices(usb)
            candidates = block_candidates(usb)
            if candidates or time.monotonic() >= deadline:
                break
            time.sleep(.5)
        result['wait_end_uptime'] = round(time.monotonic(), 3)
        result['host_peripheral_receipt'] = bool(result['peripherals'])
        result['block_candidate_present'] = bool(candidates)
        if candidates:
            command = [sys.executable, str(Path(__file__).resolve()),
                       '--read-block-child', json.dumps(candidates[0], sort_keys=True)]
            readback = run(command, capture_output=True, text=True, timeout=8, check=False)
            if readback.returncode == 0 and len(readback.stdout) <= 512:
                proof = json.loads(readback.stdout, object_pairs_hook=unique_object)
                required = dict(read_bytes=4096, descriptor_read_only=True,
                                block_identity_verified=True, cleanup_complete=True)
                if proof == required and all(type(proof[k]) is type(v) for k,v in required.items()):
                    result['storage_read_only'] = proof
                    result['host_peripheral_i_o_qualified'] = True
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        result['error_class'] = type(error).__name__
    finally:
        try:
            result['firmware_after'] = unused(usb)
            result['cleanup_complete'] = True
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            result['firmware_state_unverified'] = True
        result['functional_pass'] = bool(result['cleanup_complete']
            and 'error_class' not in result and result['host_peripheral_receipt']
            and result['host_peripheral_i_o_qualified'])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wait-seconds', type=int, default=60)
    parser.add_argument('--read-block-child')
    args = parser.parse_args()
    flags = set(Path('/proc/cmdline').read_text().split())
    if (os.geteuid() != 0 or not RAM_MARKER.is_file()
            or not {'oneplus13.usb_host_probe=1', 'qcom_battmgr.oneplus13_otg_lease=1'} <= flags):
        raise RuntimeError('Protected opt-in RAM session required')
    if not 3 <= args.wait_seconds <= 60:
        parser.error('Native observation must be between 3 and 60 seconds')
    usb = load_usb()
    mode = usb.mode_paths(root=usb.DEBUGFS)
    if args.read_block_child:
        unused(usb)
        host_guard(usb, mode)
        print(json.dumps(read_block(usb, json.loads(args.read_block_child,
              object_pairs_hook=unique_object)), sort_keys=True))
        return
    STATE.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = probe(usb, mode, args.wait_seconds)
    (STATE/'result.json').write_text(json.dumps(result, sort_keys=True)+'\n')
    print('RAM_USB_NATIVE_RESULT '+json.dumps(result, sort_keys=True), flush=True)
    raise SystemExit(0 if result['functional_pass'] else 1)


if __name__ == '__main__':
    main()
