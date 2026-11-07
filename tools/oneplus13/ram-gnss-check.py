#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise QMI location over QRTR after reviewed MPSS startup."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


NMEA = re.compile(r"^\$(?:GP|GN|GL|GA|GB)(?:GGA|RMC),.*\*[0-9A-Fa-f]{2}$")


def bounded_stdout(output):
    """Retain lifecycle evidence when a long NMEA stream fills the tail."""
    tail = output[-8192:]
    markers = []
    for prefix in ("LOCATION_SESSION_NMEA_DEFAULTS ",
                   "LOCATION_SESSION_POSITION_EVENTS ", "LOCATION_SESSION_NO_SESSION ",
                   "LOCATION_SESSION_START_PROFILE ",
                   "LOCATION_ENGINE_LOCK_SAVED ", "LOCATION_ENGINE_UNLOCK ",
                   "LOCATION_ENGINE_UNLOCK_CONFIRMED ",
                   "LOCATION_ENGINE_LOCK_RESTORED ",
                   "LOCATION_SESSION_STARTED ", "LOCATION_SESSION_STOPPED "):
        marker = next((line for line in output.splitlines()
                       if line.startswith(prefix) and len(line) <= 512), None)
        if marker is not None and marker not in tail.splitlines():
            markers.append(marker)
    services = loc_service_lines(output.splitlines())
    markers += [line for line in services if line not in tail.splitlines()]
    return "\n".join(markers + [tail])


def loc_service_lines(lines):
    matches = [line for line in lines if re.fullmatch(
        r"LOCATION_SESSION_LOC_SERVICE node=[0-9]+ port=[0-9]+ version=[0-9]+ instance=[0-9]+ selected_snapshot=[01]", line)]
    selected = [line for line in matches if line.endswith("selected_snapshot=1")]
    return list(dict.fromkeys(selected + matches))[:16]


def run(args, timeout):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": bounded_stdout(result.stdout),
            "stderr": result.stderr[-8192:],
        }
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr or ""
        return {
            "returncode": 124,
            "timed_out": True,
            "stdout": stdout[-8192:],
            "stderr": stderr[-8192:],
        }


def nmea_coordinate(value, direction):
    if not value or direction not in ("N", "S", "E", "W"):
        return None
    split = 2 if direction in ("N", "S") else 3
    if len(value) <= split:
        return None
    try:
        degrees = int(value[:split])
        minutes = float(value[split:])
    except ValueError:
        return None
    limit = 90 if direction in ("N", "S") else 180
    if not math.isfinite(minutes) or not 0 <= minutes < 60 or not 0 <= degrees <= limit:
        return None
    if degrees == limit and minutes != 0:
        return None
    result = degrees + minutes / 60
    return -result if direction in ("S", "W") else result


def parse_fix(lines):
    fixes = []
    for line in lines:
        if not NMEA.fullmatch(line):
            continue
        payload, checksum = line[1:].rsplit("*", 1)
        actual = 0
        for character in payload:
            actual ^= ord(character)
        if actual != int(checksum, 16):
            continue
        fields = line[1:].split("*", 1)[0].split(",")
        if fields[0].endswith("RMC") and len(fields) >= 7 and fields[2] == "A":
            latitude = nmea_coordinate(fields[3], fields[4])
            longitude = nmea_coordinate(fields[5], fields[6])
            if latitude is not None and longitude is not None:
                fixes.append(
                    {
                        "type": "RMC",
                        "latitude": latitude,
                        "longitude": longitude,
                        "date": fields[9] if len(fields) > 9 else None,
                    }
                )
        if (fields[0].endswith("GGA") and len(fields) >= 8
                and fields[6] in ("1", "2", "3", "4", "5")
                and fields[7].isdigit()):
            latitude = nmea_coordinate(fields[2], fields[3])
            longitude = nmea_coordinate(fields[4], fields[5])
            if latitude is not None and longitude is not None:
                fixes.append(
                    {
                        "type": "GGA",
                        "latitude": latitude,
                        "longitude": longitude,
                        "quality": int(fields[6]),
                        "satellites": int(fields[7]) if len(fields) > 7 and fields[7] else None,
                    }
                )
    return fixes[-8:]


