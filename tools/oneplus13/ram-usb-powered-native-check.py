#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Use the existing one-shot firmware lease for an already attached native host.

Never write a DWC3/Type-C role or mount a filesystem. The kernel owns the fixed
AP17/SW18 transaction and its unchanged 120-second best-effort watchdog.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

STATE = Path('/run/oneplus13/usb-powered-native')
ACTIVE = dict(state=1, used=1, dirty_ap=1, dirty_switch=1)
RESTORED = dict(state=2, used=1, dirty_ap=0, dirty_switch=0)
REFUSED = dict(state=4, used=1, dirty_ap=0, dirty_switch=0)
ONE = dict(oneplus13_otg_ap_enable=1, oneplus13_otg_switch=1)
WORK_SECONDS = 80
READ_SECONDS = 8
TAIL_SECONDS = 8


def load_native():
    base = Path(__file__).resolve().parent
    path = base/'oneplus13-ram-usb-native-host-check.py'
    if not path.is_file():
        path = base/'ram-usb-native-host-check.py'
    spec = importlib.util.spec_from_file_location('powered_native_usb', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def exact(value, expected):
    return (isinstance(value, dict) and value == expected
            and all(type(v) is int for v in value.values()))


def active(usb, *, pair=False):
    status = usb.firmware_lease_status()
    if not exact(status, ACTIVE):
        raise RuntimeError('Powered native lease is not exact ACTIVE')
    if pair:
        facts = usb.firmware_otg_inventory()
        if facts.get('complete') is not True or not exact(facts.get('values'), ONE):
            raise RuntimeError('Powered native gates are not both enabled')
    return status


def restore(usb, active_seen):
    """Never retry UNKNOWN; only an ACTIVE status permits one restore command."""
    status = usb.firmware_lease_status()
    if exact(status, ACTIVE):
        active_seen = True
        usb.firmware_lease_command('restore')
        status = usb.firmware_lease_status()
    allowed = exact(status, RESTORED) or (not active_seen and exact(status, REFUSED))
    if not allowed:
        raise RuntimeError('Powered native terminal cleanup is not proven')
    final = usb.firmware_otg_inventory()
    zero = dict(oneplus13_otg_ap_enable=0, oneplus13_otg_switch=0)
    if final.get('complete') is not True or not exact(final.get('values'), zero):
        raise RuntimeError('Powered native final gate baseline differs')
    # A driver rebind or service transition between pair GET and status is not proof.
    if not exact(usb.firmware_lease_status(), status):
        raise RuntimeError('Powered native terminal status changed')
    return dict(status=status, values=final['values'], cleanup_complete=True)


def child_read(native, usb, mode, expected):
    active(usb)
    native.host_guard(usb, mode)
    proof = native.read_block(usb, expected)
    native.host_guard(usb, mode)
    active(usb)
    return proof


def probe(native, usb, mode, seconds, run=subprocess.run):
    result = dict(scope='ONEPLUS13_RAM_POWERED_NATIVE_USB_READ_ONLY',
                  role_write_requested=False, filesystem_mount_requested=False,
                  firmware_lease_attempted=False, firmware_active_verified=False,
                  cleanup_complete=False, functional_pass=False,
                  host_peripheral_receipt=False, host_peripheral_i_o_qualified=False)
    attempted = False
    active_seen = False
    try:
        result['firmware_before'] = native.unused(usb)
        native.host_guard(usb, mode)
        result['native_host_controller_ready'] = True
        result['kernel_before'] = usb.kernel_diagnostics()
        # Fresh admission immediately before the one permitted acquisition.
        native.unused(usb)
        native.host_guard(usb, mode)
        work_deadline = time.monotonic()+WORK_SECONDS
        attempted = True
        result['firmware_lease_attempted'] = True
        result['firmware_lease_start_uptime'] = round(time.monotonic(), 3)
        usb.firmware_lease_command('start')
        result['firmware_active_status'] = active(usb)
        active_seen = True
        result['firmware_active_verified'] = True
        # Once ACTIVE was observed, even a failing pair GET requires RESTORED.
        active(usb, pair=True)
        native.host_guard(usb, mode)
        # Reserve all eight reader seconds and a separate status/loop tail.
        remaining = work_deadline-time.monotonic()-READ_SECONDS-TAIL_SECONDS
        if remaining < 3:
            raise RuntimeError('Powered native observation budget exhausted')
        wait = min(seconds, remaining)
        result['wait_budget_seconds'] = round(wait, 3)
        result['wait_begin_uptime'] = round(time.monotonic(), 3)
        deadline = time.monotonic()+wait
        candidates = []
        while True:
            native.host_guard(usb, mode)
            active(usb)
            result['peripherals'] = native.devices(usb)
            candidates = native.block_candidates(usb)
            if candidates or time.monotonic() >= deadline:
                break
            time.sleep(.5)
        result['wait_end_uptime'] = round(time.monotonic(), 3)
        result['host_peripheral_receipt'] = bool(result['peripherals'])
        result['block_candidate_present'] = bool(candidates)
        if candidates and work_deadline-time.monotonic() >= READ_SECONDS+TAIL_SECONDS:
            command = [sys.executable, str(Path(__file__).resolve()),
                       '--read-block-child', json.dumps(candidates[0], sort_keys=True)]
            child = run(command, capture_output=True, text=True, timeout=READ_SECONDS, check=False)
            if child.returncode == 0 and len(child.stdout) <= 512:
                proof = json.loads(child.stdout, object_pairs_hook=native.unique_object)
                expected = dict(read_bytes=4096, descriptor_read_only=True,
                                block_identity_verified=True, cleanup_complete=True)
                if proof == expected and all(type(proof[k]) is type(v) for k,v in expected.items()):
                    result['storage_read_only'] = proof
                    result['host_peripheral_i_o_qualified'] = True
        elif candidates:
            result['read_skipped_work_budget'] = True
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired) as error:
        result['error_class'] = type(error).__name__
    finally:
        if attempted:
            try:
                result['firmware_restore_begin_uptime'] = round(time.monotonic(), 3)
                result['firmware_after'] = restore(usb, active_seen)
                result['firmware_restore_end_uptime'] = round(time.monotonic(), 3)
                result['cleanup_complete'] = True
            except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired):
                result['firmware_residual_unknown'] = True
        else:
            try:
                result['firmware_after'] = native.unused(usb)
                result['cleanup_complete'] = True
            except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired):
                result['firmware_residual_unknown'] = True
        result['kernel_after'] = usb.kernel_diagnostics()
        result['functional_pass'] = bool(result['cleanup_complete'] and active_seen
            and 'error_class' not in result and result['host_peripheral_receipt']
            and result['host_peripheral_i_o_qualified'])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wait-seconds', type=int, default=60)
    parser.add_argument('--read-block-child')
    args = parser.parse_args()
    native = load_native()
    flags = set(Path('/proc/cmdline').read_text().split())
    if (os.geteuid() != 0 or not native.RAM_MARKER.is_file()
            or not {'oneplus13.usb_host_probe=1','qcom_battmgr.oneplus13_otg_lease=1'} <= flags):
        raise RuntimeError('Protected opt-in RAM session required')
    if not 3 <= args.wait_seconds <= 60:
        parser.error('Powered native wait must be between 3 and 60 seconds')
    usb = native.load_usb()
    mode = usb.mode_paths(root=usb.DEBUGFS)
    if args.read_block_child:
        print(json.dumps(child_read(native, usb, mode, json.loads(
            args.read_block_child, object_pairs_hook=native.unique_object)), sort_keys=True))
        return
    STATE.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = probe(native, usb, mode, args.wait_seconds)
    (STATE/'result.json').write_text(json.dumps(result,sort_keys=True)+chr(10))
    print('RAM_USB_POWERED_NATIVE_RESULT '+json.dumps(result,sort_keys=True),flush=True)
    raise SystemExit(0 if result['functional_pass'] else 1)


if __name__ == '__main__':
    main()
