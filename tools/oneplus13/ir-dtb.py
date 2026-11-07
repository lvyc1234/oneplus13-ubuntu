#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Enable the exact OnePlus 13 SPI infrared board path in a RAM test tree."""

import copy
import hashlib
import importlib

from stock import Fdt, cells, strings


modem = importlib.import_module("modem-gnss-dtb")
text = modem.video.text
u32 = modem.video.camera.u32

TLMM = "/soc@0/pinctrl@f100000"
RSC = "/soc@0/rsc@16500000"
L9B = RSC + "/regulators-0/ldo9"
SPI = "/soc@0/geniqup@ac0000/spi@a94000"
I2C = "/soc@0/geniqup@ac0000/i2c@a94000"
IR = SPI + "/ir@0"
IR_UV = 3056000


def validate(tree):
    l9 = tree.nodes[L9B]
    if strings(l9["regulator-name"]) != ["vreg_l9b_2p9"]:
        raise ValueError("Wrong L9B regulator identity")
    if cells(l9["regulator-min-microvolt"]) != [2960000]:
        raise ValueError("Unexpected L9B lower voltage")
    if cells(l9["regulator-max-microvolt"]) != [3008000]:
        raise ValueError("Unexpected L9B upper voltage")
    spi = tree.nodes[SPI]
    if "qcom,geni-spi" not in strings(spi["compatible"]):
        raise ValueError("Wrong SPI5 controller")
    if strings(spi.get("status", b"okay\0")) != ["disabled"]:
        raise ValueError("IR candidate must begin disabled")
    if cells(spi["interrupts"]) != [0, 358, 4]:
        raise ValueError("Wrong SPI5 IRQ")
    if strings(tree.nodes[I2C]["status"]) != ["disabled"]:
        raise ValueError("SPI5 I2C sibling must begin disabled")


def derive(blob):
    base, modem_report = modem.derive(blob)
    tree = Fdt(base)
    before = copy.deepcopy(tree.nodes)
    validate(tree)
    fresh = max(tree.phandles) + 1
    for path in (L9B, TLMM):
        if "phandle" not in tree.nodes[path]:
            tree.nodes[path]["phandle"] = u32(fresh)
            fresh += 1
    l9_phandle = cells(tree.nodes[L9B]["phandle"])[0]
    gpio = cells(tree.nodes[TLMM]["phandle"])[0]
    tree.nodes[L9B]["regulator-max-microvolt"] = u32(IR_UV)
    active = TLMM + "/dodge-ir-mosi-active-state"
    sleep = TLMM + "/dodge-ir-mosi-sleep-state"
    if active in tree.nodes or sleep in tree.nodes or IR in tree.nodes:
        raise ValueError("IR node already exists")
    tree.nodes[active] = {
        "pins": text("gpio53"),
        "function": text("qup1_se5"),
        "drive-strength": u32(6),
        "bias-disable": b"",
    }
    tree.nodes[sleep] = {
        "pins": text("gpio53"),
        "function": text("gpio"),
        "drive-strength": u32(2),
        "bias-disable": b"",
        "output-low": b"",
    }
    active_phandle = fresh
    tree.nodes[active]["phandle"] = u32(active_phandle)
    sleep_phandle = active_phandle + 1
    tree.nodes[sleep]["phandle"] = u32(sleep_phandle)
    tree.nodes[SPI]["status"] = text("okay")
    tree.nodes[SPI]["pinctrl-names"] = text("default", "sleep")
    tree.nodes[SPI]["pinctrl-0"] = u32(active_phandle)
    tree.nodes[SPI]["pinctrl-1"] = u32(sleep_phandle)
    tree.nodes[I2C]["status"] = text("disabled")
    tree.nodes[IR] = {
        "compatible": text("oneplus,dodge-ir-experimental"),
        "reg": u32(0),
        "spi-max-frequency": u32(5000000),
        "irda_regulator-supply": u32(l9_phandle),
        "status": text("okay"),
    }
    changed = {path for path in before if before[path] != tree.nodes[path]}
    if changed != {L9B, SPI}:
        raise ValueError("Unexpected existing-node mutation: " + ",".join(sorted(changed)))
    new = set(tree.nodes) - set(before)
    if new != {active, sleep, IR}:
        raise ValueError("Unexpected new IR node")
    final = tree.to_blob()
    check = Fdt(final)
    if check.nodes != tree.nodes or check.reservations != tree.reservations:
        raise ValueError("IR tree readback mismatch")
    return final, {
        "scope": "MANUAL_ONEPLUS13_SPI_INFRARED_RAM_TREE",
        "base_sha256": hashlib.sha256(blob).hexdigest(),
        "output_sha256": hashlib.sha256(final).hexdigest(),
        "modem_derivation": modem_report,
        "changed_nodes": sorted(changed),
        "additional_nodes": sorted(new),
        "spi_controller": SPI,
        "i2c_sibling": I2C,
        "ir_node": IR,
        "supply_node": L9B,
        "supply_microvolt": IR_UV,
        "gpio": 53,
        "spi_hz": 5000000,
        "default_boot_tree_changed": False,
        "shared_supply_consumers_not_in_qualified_dtb_require_runtime_audit": True,
        "optical_output_qualified": False,
    }
