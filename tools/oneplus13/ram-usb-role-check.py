#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Switch the reviewed DWC3 controller to host briefly, then restore device."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import select
import subprocess
import sys
import tempfile
import time


RAM_MARKER = Path("/run/oneplus13/ram-session")
STATE = Path("/run/oneplus13/usb-role")
DEBUGFS = Path("/sys/kernel/debug")
USB_DEVICE = re.compile(r"\d+-\d+(?:\.\d+)*")
FIRMWARE_LEASE = Path('/sys/bus/auxiliary/devices/pmic_glink.power-supply.0/oneplus13_otg_lease')
# Reserve 40 of the fixed kernel 120 seconds for role/firmware restoration.
# These are process budgets, not hard real-time scheduler guarantees.
FIRMWARE_WORK_SECONDS = 80
FIRMWARE_WAIT_SECONDS_MAX = 60


def ensure_debugfs():
    if not (DEBUGFS / "tracing").exists():
        mounted = subprocess.run(
            ["mount", "-t", "debugfs", "debugfs", str(DEBUGFS)],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if mounted.returncode not in (0, 32):
            raise RuntimeError("Could not mount debugfs")


def mode_paths(root=None):
    if root is None:
        ensure_debugfs()
        root = DEBUGFS
    root = Path(root)
    rows = []
    candidates = [root / "usb/a600000.usb/mode", root / "a600000.usb/mode"]
    candidates += [path / "mode" for path in sorted(root.glob("dwc3*"))]
    candidates += [path / "mode" for path in sorted(root.glob("*/dwc3*"))]
    for mode in candidates:
        if mode.is_file():
            rows.append(mode)
    if len(rows) != 1:
        found = ",".join(str(path) for path in rows) or "none"
        raise RuntimeError(
            f"Expected one DWC3 debugfs mode file, found {len(rows)}: {found}"
        )
    return rows[0]


def read(path):
    return path.read_text(encoding="utf-8").strip()


def write(path, value):
    path.write_text(value + "\n", encoding="utf-8")


def usb_peripherals(root):
    """Describe attached devices without publishing serial numbers."""
    base = root / "bus/usb/devices"
    rows = []
    entries = sorted(base.iterdir()) if base.is_dir() else []
    for path in entries:
        if not USB_DEVICE.fullmatch(path.name):
            continue
        interfaces = sorted(
            item.name for item in base.glob(path.name + ":*")
        )
        drivers = []
        for item in [path] + [base / name for name in interfaces]:
            link = item / "driver"
            if link.exists():
                drivers.append(link.resolve(strict=False).name)
        def value(name):
            candidate = path / name
            if not candidate.is_file():
                return None
            text = candidate.read_text(encoding="utf-8", errors="replace").strip()
            return text[:80] or None
        rows.append({
            "path": path.name,
            "vendor_id": value("idVendor"),
            "product_id": value("idProduct"),
            "device_class": value("bDeviceClass"),
            "manufacturer": value("manufacturer"),
            "product": value("product"),
            "interfaces": interfaces[:16],
            "drivers": sorted(set(drivers)),
        })
    return rows[:32]


def host_controllers(root):
    """Associate bound xHCI and root hubs with this exact DWC3 controller."""
    root = Path(root)
    rows = []
    for link in sorted((root / "bus/platform/drivers/xhci-hcd").glob("xhci-hcd.*.auto"))[:8]:
        if not re.fullmatch(r"xhci-hcd[.][0-9]+[.]auto", link.name):
            continue
        device = link.resolve()
        driver = device / "driver"
        if device.parent.name != "a600000.usb" or not driver.is_symlink():
            continue
        if driver.resolve().name != "xhci-hcd":
            continue
        hubs = [hub.name for hub in sorted((root / "bus/usb/devices").glob("usb*"))
                if re.fullmatch(r"usb[0-9]+", hub.name)
                and hub.resolve().parent == device][:8]
        rows.append({"device": link.name, "controller": "a600000.usb",
                     "driver": "xhci-hcd", "root_hubs": hubs})
    return rows


def inventory(root=None):
    root = Path("/sys") if root is None else Path(root)
    peripherals = usb_peripherals(root)
    drivers = {
        driver
        for row in peripherals
        for driver in row["drivers"]
    }
    return {
        "usb_devices": sorted(
            path.name for path in (root / "bus/usb/devices").glob("*")
        )[:64],
        "xhci_platform_devices": sorted(
            path.name
            for path in (root / "bus/platform/drivers/xhci-hcd").glob("*")
        )[:16],
        "udc": sorted(path.name for path in (root / "class/udc").glob("*")),
        "usb_peripherals": peripherals,
        "usb_storage_bound": "usb-storage" in drivers,
        "usb_hid_bound": "usbhid" in drivers,
        "host_controllers": host_controllers(root),
    }


def wait_for_host_driver(seconds, sleep=time.sleep, reconcile=None):
    deadline = time.monotonic() + seconds
    facts = inventory()
    samples = 1
    while time.monotonic() < deadline:
        if reconcile is not None:
            reconcile()
        facts = inventory()
        samples += 1
        if facts["usb_storage_bound"] or facts["usb_hid_bound"]:
            return facts, samples
        sleep(0.5)
    return facts, samples


def host_controller_ready(facts):
    """GCTL host readback alone does not establish a running xHCI controller."""
    return any(row.get("controller") == "a600000.usb"
               and row.get("driver") == "xhci-hcd"
               and any(re.fullmatch(r"usb[0-9]+", name)
                       for name in row.get("root_hubs", []))
               for row in facts.get("host_controllers", []))


def kernel_diagnostics():
    """Keep bounded USB-only diagnostics in this private RAM test result."""
    try:
        payload = bounded_kernel_history()
        lines = [line[:512] for line in payload.decode(errors="replace").splitlines()
                 if re.search(r"dwc3|xhci|ucsi|typec|pmic.gl[i]*nk|usb.*(?:fail|error|timeout)",
                              line, re.I)][-64:]
        return {"available": True, "lines": lines, "private_only": True}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"available": False}


