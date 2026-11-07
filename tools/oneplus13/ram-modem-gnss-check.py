#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install reviewed C.93 modem splits and start/stop MPSS in a RAM session."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time


RAM_MARKER = Path("/run/oneplus13/ram-session")
FIRMWARE_ROOT = Path("/usr/lib/firmware")
MAX_FILES = 64
MAX_BYTES = 96 * 1024 * 1024
INSTALLED_ROOT = FIRMWARE_ROOT / "qcom/sm8750/oneplus/dodge"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def find_mpss():
    rows = []
    for path in sorted(Path("/sys/class/remoteproc").glob("remoteproc[0-9]*")):
        target = str(path.resolve(strict=False))
        if "4080000.remoteproc" not in target:
            continue
        rows.append(
            {
                "path": path,
                "target": target,
                "name": (path / "name").read_text().strip(),
                "state": (path / "state").read_text().strip(),
                "firmware": (path / "firmware").read_text().strip()
                if (path / "firmware").is_file()
                else None,
            }
        )
    if len(rows) != 1:
        raise RuntimeError(f"Expected one MPSS remoteproc, found {len(rows)}")
    return rows[0]


def runtime_facts(entry):
    result = dict(entry)
    result.pop("path", None)
    result["rpmsg_devices"] = sorted(
        path.name for path in Path("/sys/bus/rpmsg/devices").glob("*")
    )[:64]
    result["rpmsg_drivers"] = {
        path.name: (
            path.resolve(strict=False).name
            if (path / "driver").exists()
            else None
        )
        for path in sorted(Path("/sys/bus/rpmsg/devices").glob("*"))[:64]
    }
    result["qrtr_devices"] = sorted(
        path.name for path in Path("/dev").glob("qrtr*") if path.exists()
    )
    result["qrtr_classes"] = sorted(
        path.name for path in Path("/sys/class/qrtr").glob("*")
    ) if Path("/sys/class/qrtr").is_dir() else []
    result["gnss_devices"] = sorted(
        name for name in Path("/dev").glob("gnss*") if name.exists()
    )
    result["qmi_devices"] = sorted(
        path.name for path in Path("/dev").glob("qmi*") if path.exists()
    )
    result["rpmsg_has_ipcrtr"] = any(
        "IPCRTR" in name for name in result["rpmsg_devices"]
    )
    return result


def install_firmware(archive, review_path, expected_archive_sha):
    if not RAM_MARKER.is_file():
        raise RuntimeError("Protected phone RAM session required")
    if sha256(archive) != expected_archive_sha:
        raise ValueError("Modem firmware archive hash mismatch")
    review = json.loads(review_path.read_text())
    expected = review["firmware_sha256"] | {
        "modemr.jsn": review["resource_json_sha256"]
    }
    if not 4 <= len(expected) <= MAX_FILES:
        raise ValueError("Unexpected modem firmware inventory size")
    actual = {}
    total = 0
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            if not member.isfile():
                raise ValueError("Modem archive must contain only regular files")
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe modem archive path")
            prefix = ("qcom", "sm8750", "oneplus", "dodge")
            if path.parts[:4] != prefix or len(path.parts) != 5:
                raise ValueError("Foreign modem archive path")
            total += member.size
            if total > MAX_BYTES:
                raise ValueError("Modem archive exceeds the reviewed size bound")
            member.uid = member.gid = 0
            member.uname = member.gname = "root"
            source.extract(member, FIRMWARE_ROOT, filter="data")
            actual[path.name] = hashlib.sha256(
                source.extractfile(member).read()
            ).hexdigest()
    if actual != expected:
        raise ValueError("Installed modem firmware differs from the review")
    return {
        "archive_sha256": expected_archive_sha,
        "file_count": len(actual),
        "installed_bytes_bounded_input": total,
        "files": actual,
    }


def verify_installed_firmware(review_path):
    """Verify the exact reviewed files already packaged in the rootfs."""
    if not RAM_MARKER.is_file():
        raise RuntimeError("Protected phone RAM session required")
    review = json.loads(review_path.read_text())
    expected = review["firmware_sha256"] | {
        "modemr.jsn": review["resource_json_sha256"]
    }
    if not 4 <= len(expected) <= MAX_FILES:
        raise ValueError("Unexpected modem firmware inventory size")
    actual = {}
    total = 0
    for name, digest in expected.items():
        path = INSTALLED_ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing installed modem firmware: {name}")
        size = path.stat().st_size
        total += size
        if total > MAX_BYTES:
            raise ValueError("Installed modem firmware exceeds the size bound")
        if sha256(path) != digest:
            raise ValueError(f"Installed modem firmware differs: {name}")
        actual[name] = digest
    return {
        "source": "installed_rootfs",
        "file_count": len(actual),
        "installed_bytes_bounded_input": total,
        "files": actual,
    }


def write_state(path, value, timeout):
    end = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < end:
        try:
            (path / "state").write_text(value + "\n")
            return
        except OSError as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f"Could not write remoteproc state={value}: {last_error}")


