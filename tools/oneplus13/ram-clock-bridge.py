"""Explicit RAM-session CLOCK_REALTIME bridge; no RTC or filesystem writes."""
import argparse
import json
from pathlib import Path
import time


def bridge(host_epoch, mounts, clock=time):
    if isinstance(host_epoch, bool) or not isinstance(host_epoch, int) or not 1767225600 <= host_epoch <= 1924992000:
        raise ValueError('Host epoch outside reviewed range')
    if ' / overlay ' not in mounts:
        raise ValueError('Refusing outside RAM overlay')
    before, mono = clock.time(), clock.monotonic()
    changed = abs(before-host_epoch) > 5
    if changed:
        clock.clock_settime(clock.CLOCK_REALTIME, host_epoch)
    after = clock.time()
    elapsed = clock.monotonic()-mono
    if not 0 <= elapsed <= 5 or abs(after-host_epoch) > 10:
        raise ValueError('Clock bridge did not meet bounded check')
    return {'before_epoch': round(before, 3), 'after_epoch': round(after, 3),
            'host_epoch': host_epoch, 'changed': changed,
            'monotonic_elapsed_seconds': round(elapsed, 6), 'rtc_write_requested': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-epoch', type=int, required=True)
    args = parser.parse_args()
    print('RAM_CLOCK_RESULT '+json.dumps(bridge(args.host_epoch, Path('/proc/mounts').read_text()),sort_keys=True))


if __name__ == '__main__':
    main()
