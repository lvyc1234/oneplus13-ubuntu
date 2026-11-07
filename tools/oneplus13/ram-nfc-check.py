#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Load and verify the reviewed NXP NCI path in one disposable RAM session."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


POLICY = Path("/etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf")
RAM_MARKER = Path("/run/oneplus13/ram-session")
ALLOWED = ("nfc", "nci", "nxp_nci", "nxp_nci_i2c")


def sha256(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def run(args, timeout=8):
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def allow_exact_modules():
    if not RAM_MARKER.is_file():
        raise RuntimeError("Protected phone RAM session required")
    original = POLICY.read_text(encoding="utf-8")
    lines = original.splitlines()
    changed = []
    output = []
    for line in lines:
        module = None
        if line.startswith("blacklist "):
            module = line.removeprefix("blacklist ")
        elif line.startswith("install ") and line.endswith(" /bin/false"):
            module = line.removeprefix("install ").removesuffix(" /bin/false")
        if module in ALLOWED:
            changed.append(module)
            continue
        output.append(line)
    counts = {module: changed.count(module) for module in ALLOWED}
    if counts == {module: 0 for module in ALLOWED}:
        # The source image may build the reviewed NCI closure in and carry no
        # deny entries. No policy mutation is then required.
        return []
    if counts != {module: 2 for module in ALLOWED}:
        raise ValueError(
            "Policy does not contain the exact NFC deny set: "
            + ",".join(sorted(changed))
        )
    POLICY.write_text("\n".join(output) + "\n", encoding="utf-8")
    return list(ALLOWED)


def module_loaded(name):
    return Path("/sys/module") / name.replace("-", "_")


def binding_facts():
    candidates = []
    for target in sorted(Path("/sys/bus/i2c/devices").glob("*-0028")):
        driver = target / "driver"
        name = None
        try:
            name = (target / "name").read_text().strip()
        except (FileNotFoundError, PermissionError, OSError):
            pass
        candidates.append(
            {
                "name": target.name,
                "device_name": name,
                "driver": driver.resolve(strict=False).name if driver.exists() else None,
                "driver_link": str(driver.resolve(strict=False)) if driver.exists() else None,
                "target": str(target.resolve(strict=False)),
            }
        )
    char = Path("/dev/nq-nci")
    interrupts = ""
    try:
        interrupts = Path("/proc/interrupts").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        pass
    irq_rows = [
        line
        for line in interrupts.splitlines()
        if "nxp" in line.lower() or "nfc" in line.lower()
    ][:16]
    return {
        "i2c": candidates,
        "vendor_char_device_optional": "/dev/nq-nci",
        "char_device_present": char.exists(),
        "nfc_class": sorted(
            item.name for item in Path("/sys/class/nfc").glob("nfc*")
        )
        if Path("/sys/class/nfc").is_dir()
        else [],
        "modules": {name: module_loaded(name).is_dir() for name in ALLOWED},
        "interrupt_rows": irq_rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-sha256", required=True)
    args = parser.parse_args()
    if not POLICY.is_file():
        raise RuntimeError("Module policy is absent")
    before_sha = sha256(POLICY)
    if before_sha != args.policy_sha256:
        raise ValueError("Module policy hash mismatch")
    changed = allow_exact_modules()
    after_policy_sha = sha256(POLICY)

    load = {}
    for module in ALLOWED:
        result = run(["modprobe", "--ignore-install", module])
        load[module] = {
            "returncode": result.returncode,
            "stdout": result.stdout[-2048:],
            "stderr": result.stderr[-2048:],
        }
        if result.returncode:
            fail = dict(binding_facts())
            fail.update(
                {
                    "scope": "ONEPLUS13_MANUAL_NXP_NCI_BIND",
                    "pass": False,
                    "policy_sha256_before": before_sha,
                    "policy_sha256_after": after_policy_sha,
                    "allowed_policy_entries_removed": changed,
                    "load": load,
                }
            )
            print("NFC_BIND_RESULT " + json.dumps(fail, sort_keys=True))
            raise SystemExit(1)

    deadline = time.monotonic() + 8
    facts = binding_facts()
    while time.monotonic() < deadline:
        facts = binding_facts()
        if (
            facts["nfc_class"]
            and any(row["driver"] == "nxp-nci_i2c" for row in facts["i2c"])
        ):
            break
        time.sleep(0.25)
    pass_ = (
        bool(facts["nfc_class"])
        and any(row["driver"] == "nxp-nci_i2c" for row in facts["i2c"])
        and all(facts["modules"].values())
    )
    result = {
        "scope": "ONEPLUS13_MANUAL_NXP_NCI_BIND",
        "pass": pass_,
        "chipset": "SN220T",
        "i2c_bus": "I2C0",
        "i2c_address_7bit": 0x28,
        "irq_gpio": 75,
        "enable_gpio": 34,
        "clock_request_gpio": 35,
        "policy_sha256_before": before_sha,
        "policy_sha256_after": after_policy_sha,
        "allowed_policy_entries_removed": changed,
        "load": load,
        "facts": facts,
        "controller_reset_or_tag_polled": False,
        "hardware_tag_qualified": False,
    }
    print("NFC_BIND_RESULT " + json.dumps(result, sort_keys=True))
    raise SystemExit(0 if pass_ else 1)


if __name__ == "__main__":
    main()
