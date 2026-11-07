#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare, enter and inspect one RTC-woken deep suspend in a RAM session."""

import argparse
import array
try:
    import fcntl
except ImportError:  # Windows host runs the parser tests, never this ioctl path.
    fcntl = None
import json
import os
from pathlib import Path
import subprocess
import time


RAM_MARKER = Path("/run/oneplus13/ram-session")
STATE = Path("/run/oneplus13/suspend")
RTC = Path("/sys/class/rtc/rtc0")
POWER_STATE = Path("/sys/power/state")
MEM_SLEEP = Path("/sys/power/mem_sleep")
WDIOC_SETTIMEOUT = 0xC0045706
WDIOC_GETTIMEOUT = 0x80045707
WDIOC_KEEPALIVE = 0x80045705


def read(path):
    return path.read_text(encoding="utf-8").strip()


def write(path, value):
    path.write_text(value + "\n", encoding="utf-8")


def ioctl_int(fd, request, value=0):
    data = array.array("i", [value])
    fcntl.ioctl(fd, request, data, True)
    return data[0]


def write_result(result):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = STATE / "result.tmp"
    temporary.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(STATE / "result.json")
    print("RAM_SUSPEND_RESULT " + json.dumps(result, sort_keys=True), flush=True)


def protected():
    if not RAM_MARKER.is_file():
        raise RuntimeError("Protected phone RAM session required")
    for unit in ("oneplus13-ram-guard.service", "oneplus13-ram-deadline.timer"):
        subprocess.run(["systemctl", "is-active", "--quiet", unit], check=True, timeout=4)


def prepare(seconds, sleep_mode="deep"):
    protected()
    if not 5 <= seconds <= 20:
        raise ValueError("Suspend duration must stay between 5 and 20 seconds, below the 32-second recovery watchdog")
    states = read(POWER_STATE).split()
    if "mem" not in states:
        raise ValueError("System memory suspend is unavailable")
    modes = read(MEM_SLEEP)
    mode_names = [value.strip("[]") for value in modes.split()]
    if sleep_mode not in mode_names:
        raise ValueError(f"{sleep_mode} memory sleep is unavailable")
    if f"[{sleep_mode}]" not in modes:
        write(MEM_SLEEP, sleep_mode)
        modes = read(MEM_SLEEP)
        if f"[{sleep_mode}]" not in modes:
            raise ValueError(f"Could not select {sleep_mode} memory sleep")
    wakealarm = RTC / "wakealarm"
    if not wakealarm.is_file():
        raise ValueError("RTC wakealarm is unavailable")
    wakeup = RTC / "device/power/wakeup"
    if wakeup.is_file():
        write(wakeup, "enabled")
    now = int(read(RTC / "since_epoch"))
    alarm = now + seconds
    write(wakealarm, "0")
    write(wakealarm, str(alarm))
    readback = int(read(wakealarm))
    if abs(readback - alarm) > 1:
        raise ValueError("RTC alarm readback mismatch")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = {
        "scope": "ONEPLUS13_RAM_RTC_DEEP_SUSPEND",
        "mode": "prepare",
        "pass": True,
        "power_states": states,
        "mem_sleep": modes,
        "sleep_mode": sleep_mode,
        "rtc_name": read(RTC / "name"),
        "rtc_epoch": now,
        "wakealarm_epoch": readback,
        "requested_seconds": seconds,
        "wakeup_path": str(wakeup),
        "wakeup_state": read(wakeup) if wakeup.is_file() else None,
        "suspend_entered": False,
        "resume_verified": False,
    }
    write_result(result)