def marker_json(lines, prefix):
    """Return the JSON payload of one bounded marker line, or None."""
    for line in lines:
        if line.startswith(prefix) and len(line) <= 512:
            try:
                return json.loads(line[len(prefix):])
            except ValueError:
                return None
    return None


def collect_native_session(native, seconds, unlock=False):
    """Run one native QRTR client for setup, start, follow and stop.

    With unlock=True the helper first saves the legacy engine lock, sets
    LOCK_NONE, confirms it by readback, runs the normal session, then restores
    the saved lock and confirms the restore. The restore proof is required for a
    clean cleanup; an unproven restore is never reported as clean.
    """
    if not 5 <= seconds <= 180:
        raise ValueError('Native GNSS collection must stay between 5 and 180 seconds')
    native = str(native)
    # Native soft deadline is seconds+45 (seconds+90 while the unlock cycle adds
    # a SET plus two readback queries); pending start and stop take up to 20
    # seconds each. Keep ten more for GLib timer granularity/process exit.
    arguments = [native, "--seconds", str(seconds)]
    if unlock:
        arguments = [native, "--unlock-engine-lock", "--seconds", str(seconds)]
    process = run(arguments, seconds + (140 if unlock else 95))
    lines = process.get("stdout", "").splitlines()
    loc_services = loc_service_lines(lines)
    nmea = [
        line[5:].strip()
        for line in lines
        if line.startswith("NMEA ") and line[5:].strip()
    ][-64:]
    fixes = parse_fix(nmea)
    positions = []
    for line in lines:
        if not line.startswith("LOCATION_SESSION_POSITION "):
            continue
        try:
            value = json.loads(line.split(" ", 1)[1])
            latitude, longitude = value["latitude"], value["longitude"]
            if (value.get("status") == "success" and value.get("satellite") is True
                    and type(latitude) in (int, float) and type(longitude) in (int, float)
                    and math.isfinite(latitude) and math.isfinite(longitude)
                    and -90 <= latitude <= 90 and -180 <= longitude <= 180):
                positions.append({"type": "QMI_LOC_SATELLITE", "latitude": latitude,
                                  "longitude": longitude})
        except (ValueError, KeyError, TypeError):
            continue
    fixes = (fixes + positions)[-8:]
    started = any(
        line.startswith("LOCATION_SESSION_STARTED") for line in lines
    )
    stop_markers = [line for line in lines
                    if re.fullmatch(r"LOCATION_SESSION_STOPPED nmea=[0-9]+", line)]
    stopped = bool(stop_markers)
    # The native client deliberately exits 1 after a rejected/lost START even
    # when the same client's STOP succeeded. Keep functional failure separate
    # from that explicit cleanup acknowledgement; timeout/crash stays unsafe.
    start_errors = [line for line in process.get("stderr", "").splitlines() if line]
    # Engine-lock evidence may share stderr with the protocol error; only the
    # non-lock lines take part in the rejected-start classification.
    lock_errors = [line for line in start_errors
                   if line.startswith("LOCATION_ENGINE_LOCK_")]
    protocol_errors = [line for line in start_errors
                       if not line.startswith("LOCATION_ENGINE_LOCK_")]
    lock_unrestored = any(line.startswith("LOCATION_ENGINE_LOCK_UNRESTORED")
                          for line in lock_errors)
    rejected_start_cleaned = (
        process.get("returncode") == 1 and not started and len(stop_markers) == 1
        and not any(line.startswith("LOCATION_SESSION_NO_SESSION") for line in lines)
        and protocol_errors == ["LOCATION_SESSION_ERROR operation=start-result "
                                "error=QMI protocol error (46): 'GeneralError'"]
    )
    clean_stop = (stopped and process.get("timed_out", False) is False
                  and (process.get("returncode") == 0 or rejected_start_cleaned)
                  and not lock_unrestored)
    saved_lock = marker_json(lines, "LOCATION_ENGINE_LOCK_SAVED ")
    restored_lock = marker_json(lines, "LOCATION_ENGINE_LOCK_RESTORED ")
    already_none = any(line.startswith("LOCATION_ENGINE_UNLOCK already=none")
                       for line in lines)
    unlock_confirmed = marker_json(lines, "LOCATION_ENGINE_UNLOCK_CONFIRMED ")
    lock_changed = bool(saved_lock) and saved_lock.get("lock_type") != 1
    restore_required = bool(unlock) and lock_changed
    restore_proven = bool(restored_lock) and (
        restored_lock.get("lock_type") == saved_lock.get("lock_type"))
    no_session = ("LOCATION_SESSION_NO_SESSION start_requested=0" in lines
                  and not started and not stopped and process.get("returncode") == 1
                  and process.get("timed_out", False) is False)
    return {
        "native": native,
        "loc_services": loc_services,
        "returncode": process.get("returncode"),
        "timed_out": process.get("timed_out", False),
        "started": started,
        "stopped": stopped,
        "no_session_started": no_session,
        "cleanup_not_needed": no_session,
        "position_events_fallback": any(
            line.startswith("LOCATION_SESSION_POSITION_EVENTS ") for line in lines
        ),
        "nmea_defaults_used": any(
            line.startswith("LOCATION_SESSION_NMEA_DEFAULTS ") for line in lines
        ),
        "start_profile": next(
            (line[len("LOCATION_SESSION_START_PROFILE "):] for line in lines
             if line.startswith("LOCATION_SESSION_START_PROFILE ")
             and len(line) <= 512), None),
        "unlock_requested": bool(unlock),
        "lock_saved": saved_lock,
        "lock_already_unlocked": already_none,
        "lock_unlock_confirmed": unlock_confirmed,
        "lock_restored": restored_lock,
        "lock_restore_required": restore_required,
        "lock_restore_proven": restore_proven,
        "lock_unrestored": lock_unrestored,
        "stderr": process.get("stderr", "")[-8192:],
        "nmea": nmea,
        "fixes": fixes,
        "fix_acquired": bool(fixes),
        "engine_control_qualified": started and clean_stop,
        "cleanup_complete": (clean_stop or no_session)
                            and not (restore_required and not restore_proven),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default=None)
    parser.add_argument("--seconds", type=int, default=45)
    parser.add_argument("--ready-seconds", type=int, default=60)
    parser.add_argument("--temporary-unlock", action="store_true",
                        help="Save, set LOCK_NONE, then restore the legacy engine lock")
    args = parser.parse_args()
    if not 5 <= args.seconds <= 180:
        raise ValueError("GNSS collection must stay between 5 and 180 seconds")
    if not 0 <= args.ready_seconds <= 90:
        raise ValueError("GNSS readiness wait must stay between 0 and 90 seconds")
    if not Path("/run/oneplus13/ram-session").is_file():
        raise RuntimeError("Protected phone RAM session required")
    native = Path("/usr/libexec/oneplus13-location-session")
    if native.is_file() and os.access(native, os.X_OK):
        result = collect_native_session(native, args.seconds,
                                        unlock=args.temporary_unlock)
        # Self-identification: proves which collector bytes and which argument
        # vector produced this receipt when a round is reviewed afterwards.
        result["collector_sha256"] = hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest()
        result["argv"] = list(sys.argv[1:])
        result["scope"] = "ONEPLUS13_RAM_QMI_GNSS"
        result["pass"] = bool(
            result["engine_control_qualified"]
            and result["fix_acquired"]
            and result["cleanup_complete"]
        )
        print("RAM_GNSS_RESULT " + json.dumps(result, sort_keys=True))
        raise SystemExit(0 if result["pass"] else 1)
    raise RuntimeError("Native same-client GNSS helper is required; unsafe legacy fallback disabled")

if __name__ == "__main__":
    main()
