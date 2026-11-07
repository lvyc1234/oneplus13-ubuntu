#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Add the reviewed SN220 NXP NCI path to the default OnePlus 13 RAM tree."""

import copy
import hashlib
import importlib
from pathlib import Path

from stock import Fdt, cells, strings


audio = importlib.import_module("audio-dtb")
u32 = audio.u32
text = audio.text

TLMM = "/soc@0/pinctrl@f100000"
NFC_I2C = "/soc@0/geniqup@ac0000/i2c@a80000"
NFC = NFC_I2C + "/nfc@28"


def phandle_factory(tree):
    fresh = max(tree.phandles) + 1

    def ensure(path):
        nonlocal fresh
        if "phandle" not in tree.nodes[path]:
            tree.nodes[path]["phandle"] = u32(fresh)
            fresh += 1
        return cells(tree.nodes[path]["phandle"])[0]

    return ensure


def derive(blob):
    result, audio_report = audio.derive(blob)
    tree = Fdt(result)
    before = copy.deepcopy(tree.nodes)
    if hashlib.sha256(blob).hexdigest() != audio.BASE_SHA:
        raise ValueError("Wrong base device-tree identity")
    if "qcom,geni-i2c" not in strings(tree.nodes[NFC_I2C]["compatible"]):
        raise ValueError("Wrong reviewed NFC I2C provider")
    ensure = phandle_factory(tree)
    gpio = ensure(TLMM)

    def add(path, properties):
        if path in tree.nodes:
            raise ValueError("NFC node already exists: " + path)
        tree.nodes[path] = properties
        return ensure(path)

    irq = add(TLMM + "/nfc-irq-active-state", {})
    enable = add(TLMM + "/nfc-enable-active-state", {})
    sleep = add(TLMM + "/nfc-sleep-state", {})
    tree.nodes[TLMM + "/nfc-irq-active-state/irq-pins"] = {
        "pins": text("gpio75"),
        "function": text("gpio"),
        "drive-strength": u32(2),
        "bias-pull-down": b"",
    }
    tree.nodes[TLMM + "/nfc-enable-active-state/enable-pins"] = {
        "pins": text("gpio34", "gpio35"),
        "function": text("gpio"),
        "drive-strength": u32(2),
        "bias-disable": b"",
    }
    tree.nodes[TLMM + "/nfc-sleep-state/sleep-pins"] = {
        "pins": text("gpio34", "gpio35"),
        "function": text("gpio"),
        "drive-strength": u32(2),
        "bias-disable": b"",
    }
    tree.nodes[NFC_I2C]["status"] = text("okay")
    tree.nodes[NFC_I2C]["clock-frequency"] = u32(400000)
    add(
        NFC,
        {
            "compatible": text("nxp,nxp-nci-i2c"),
            "reg": u32(0x28),
            "interrupt-parent": u32(gpio),
            "interrupts": u32(75, 0),
            "wakeup-source": b"",
            "enable-gpios": u32(gpio, 34, 0),
            "pinctrl-0": u32(irq, enable),
            "pinctrl-1": u32(sleep),
            "pinctrl-names": text("default", "sleep"),
            "status": text("okay"),
        },
    )

    changed = {path for path in before if before[path] != tree.nodes[path]}
    if changed != {NFC_I2C}:
        raise ValueError("Unexpected existing-node mutation")
    new = set(tree.nodes) - set(before)
    if any(not path.startswith((TLMM + "/nfc-", NFC)) for path in new):
        raise ValueError("Unexpected new NFC node")
    final = tree.to_blob()
    check = Fdt(final)
    if check.nodes != tree.nodes or check.reservations != tree.reservations:
        raise ValueError("NFC tree readback mismatch")
    return final, {
        "scope": "DEFAULT_NFC_SN220_NXP_NCI_TREE",
        "base_sha256": audio.BASE_SHA,
        "output_sha256": hashlib.sha256(final).hexdigest(),
        "audio_derivation": audio_report,
        "changed_nodes": sorted(changed),
        "additional_nodes": sorted(new),
        "chipset_source": "C93_DODGE_NFC_DTSI_SN220T",
        "i2c": NFC_I2C,
        "i2c_address_7bit": 0x28,
        "irq_gpio": 75,
        "enable_gpio": 34,
        "clock_request_gpio": 35,
        "clock_request_is_firmware_managed": True,
        "driver": "nxp,nxp-nci-i2c",
        "module_closure": ["nfc", "nci", "nxp_nci", "nxp_nci_i2c"],
        "default_boot_tree_changed": True,
        "controller_bind_hardware_qualified": False,
        "tag_poll_hardware_qualified": False,
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    blob, report = derive(args.stock.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(blob)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