def enter():
    protected()
    result = json.loads(read(STATE / "result.json"))
    if result.get("mode") != "prepare" or result.get("pass") is not True:
        raise ValueError("Suspend must be prepared first")
    alarm = int(read(RTC / "wakealarm"))
    if alarm <= int(read(RTC / "since_epoch")) + 2:
        raise ValueError("RTC wake deadline is too close or already expired")
    result.update(
        mode="enter",
        suspend_entered=True,
        enter_wall=time.time(),
        enter_monotonic=time.monotonic(),
        enter_boottime=time.clock_gettime(time.CLOCK_BOOTTIME),
        enter_uptime=read(Path("/proc/uptime")),
    )
    write_result(result)
    candidates = [
        path
        for path in Path("/sys/class/watchdog").glob("watchdog[0-9]*")
        if (path / "identity").read_text().strip() == "Gunyah Watchdog"
    ]
    if len(candidates) != 1:
        raise ValueError("Exactly one Gunyah safety watchdog is required")
    watchdog = candidates[0]
    device = Path("/dev") / watchdog.name
    fd = None
    try:
        fd = os.open(device, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        result["watchdog_timeout_readback"] = ioctl_int(fd, WDIOC_SETTIMEOUT, 32)
        result["watchdog_timeout_reported"] = ioctl_int(fd, WDIOC_GETTIMEOUT)
        if result["watchdog_timeout_readback"] != 32 or result["watchdog_timeout_reported"] != 32:
            raise ValueError("Safety watchdog timeout readback mismatch")
        ioctl_int(fd, WDIOC_KEEPALIVE)
        result["watchdog_armed_before_suspend"] = True
        write_result(result)
        with POWER_STATE.open("w", encoding="ascii") as stream:
            stream.write("mem")
            stream.flush()
    finally:
        if fd is not None:
            try:
                os.write(fd, b"V")
            finally:
                os.close(fd)
    result.update(
        exit_wall=time.time(),
        exit_monotonic=time.monotonic(),
        exit_boottime=time.clock_gettime(time.CLOCK_BOOTTIME),
        exit_uptime=read(Path("/proc/uptime")),
        rtc_after=int(read(RTC / "since_epoch")),
    )
    result["monotonic_elapsed_seconds"] = round(
        result["exit_monotonic"] - result["enter_monotonic"], 3
    )
    result["boottime_elapsed_seconds"] = round(
        result["exit_boottime"] - result["enter_boottime"], 3
    )
    result["wall_elapsed_seconds"] = round(
        result["exit_wall"] - result["enter_wall"], 3
    )
    result["watchdog_disarmed_after_resume"] = True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result["watchdog_state_after_resume"] = read(watchdog / "state")
        if result["watchdog_state_after_resume"] == "inactive":
            break
        time.sleep(0.1)
    result["suspended_seconds"] = round(
        result["boottime_elapsed_seconds"] - result["monotonic_elapsed_seconds"], 3
    )
    result["resume_verified"] = result["suspended_seconds"] >= 5
    result["pass"] = bool(
        result["suspend_entered"]
        and result["resume_verified"]
        and result["watchdog_state_after_resume"] == "inactive"
    )
    write_result(result)
    raise SystemExit(0 if result["pass"] else 1)


def cleanup():
    protected()
    result = json.loads(read(STATE / "result.json"))
    wakealarm = RTC / "wakealarm"
    if wakealarm.is_file():
        write(wakealarm, "0")
    result["wakealarm_cleared"] = (
        read(wakealarm) == "" or int(read(wakealarm)) == 0
        if wakealarm.is_file()
        else True
    )
    result["cleanup_complete"] = bool(result["wakealarm_cleared"])
    write_result(result)
    raise SystemExit(0 if result["cleanup_complete"] else 1)


def inspect():
    result = json.loads(read(STATE / "result.json"))
    print("RAM_SUSPEND_RESULT " + json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result.get("resume_verified") else 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("prepare", "enter", "inspect", "cleanup"), required=True
    )
    parser.add_argument(
        "--sleep-mode", choices=("s2idle", "deep"), default="deep"
    )
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args()
    {"prepare": lambda: prepare(args.seconds, args.sleep_mode), "enter": enter,
     "inspect": inspect, "cleanup": cleanup}[args.mode]()


if __name__ == "__main__":
    main()
