#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Collect one bounded, read-only peripheral inventory from the RAM system.

The report distinguishes an available Linux resource from a feature that has
passed physical acceptance. It never opens audio or camera streams.
"""

import json
from pathlib import Path
import re
import stat


MAX_ENTRIES = 256
MAX_TEXT = 4096


def text(path, limit=MAX_TEXT):
    try:
        value = Path(path).read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    return value[:limit].strip()


def entries(root, limit=MAX_ENTRIES):
    try:
        values = sorted(Path(root).iterdir(), key=lambda item: item.name)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return []
    return [item.name for item in values[:limit]]


def links(root, suffix="", limit=MAX_ENTRIES):
    rows = []
    try:
        values = sorted(Path(root).iterdir(), key=lambda item: item.name)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return rows
    for item in values:
        if suffix and not item.name.endswith(suffix):
            continue
        row = {"name": item.name}
        try:
            row["target"] = str(item.resolve(strict=False))
        except OSError:
            row["target"] = None
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def class_inventory(name):
    root = Path("/sys/class") / name
    rows = []
    for item_name in entries(root):
        item = root / item_name
        row = {"name": item_name}
        for field in (
            "state",
            "firmware",
            "recovery",
            "name",
            "type",
            "role",
            "data_role",
            "power_role",
            "online",
            "current_speed",
            "maximum_speed",
        ):
            value = text(item / field, 256)
            if value is not None:
                row[field] = value
        rows.append(row)
    return rows


def input_inventory():
    rows = []
    for event in sorted(Path("/sys/class/input").glob("event*/device")):
        name = text(event / "name", 256)
        if name:
            rows.append({"event": event.parent.name, "name": name})
    return rows[:MAX_ENTRIES]


def power_supply_inventory():
    rows = []
    root = Path("/sys/class/power_supply")
    for item_name in entries(root):
        item = root / item_name
        row = {"name": item_name}
        for field in ("type", "status", "online", "capacity", "temp", "voltage_now"):
            value = text(item / field, 256)
            if value is not None:
                row[field] = value
        rows.append(row)
    return rows


def thermal_inventory():
    rows = []
    root = Path("/sys/class/thermal")
    for item_name in entries(root):
        if not item_name.startswith("thermal_zone"):
            continue
        item = root / item_name
        rows.append(
            {
                "name": item_name,
                "type": text(item / "type", 256),
                "temperature_millic": text(item / "temp", 256),
            }
        )
    return rows


def nfc_inventory():
    i2c_rows = []
    for item_name in entries("/sys/bus/i2c/devices"):
        item = Path("/sys/bus/i2c/devices") / item_name
        name = text(item / "name", 256)
        if name and ("nfc" in name.lower() or item_name.endswith("-0028")):
            row = {
                "name": item_name,
                "driver_name": name,
                "target": str(item.resolve(strict=False)),
            }
            driver = item / "driver"
            if driver.exists():
                row["driver"] = driver.name
            i2c_rows.append(row)
    device_path = Path("/dev/nq-nci")
    return {
        "i2c_devices": i2c_rows,
        "char_device_present": device_path.exists(),
        "char_device": (
            {
                "mode": oct(stat.S_IMODE(device_path.stat().st_mode)),
                "major": stat.S_MAJOR(device_path.stat().st_rdev),
                "minor": stat.S_MINOR(device_path.stat().st_rdev),
            }
            if device_path.exists()
            else None
        ),
        "modules": {
            name: (Path("/sys/module") / name).is_dir()
            for name in ("nxp_nci", "nxp_nci_i2c", "nfc")
        },
        "proc_nfc": text("/proc/net/nfc"),
    }


def device_tree_matches():
    wanted = re.compile(
        r"(?:qcom,[^\\0]*(?:mpss|modem)|nxp,nxp-nci|qcom,[^\\0]*csiphy|"
        r"qcom,[^\\0]*cci|qcom,[^\\0]*geniqup|"
        r"qcom,[^\\0]*(?:pcie|pmic-glink|qcom,smem|qcom,smd|qcom,glink))",
        re.IGNORECASE,
    )
    rows = []
    for path in Path("/proc/device-tree").rglob("compatible"):
        try:
            raw = path.read_bytes()[:MAX_TEXT]
        except (FileNotFoundError, PermissionError, OSError):
            continue
        values = [item.decode("utf-8", errors="replace") for item in raw.split(b"\0")]
        matches = [item for item in values if wanted.fullmatch(item)]
        if matches:
            rows.append({"node": str(path.parent), "compatible": matches})
        if len(rows) >= 128:
            break
    return rows


def main():
    result = {
        "scope": "ONEPLUS13_RAM_QUIET_PERIPHERAL_INVENTORY",
        "read_only": True,
        "audio_device_opened": False,
        "camera_stream_opened": False,
        "nfc": nfc_inventory(),
        "gnss": {
            "classes": class_inventory("gnss"),
            "device_nodes": [
                name
                for name in entries("/dev", 1024)
                if name.startswith("gnss")
            ],
        },
        "remoteproc": class_inventory("remoteproc"),
        "usb": {
            "roles": class_inventory("usb_role"),
            "typec": class_inventory("typec"),
            "extcon": class_inventory("extcon"),
            "udc": entries("/sys/class/udc"),
        },
        "infrared": {
            "rc": class_inventory("rc"),
            "lirc_devices": [
                name for name in entries("/dev", 1024) if name.startswith("lirc")
            ],
            "spidev": [
                name for name in entries("/dev", 1024) if name.startswith("spidev")
            ],
        },
        "power": {
            "state": text("/sys/power/state"),
            "mem_sleep": text("/sys/power/mem_sleep"),
            "wakeup_sources": entries("/sys/class/wakeup"),
            "suspend_stats": text("/sys/kernel/debug/suspend_stats"),
            "watchdogs": class_inventory("watchdog"),
            "rtc": class_inventory("rtc"),
        },
        "peripherals": {
            "input": input_inventory(),
            "power_supply": power_supply_inventory(),
            "thermal": thermal_inventory(),
            "leds": class_inventory("led"),
            "backlights": class_inventory("backlight"),
            "i2c_buses": entries("/sys/class/i2c-adapter"),
            "spi_buses": entries("/sys/class/spi_master"),
        },
        "device_tree_matches": device_tree_matches(),
    }
    nfc = result["nfc"]
    result["facts"] = {
        "nfc_i2c_target_present": bool(nfc["i2c_devices"]),
        "nfc_char_device_present": nfc["char_device_present"],
        "nfc_driver_loaded": nfc["modules"]["nxp_nci"]
        and nfc["modules"]["nxp_nci_i2c"],
        "gnss_class_present": bool(result["gnss"]["classes"]),
        "gnss_device_present": bool(result["gnss"]["device_nodes"]),
        "remoteproc_present": bool(result["remoteproc"]),
        "usb_role_interface_present": bool(result["usb"]["roles"]),
        "typec_interface_present": bool(result["usb"]["typec"]),
        "infrared_rc_present": bool(result["infrared"]["rc"]),
        "suspend_state_available": bool(result["power"]["state"]),
        "wakeup_sources_present": bool(result["power"]["wakeup_sources"]),
    }
    print("QUIET_PERIPHERAL_MATRIX_RESULT " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