def bounded_kernel_history():
    """Never accumulate more than 4 MiB or leave a diagnostic child running."""
    process = subprocess.Popen(["dmesg"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 4
    chunks = []
    size = 0
    try:
        os.set_blocking(process.stdout.fileno(), False)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Kernel diagnostic deadline")
            if not select.select([process.stdout], [], [], remaining)[0]:
                raise ValueError("Kernel diagnostic deadline")
            chunk = os.read(process.stdout.fileno(), min(65536, 4 * 1024 * 1024 - size + 1))
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                raise ValueError("Kernel diagnostic exceeds bound")
            if not chunk:
                break
            chunks.append(chunk)
        if process.wait(timeout=max(.01, deadline - time.monotonic())):
            raise ValueError("Kernel history unavailable")
        return b"".join(chunks)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        process.stdout.close()


def typec_host_source(facts):
    """Only follow a real attached UFP; never force the Type-C power role."""
    ports = facts.get("ports", [])
    return bool(len(ports) == 1 and ports[0].get("partner_present") is True
                and "[host]" in ports[0].get("data_role", "").split()
                and "[source]" in ports[0].get("power_role", "").split())


def reconcile_host(mode, observations):
    """UCSI disconnect may replace an earlier debugfs host request with device."""
    facts = typec_inventory()
    current = read(mode)
    controller = inventory()
    ready = host_controller_ready(controller)
    key = (current, json.dumps(facts, sort_keys=True),
           json.dumps(controller, sort_keys=True))
    if not observations or observations[-1]["key"] != key:
        observations.append({"key": key, "mode": current, "typec": facts,
                             "host_controller_ready": ready,
                             "controller_inventory": controller,
                             "monotonic_seconds": round(time.monotonic(), 3)})
        del observations[:-24]
    if current != "host" and typec_host_source(facts):
        write(mode, "host")
        observations[-1]["host_reasserted_for_attached_ufp"] = True


def storage_partitions(root=Path("/sys/class/block")):
    """Return bounded block-partition names without assuming a device letter."""
    rows = []
    for path in sorted(Path(root).glob("sd*")):
        name = path.name
        if not re.fullmatch(r"sd[a-z][0-9]+", name):
            continue
        if not (path / "partition").is_file() or not (path / "dev").is_file():
            continue
        # Phone UFS devices also use sd* names. Accept only descendants of
        # an enumerated USB device, and require the block layer read-only bit.
        if not any(
            USB_DEVICE.fullmatch(parent.name)
            and (parent / "subsystem").resolve().name == "usb"
            for parent in path.resolve().parents
        ):
            continue
        if not (path / "ro").is_file() or read(path / "ro") != "1":
            continue
        try:
            size_sectors = int((path / "size").read_text().strip())
        except (OSError, ValueError):
            continue
        if not 1 <= size_sectors:
            continue
        rows.append({
            "device": "/dev/" + name,
            "size_sectors": size_sectors,
        })
    return rows[:8]


def typec_inventory(root=Path("/sys")):
    """Read fixed Type-C and ADSP fields without changing port policy."""
    root = Path(root)
    ports = []
    for path in sorted((root / "class/typec").glob("port*"))[:8]:
        if not re.fullmatch(r"port[0-9]+", path.name):
            continue
        row = {"port": path.name}
        for field in ("data_role", "power_role", "port_type", "power_operation_mode", "orientation"):
            try:
                value = read(path / field)
            except OSError:
                continue
            if len(value) <= 80 and re.fullmatch(r"[A-Za-z0-9 ._\[\]-]+", value):
                row[field] = value
        row["partner_present"] = (path.parent / (path.name + "-partner")).exists()
        ports.append(row)
    adsp = []
    for path in sorted((root / "class/remoteproc").glob("remoteproc*"))[:16]:
        try:
            name = read(path / "name")
            state = read(path / "state")
        except OSError:
            continue
        if "adsp" in name.lower() and state in {"offline", "running", "crashed", "suspended", "attached", "detached"}:
            adsp.append({"remoteproc": path.name, "state": state})
    return {"ports": ports, "adsp": adsp, "read_only": True}


def firmware_otg_inventory(root=Path("/sys"), run=subprocess.run):
    """Read only the optional phone-bound kernel interface, once per phase.

    The kernel serializes these requests with battery polling and poisons
    optional probes after timeout. Missing or failed reads are unknown.
    No raw property IDs, setter or automatic power policy is exposed here.
    """
    base = Path(root) / "bus/auxiliary/devices/pmic_glink.power-supply.0"
    facts = {"read_only": True, "values": {}, "unavailable": [], "status": {},
             "interface": {}}
    for field, path, probe in (("device_present", base, Path.is_dir),
                               ("driver_bound", base / "driver", Path.is_symlink)):
        try:
            facts["interface"][field] = probe(path)
        except OSError:
            facts["interface"][field] = None
    for name in ("oneplus13_otg_ap_enable", "oneplus13_otg_switch"):
        path = base / name
        try:
            regular = stat.S_ISREG(path.stat().st_mode)
        except FileNotFoundError:
            facts["status"][name] = "missing"
            facts["unavailable"].append(name)
            continue
        except OSError:
            facts["status"][name] = "stat_error"
            facts["unavailable"].append(name)
            continue
        if not regular:
            facts["status"][name] = "not_regular"
            facts["unavailable"].append(name)
            continue
        try:
            result = run(["cat", str(path)], capture_output=True, text=True,
                         timeout=3, check=False)
            value = result.stdout.strip()
            if result.returncode == 0 and value in {"0", "1"}:
                facts["values"][name] = int(value)
                facts["status"][name] = "ok"
            else:
                facts["status"][name] = ("read_error" if result.returncode
                                         else "invalid_value")
                facts["unavailable"].append(name)
        except subprocess.TimeoutExpired:
            facts["status"][name] = "timeout"
            facts["unavailable"].append(name)
        except OSError:
            facts["status"][name] = "read_error"
            facts["unavailable"].append(name)
    facts["complete"] = len(facts["values"]) == 2
    return facts


def firmware_lease_status(run=subprocess.run):
    """Fixed, bounded read; numeric kernel receipt, never raw firmware text."""
    result = run(['cat', str(FIRMWARE_LEASE)], capture_output=True, text=True,
                 timeout=3, check=False)
    match = re.fullmatch(r'state=([0-4]) used=([01]) dirty_ap=([01]) dirty_switch=([01])\n?',
                         result.stdout)
    if result.returncode or match is None:
        raise RuntimeError('USB firmware lease receipt unavailable')
    return dict(zip(('state', 'used', 'dirty_ap', 'dirty_switch'), map(int, match.groups())))


def firmware_lease_command(command, run=subprocess.run):
    if command not in {'start', 'restore'}:
        raise ValueError('Invalid fixed USB lease command')
    result = run([sys.executable, '-c',
                  'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]+chr(10))',
                  str(FIRMWARE_LEASE), command], capture_output=True, text=True,
                 timeout=20 if command == 'start' else 15, check=False)
    if result.returncode:
        raise RuntimeError('USB firmware lease command failed')


def firmware_lease_restore(run=subprocess.run):
    """A clean receipt requires final pair GET validation in the kernel.

    State UNKNOWN is terminal and forbids retry. Reboot/device role alone
    cannot prove a firmware rollback. Keep errors generic and private.
    """
    status = firmware_lease_status(run)
    if status['state'] == 1:
        firmware_lease_command('restore', run)
        status = firmware_lease_status(run)
    # An idle receipt after an attempted start may be a driver rebind.
    # Only this driver's used, terminal receipt proves our cleanup.
    clean = (status['state'] in {2, 4} and status['used'] == 1
             and not status['dirty_ap'] and not status['dirty_switch'])
    return {'status': status, 'cleanup_complete': clean}


def usb_hid_input_present(facts, root=Path("/sys")):
    """Require an input node below the actual enumerated USB device."""
    devices = Path(root) / "bus/usb/devices"
    for row in facts["usb_peripherals"]:
        if "usbhid" not in row["drivers"]:
            continue
        parent = (devices / row["path"]).resolve()
        for event in (Path(root) / "class/input").glob("event*"):
            if event.resolve().is_relative_to(parent):
                return True
    return False


def bounded_regular_files(root, maximum_entries=128):
    """Inspect a fixed number of entries without traversing symlinks."""
    candidates = []
    inspected = 0
    pending = [Path(root)]
    while pending and inspected < maximum_entries:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                inspected += 1
                if entry.is_file(follow_symlinks=False):
                    candidates.append(Path(entry.path))
                    if len(candidates) == 8:
                        return candidates
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                if inspected >= maximum_entries:
                    break
    return candidates


def read_regular_prefix(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("USB proof must be a regular file")
        return os.read(descriptor, 4096)
    finally:
        os.close(descriptor)


def storage_read_only_probe(
    run=subprocess.run,
    partitions=storage_partitions,
    temporary_root=Path("/run/oneplus13"),
):
    """Try one verified partition, with a separate deadline for filesystem I/O.

    A directory entry limit and O_NONBLOCK do not bound filesystem latency.
    Keep scanning/reading in a timed child; always unmount in the parent,
    including after mount timeout. Never recursively delete a mountpoint.
    """
    rows = partitions()
    attempts = []
    for row in rows[:1]:
        mountpoint = tempfile.mkdtemp(dir=temporary_root, prefix="usb-mount-")
        attempt = {"device": row["device"], "read_pass": False}
        mount_attempted = False
        try:
            mount_attempted = True
            mounted = run(
                ["mount", "-o", "ro,nosuid,nodev,noexec", row["device"], mountpoint],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            attempt["mount_returncode"] = mounted.returncode
            if mounted.returncode == 0:
                proof = run(
                    [sys.executable, "-c", (
                        "import runpy,sys,json,hashlib; "
                        "m=runpy.run_path(sys.argv[1]); "
                        "p=m['bounded_regular_files'](sys.argv[2]); "
                        "b=m['read_regular_prefix'](p[0]) if p else None; "
                        "print(json.dumps({'read_bytes':len(b),"
                        "'read_sha256':hashlib.sha256(b).hexdigest()}) "
                        "if b is not None else '{}')"
                    ), str(Path(__file__).resolve()), mountpoint],
                    capture_output=True, text=True, timeout=8, check=False,
                )
                if proof.returncode == 0 and len(proof.stdout) <= 256:
                    detail = json.loads(proof.stdout)
                    if (type(detail.get("read_bytes")) is int
                            and 0 <= detail["read_bytes"] <= 4096
                            and re.fullmatch(r"[0-9a-f]{64}", detail.get("read_sha256", ""))):
                        attempt.update(detail)
        except subprocess.TimeoutExpired:
            attempt["operation_timed_out"] = True
        except (OSError, ValueError, TypeError):
            attempt["operation_failed"] = True
        finally:
            if mount_attempted:
                try:
                    unmounted = run(
                        ["umount", mountpoint], capture_output=True,
                        text=True, timeout=12, check=False,
                    )
                    attempt["unmount_returncode"] = unmounted.returncode
                    attempt["cleanup_complete"] = unmounted.returncode == 0
                except (subprocess.TimeoutExpired, OSError):
                    attempt["cleanup_complete"] = False
            else:
                attempt["cleanup_complete"] = True
            if attempt["cleanup_complete"]:
                try:
                    os.rmdir(mountpoint)
                except OSError:
                    attempt["mountpoint_retained"] = True
        attempt["read_pass"] = bool(
            "read_sha256" in attempt and attempt["cleanup_complete"]
        )
        attempts.append(attempt)
    return {
        "partitions": rows,
        "candidate_limit": 1,
        "operation_timeout_seconds": 32,
        "attempts": attempts,
        "cleanup_complete": all(row["cleanup_complete"] for row in attempts),
        "read_only_io_pass": any(row["read_pass"] for row in attempts),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=int)
    parser.add_argument('--firmware-lease', action='store_true',
                        help='Explicit fixed AP17/SWITCH18 lease, experimental RAM boot only')
    args = parser.parse_args()
    if args.wait_seconds is None:
        args.wait_seconds = FIRMWARE_WAIT_SECONDS_MAX if args.firmware_lease else 75
    if not 3 <= args.wait_seconds <= 120:
        parser.error("--wait-seconds must be between 3 and 120")
    if not RAM_MARKER.is_file():
        raise RuntimeError("Protected phone RAM session required")
    if args.firmware_lease:
        flags = set(Path('/proc/cmdline').read_text().split())
        required = {'oneplus13.usb_host_probe=1', 'qcom_battmgr.oneplus13_otg_lease=1'}
        if os.geteuid() != 0 or not required <= flags or args.wait_seconds > FIRMWARE_WAIT_SECONDS_MAX:
            raise RuntimeError('USB firmware lease requires opt-in boot, root and at most 60s wait')
    mode = mode_paths()
    before = read(mode)
    if before != "device":
        raise RuntimeError("USB must begin in device role")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = {
        "scope": "ONEPLUS13_RAM_DWC3_ROLE_SWITCH",
        "mode_file": str(mode),
        "role_before": before,
        "host_role_requested": False,
        "host_inventory": None,
        "role_restored": False,
        "cleanup_complete": False,
        "host_peripheral_receipt": False,
        "host_peripheral_i_o_qualified": False,
        "typec_before": typec_inventory(),
        "inventory_before": inventory(),
        "firmware_otg_before": firmware_otg_inventory(),
    }
    try:
        if args.firmware_lease:
            status = firmware_lease_status()
            if status != {'state': 0, 'used': 0, 'dirty_ap': 0, 'dirty_switch': 0}:
                raise RuntimeError('USB firmware lease is not unused')
            result['firmware_lease_attempted'] = True
            result['firmware_lease_start_uptime'] = round(time.monotonic(), 3)
            lease_work_deadline = time.monotonic() + FIRMWARE_WORK_SECONDS
            firmware_lease_command('start')
            result['firmware_lease_active'] = firmware_lease_status()
            if result['firmware_lease_active'] != {'state': 1, 'used': 1, 'dirty_ap': 1, 'dirty_switch': 1}:
                raise RuntimeError('USB firmware lease did not verify both gates')
        write(mode, "host")
        result["host_role_requested"] = True
        time.sleep(3)
        result["role_after_host_request"] = read(mode)
        result["host_role_verified"] = result["role_after_host_request"] == "host"
        result["typec_after_host_request"] = typec_inventory()
        result["initial_host_inventory"] = inventory()
        result["initial_host_controller_ready"] = host_controller_ready(
            result["initial_host_inventory"])
        result["kernel_after_host_request"] = kernel_diagnostics()
        result["role_observations"] = []
        wait_seconds = args.wait_seconds
        if args.firmware_lease:
            # Leave room for the following bounded kernel diagnostic.
            wait_seconds = min(wait_seconds, max(0, lease_work_deadline - time.monotonic() - 8))
        result['peripheral_wait_requested_seconds'] = args.wait_seconds
        result['peripheral_wait_budget_seconds'] = round(wait_seconds, 3)
        result['peripheral_wait_begin_uptime'] = round(time.monotonic(), 3)
        result["host_inventory"], result["host_inventory_samples"] = (
            wait_for_host_driver(wait_seconds, reconcile=lambda: reconcile_host(
                mode, result["role_observations"]))
        )
        result['peripheral_wait_end_uptime'] = round(time.monotonic(), 3)
        result["typec_after_peripheral_wait"] = typec_inventory()
        result["role_after_peripheral_wait"] = read(mode)
        result["kernel_after_peripheral_wait"] = kernel_diagnostics()
        result["host_peripheral_receipt"] = bool(
            result["host_inventory"]["usb_peripherals"]
        )
        result['physical_connection_time_verified'] = False
        if not result['host_peripheral_receipt']:
            result['peripheral_wait_outcome'] = 'no_peripheral_observed_within_budget'
        if result["host_inventory"]["usb_storage_bound"]:
            if not args.firmware_lease or lease_work_deadline - time.monotonic() >= 32:
                result["storage_read_only"] = storage_read_only_probe()
            else:
                result['storage_skipped_lease_budget'] = True
        result["hid_input_present"] = usb_hid_input_present(result["host_inventory"])
        # Enumerating an HID and finding an input node does not prove events
        # were received. Keep actual I/O qualification on the bounded read.
        result["host_peripheral_i_o_qualified"] = bool(
            result.get("storage_read_only", {}).get("read_only_io_pass")
        )
    finally:
        try:
            write(mode, "device")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                result["role_after_restore"] = read(mode)
                if result["role_after_restore"] == "device" and inventory()["udc"]:
                    result["role_restored"] = True
                    break
                time.sleep(0.25)
        except (OSError, RuntimeError):
            result['device_role_restore_error'] = True
        finally:
            if result.get('firmware_lease_attempted'):
                try:
                    result['firmware_restore_begin_uptime'] = round(time.monotonic(), 3)
                    result['firmware_lease_restore'] = firmware_lease_restore()
                    result['firmware_restore_end_uptime'] = round(time.monotonic(), 3)
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    result['firmware_lease_restore'] = {'cleanup_complete': False}
        result["cleanup_inventory"] = inventory()
        result["kernel_after_restore"] = kernel_diagnostics()
        result["typec_after_restore"] = typec_inventory()
        result["cleanup_complete"] = bool(
            result["role_restored"]
            and result.get("storage_read_only", {}).get("cleanup_complete", True)
            and result.get('firmware_lease_restore', {}).get('cleanup_complete', True)
        )
        result["pass"] = bool(
            result["host_role_requested"] and result.get("host_role_verified", False)
            and result["cleanup_complete"]
        )
        result["functional_pass"] = bool(
            result["pass"] and result["host_peripheral_i_o_qualified"]
        )
        (STATE / "result.json").write_text(
            json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("RAM_USB_ROLE_RESULT " + json.dumps(result, sort_keys=True), flush=True)
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