def wait_state(path, wanted, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = (path / "state").read_text().strip()
        if value == wanted:
            return value
        time.sleep(0.25)
    return (path / "state").read_text().strip()


def wait_control_plane(entry, timeout):
    end = time.monotonic() + timeout
    facts = runtime_facts(entry)
    while time.monotonic() < end:
        current = find_mpss()
        facts = runtime_facts(current)
        if current["state"] == "running" and facts["rpmsg_has_ipcrtr"]:
            return facts
        if current["state"] in ("crashed", "offline"):
            return facts
        time.sleep(0.5)
    return facts


def reusable_running_control_plane(entry, timeout=10):
    """Return facts for an already running MPSS without cycling remoteproc."""
    if entry["state"] != "running":
        return None
    facts = wait_control_plane(entry, timeout)
    return facts if facts["rpmsg_has_ipcrtr"] else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("start", "inspect", "verify", "stop"), required=True
    )
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--archive-sha256")
    args = parser.parse_args()
    result = {
        "scope": "ONEPLUS13_RAM_MPSS_GNSS_CONTROL",
        "mode": args.mode,
        "pass": False,
        "signature_authenticated": False,
        "gnss_fix_qualified": False,
    }
    entry = find_mpss()
    if args.mode == "inspect":
        result["facts"] = runtime_facts(entry)
        result["pass"] = True
        print("RAM_MODEM_GNSS_RESULT " + json.dumps(result, sort_keys=True))
        return
    if args.mode == "verify":
        if not args.review:
            parser.error("verify requires --review")
        result["firmware"] = verify_installed_firmware(args.review)
        result["facts"] = runtime_facts(entry)
        result["pass"] = (
            entry["state"] == "running" and result["facts"]["rpmsg_has_ipcrtr"]
        )
        print("RAM_MODEM_GNSS_RESULT " + json.dumps(result, sort_keys=True))
        raise SystemExit(0 if result["pass"] else 1)
    if args.mode == "start":
        if not args.review:
            parser.error("start requires --review")
        if args.archive or args.archive_sha256:
            if not args.archive or not args.archive_sha256:
                parser.error(
                    "archive installation requires --archive and "
                    "--archive-sha256"
                )
            result["firmware"] = install_firmware(
                args.archive, args.review, args.archive_sha256
            )
        else:
            result["firmware"] = verify_installed_firmware(args.review)
        control_modules = {}
        for module in ("rpmsg_char", "rpmsg_ctrl", "qrtr_tun"):
            process = subprocess.run(
                ["modprobe", "--ignore-install", module],
                capture_output=True,
                text=True,
                timeout=10,
            )
            control_modules[module] = {
                "returncode": process.returncode,
                "stderr": process.stderr[-1024:],
            }
        result["control_modules"] = control_modules
        if any(item["returncode"] for item in control_modules.values()):
            raise RuntimeError("Required modem control-plane module failed to load")
        facts = reusable_running_control_plane(entry)
        if facts:
            result["pass"] = True
            result["facts"] = facts
            result["started_state"] = "running"
            result["reused_running_session"] = True
            print("RAM_MODEM_GNSS_RESULT " + json.dumps(result, sort_keys=True))
            return
        if entry["state"] != "offline":
            if entry["state"] not in ("running", "crashed"):
                raise RuntimeError("MPSS must be offline, running or crashed before start")
            write_state(entry["path"], "stop", 6)
            state = wait_state(entry["path"], "offline", 25)
            if state != "offline":
                raise RuntimeError("MPSS did not stop before restart")
        attempts = []
        for attempt in range(1, 3):
            write_state(entry["path"], "start", 6)
            state = wait_state(entry["path"], "running", 50)
            facts = wait_control_plane(find_mpss(), 30)
            attempts.append(
                {"attempt": attempt, "started_state": state, "facts": facts}
            )
            if state == "running" and facts["rpmsg_has_ipcrtr"]:
                break
            if find_mpss()["state"] != "offline":
                write_state(entry["path"], "stop", 6)
                wait_state(entry["path"], "offline", 25)
            if attempt == 2:
                break
            time.sleep(1)
        result["attempts"] = attempts
        state = find_mpss()["state"]
        result["facts"] = runtime_facts(find_mpss())
        result["started_state"] = state
        result["pass"] = state == "running" and result["facts"]["rpmsg_has_ipcrtr"]
        if not result["pass"]:
            try:
                write_state(entry["path"], "stop", 4)
                result["failure_stop_state"] = wait_state(entry["path"], "offline", 15)
            except Exception as error:
                result["failure_stop_error"] = type(error).__name__
        print("RAM_MODEM_GNSS_RESULT " + json.dumps(result, sort_keys=True))
        raise SystemExit(0 if result["pass"] else 1)
    before = runtime_facts(entry)
    write_state(entry["path"], "stop", 6)
    state = wait_state(entry["path"], "offline", 25)
    result.update(facts_before_stop=before, final_state=state)
    result["pass"] = state == "offline"
    result["remote_processor_offline"] = state == "offline"
    print("RAM_MODEM_GNSS_RESULT " + json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
