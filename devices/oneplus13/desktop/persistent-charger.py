#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
"""Keep charger boots dark until unplugged or the power key is held."""
import os
from pathlib import Path
import runpy
import select
import struct
import subprocess
import time


EVENT = struct.Struct('llHHi')
KEY_POWER = 116
EVIOCGRAB = 0x40044590
EVIOCGKEY = 0x80604518  # 96 bytes, covering Linux KEY_MAX.
SUPPLY = Path('/sys/class/power_supply/qcom-battmgr-usb/online')


class ChargerState:
    def __init__(self, now):
        self.ready_at = now + 15
        self.offline_since = None
        self.pressed_since = None

    def key(self, down, now):
        if not down:
            self.pressed_since = None
        elif self.pressed_since is None:
            self.pressed_since = now

    def action(self, online, now):
        if self.pressed_since is not None and now - self.pressed_since >= 2:
            return 'reboot'
        if now < self.ready_at or online != 0:
            self.offline_since = None
        elif self.offline_since is None:
            self.offline_since = now
        elif now - self.offline_since >= 3:
            return 'poweroff'
        return None


def key_down(fd):
    import fcntl

    keys = bytearray(96)
    fcntl.ioctl(fd, EVIOCGKEY, keys, True)
    return bool(keys[KEY_POWER // 8] & (1 << (KEY_POWER % 8)))


def main():
    import fcntl

    args = Path('/proc/cmdline').read_text().split()
    if 'oneplus13.persistent=1' not in args or 'androidboot.mode=charger' not in args:
        raise RuntimeError('Persistent charger boot required')
    power_keys = [
        Path('/dev/input') / path.parent.parent.name
        for path in Path('/sys/class/input').glob('event*/device/name')
        if path.read_text().strip() == 'pmic_pwrkey'
    ]
    if len(power_keys) != 1:
        raise RuntimeError('Expected exactly one PMIC power key')
    display = runpy.run_path('/usr/libexec/oneplus13-display-power.py')
    light = display['Backlight'](display['find_backlight']())
    light.set_mode(3)
    state = ChargerState(time.monotonic())
    print('ONEPLUS13_OFF_MODE_CHARGING_READY', flush=True)
    next_report = 0
    with power_keys[0].open('rb', buffering=0) as key:
        fcntl.ioctl(key.fileno(), EVIOCGRAB, 1)
        state.key(key_down(key.fileno()), time.monotonic())
        dropped = False
        while True:
            if select.select([key], [], [], 0.25)[0]:
                data = os.read(key.fileno(), EVENT.size * 64)
                if not data or len(data) % EVENT.size:
                    raise RuntimeError('Power key event stream ended')
                for _, _, kind, code, value in EVENT.iter_unpack(data):
                    if kind == 0 and code == 3:  # SYN_DROPPED
                        dropped = True
                        state.key(False, time.monotonic())
                    elif dropped:
                        if kind == 0 and code == 0:  # SYN_REPORT
                            state.key(key_down(key.fileno()), time.monotonic())
                            dropped = False
                    elif kind == 1 and code == KEY_POWER and value in (0, 1):
                        state.key(value == 1, time.monotonic())
            try:
                online = int(SUPPLY.read_text().strip())
            except (OSError, ValueError):
                online = None
            now = time.monotonic()
            if now >= next_report:
                battery = SUPPLY.parent.parent / 'qcom-battmgr-bat'
                values = []
                for name in ('status', 'capacity', 'current_now'):
                    try:
                        values.append(f'{name}={(battery / name).read_text().strip()}')
                    except OSError:
                        pass
                print(f'charger online={online} ' + ' '.join(values), flush=True)
                next_report = now + 15
            action = state.action(online, now)
            if action:
                print(f'charger requested {action}', flush=True)
                subprocess.run(['systemctl', '--no-block', action], check=True, timeout=10)
                return


if __name__ == '__main__':
    main()
