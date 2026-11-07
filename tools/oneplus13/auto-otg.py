#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Desktop OTG policy: Type-C observations plus the kernel's bounded owner API."""
import argparse
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import time

ENDPOINT = Path('/sys/bus/auxiliary/devices/pmic_glink.power-supply.0/oneplus13_otg_auto')
RUNTIME = Path('/run/oneplus13-auto-otg')
FLAG = 'qcom_battmgr.oneplus13_otg_auto=1'
FIELDS = ('state generation source healthy saved_ap saved_switch saved_vbus '
          'saved_current dirty_ap dirty_switch dirty_vbus dirty_current').split()


def parse_status(raw):
    pattern = ' '.join(key + r'=([0-9]{1,10})' for key in FIELDS) + r'\n?'
    match = re.fullmatch(pattern, raw)
    if not match:
        raise RuntimeError('Automatic OTG kernel receipt is malformed')
    result = dict(zip(FIELDS, map(int, match.groups())))
    if result['state'] not in range(6) or any(result[k] not in (0, 1)
            for k in ('source', 'healthy', 'dirty_ap', 'dirty_switch', 'dirty_vbus', 'dirty_current')):
        raise RuntimeError('Automatic OTG kernel state is invalid')
    return result


def clean_status(status):
    return status['state'] in (0, 2) and not any(
        status[k] for k in ('dirty_ap', 'dirty_switch', 'dirty_vbus', 'dirty_current'))


def oneplus13_machine(root=Path('/sys/firmware/devicetree/base')):
    """Match the kernel gate, including the exact identity rewritten by ABL."""
    try:
        compatible = root.joinpath('compatible').read_bytes().decode().split('\0')
        model = root.joinpath('model').read_bytes().decode().rstrip('\0')
        chassis = root.joinpath('chassis-type').read_bytes().decode().rstrip('\0')
    except (OSError, UnicodeError):
        return False
    if chassis != 'handset':
        return False
    if model == 'OnePlus 13 (AA569 native display experimental candidate)':
        return {'oneplus,dodge', 'qcom,sm8750'} <= set(compatible)
    return model == 'Qualcomm Technologies, Inc. Sun MTP,dodge' and 'qcom,sun' in compatible


class Backend:
    def __init__(self):
        if os.geteuid() or not oneplus13_machine():
            raise RuntimeError('Exact OnePlus 13 root service required')
        flags = set(Path('/proc/cmdline').read_text().split())
        if FLAG not in flags or flags & {
                'qcom_battmgr.oneplus13_otg_lease=1',
                'qcom_battmgr.oneplus13_otg_supply_lease=1'}:
            raise RuntimeError('Automatic and manual firmware owners are exclusive')
        spec = importlib.util.spec_from_file_location('op13_usb_inventory',
            '/usr/libexec/oneplus13-ram-usb-role-check.py')
        self.usb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.usb)

    def status(self):
        return parse_status(ENDPOINT.read_text())

    def command(self, action):
        if action not in {'arm', 'keepalive', 'off', 'close'} and not re.fullmatch(
                r'on [1-9][0-9]{0,9}', action):
            raise ValueError('Unknown automatic OTG command')
        ENDPOINT.write_text(action + '\n')

    def observe(self):
        return dict(host_source=self.usb.typec_host_source(self.usb.typec_inventory()),
            host_controller_ready=self.usb.host_controller_ready(dict(
                host_controllers=self.usb.host_controllers(Path('/sys')))))


class Controller:
    def __init__(self, backend, clock=time.monotonic):
        self.backend, self.clock = backend, clock
        self.last_heartbeat = float('-inf')
        self.on_count = self.off_count = 0
        self.last = None
        self.last_off_observation = None

    def step(self):
        state = self.backend.status()
        if not state['healthy'] or state['state'] in (3, 4):
            raise RuntimeError('Automatic OTG transport/state is unavailable; stop')
        if state['state'] in (0, 2):
            if not clean_status(state):
                raise RuntimeError('Unrestored values forbid a new ownership cycle')
            self.backend.command('arm')
            self.last_heartbeat = self.clock()
            state = self.backend.status()
        if self.clock() - self.last_heartbeat >= 3:
            self.backend.command('keepalive')
            self.last_heartbeat = self.clock()
        observed = self.backend.observe()
        attached = observed['host_source'] and observed['host_controller_ready']
        # Controller/root-hub enumeration is an activation prerequisite, not a
        # physical detach signal. Rebuilding xHCI must not cut a valid source's
        # VBUS and thereby prevent that same controller from enumerating again.
        # Actual Type-C or firmware source loss still turns power off at once.
        if state['state'] == 1 and (not observed['host_source'] or not state['source']):
            self.last_off_observation = dict(monotonic=self.clock(),
                firmware_source=state['source'],physical=dict(observed))
            self.backend.command('off')
            self.off_count += 1
        elif state['state'] == 5 and attached and state['source']:
            # A second physical-role sample narrows the userspace observation gap;
            # the kernel also rejects a changed firmware notification generation.
            fresh = self.backend.observe()
            if fresh['host_source'] and fresh['host_controller_ready']:
                try:
                    self.backend.command('on ' + str(state['generation']))
                except OSError as error:
                    if error.errno != errno.EINVAL:
                        raise
                    current = self.backend.status()
                    if not current['healthy'] or current['state'] not in (2, 5):
                        raise
                else:
                    self.on_count += 1
        final = self.backend.status()
        self.last = dict(kernel=final, physical=observed, on_count=self.on_count,
                         off_count=self.off_count, cleanup_proven=clean_status(final),
                         last_off_observation=self.last_off_observation)
        return self.last

    def close(self):
        self.backend.command('close')
        status = self.backend.status()
        if not clean_status(status):
            raise RuntimeError('Automatic OTG cleanup remains unproven')
        return dict(kernel=status, cleanup_proven=True, on_count=self.on_count,
                    off_count=self.off_count,last_off_observation=self.last_off_observation)


def save(value):
    temporary = RUNTIME/'state.tmp'
    temporary.write_text(json.dumps(value, sort_keys=True)+'\n')
    temporary.chmod(0o600)
    temporary.replace(RUNTIME/'state.json')
    if value.get('last_off_observation') is not None:
        receipt=RUNTIME/'last-off-observation.json'
        receipt.write_text(json.dumps(value['last_off_observation'],sort_keys=True)+'\n')
        receipt.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--close', action='store_true')
    args = parser.parse_args()
    backend = Backend()
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(RUNTIME/'owner.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        controller = Controller(backend)
        if args.close:
            try:
                save(controller.close())
            finally:
                (RUNTIME/'desktop-mode').unlink(missing_ok=True)
            return
        stopping = False
        def stop(signum, frame):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        # Waiting for the ADSP/auxiliary device performs no firmware mutation.
        end = time.monotonic() + 180
        while not stopping:
            try:
                ready = backend.status()['healthy'] == 1
            except FileNotFoundError:
                ready = False
            if ready:
                break
            if time.monotonic() >= end:
                raise RuntimeError('Automatic OTG kernel service did not become ready')
            time.sleep(.25)
        if stopping:
            return
        (RUNTIME/'desktop-mode').write_text('automatic-otg\n')
        (RUNTIME/'desktop-mode').chmod(0o600)
        previous = None
        try:
            while not stopping:
                state = controller.step()
                if state != previous:
                    save(state)
                    previous = state
                time.sleep(.25)
        finally:
            try:
                save(controller.close())
            finally:
                (RUNTIME/'desktop-mode').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
