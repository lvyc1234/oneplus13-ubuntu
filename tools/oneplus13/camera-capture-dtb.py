#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Derive the isolated S5KJN5 C-PHY capture graph for RAM testing only."""

import copy
import hashlib
import importlib

from stock import Fdt, cells, strings


probe = importlib.import_module("camera-probe-dtb")
u32 = probe.u32
text = probe.text

SOC = "/soc@0"
GCC = SOC + "/clock-controller@100000"
CAMCC = SOC + "/clock-controller@ade0000"
RPMHPD = SOC + "/rsc@16500000/power-controller"
RPMHPD_OPP_LOW_SVS_D1 = RPMHPD + "/opp-table/opp-56"
APPS_SMMU = SOC + "/iommu@15000000"
CNOC_MAIN = SOC + "/interconnect@1500000"
CONFIG_NOC = SOC + "/interconnect@1600000"
MMSS_NOC = SOC + "/interconnect@1780000"
GEM_NOC = SOC + "/interconnect@24100000"
MC_VIRT = "/interconnect-1"
CAMSS = SOC + "/isp@ad27000"

CSI_0P9 = SOC + "/rsc@16500000/regulators-2/ldo1"
CSI_1P2 = SOC + "/rsc@16500000/regulators-3/ldo3"
SENSOR_CORE = probe.CORE

PHY_TYPE_CPHY = 11
V4L2_FWNODE_BUS_TYPE_CSI2_CPHY = 1
QCOM_ICC_TAG_ACTIVE_ONLY = 3
QCOM_ICC_TAG_ALWAYS = 7

CAMSS_REGS = (
    (0x0AD27000, 0x2B00),
    (0x0AD2A000, 0x2B00),
    (0x0AD2D000, 0x2B00),
    (0x0AD6D000, 0x0A00),
    (0x0AD72000, 0x0A00),
    (0x0AD8B000, 0x0400),
    (0x0AD8C000, 0x0400),
    (0x0AD8D000, 0x0400),
    (0x0AC86000, 0x10000),
    (0x0AC96000, 0x10000),
    (0x0ACA6000, 0x10000),
    (0x0AD6E000, 0x3000),
    (0x0AD73000, 0x3000),
)
CAMSS_REG_NAMES = (
    "csid0",
    "csid1",
    "csid2",
    "csid_lite0",
    "csid_lite1",
    "csitpg0",
    "csitpg1",
    "csitpg2",
    "vfe0",
    "vfe1",
    "vfe2",
    "vfe_lite0",
    "vfe_lite1",
)
CAMSS_CLOCKS = (
    (CAMCC, 3),
    (CAMCC, 9),
    (CAMCC, 0),
    (CAMCC, 1),
    (CAMCC, 13),
    (CAMCC, 15),
    (CAMCC, 17),
    (CAMCC, 11),
    (CAMCC, 42),
    (CAMCC, 44),
    (GCC, 7),
    (GCC, 8),
    (CAMCC, 109),
    (CAMCC, 110),
    (CAMCC, 114),
    (CAMCC, 115),
    (CAMCC, 119),
    (CAMCC, 120),
    (CAMCC, 62),
    (CAMCC, 61),
    (CAMCC, 64),
    (CAMCC, 65),
    (CAMCC, 102),
)
CAMSS_CLOCK_NAMES = (
    "camnoc_nrt_axi",
    "camnoc_rt_axi",
    "cpas_ahb",
    "cpas_fast_ahb",
    "cpas_vfe0",
    "cpas_vfe1",
    "cpas_vfe2",
    "cpas_vfe_lite",
    "csid",
    "csid_csiphy_rx",
    "gcc_axi_hf",
    "gcc_axi_sf",
    "vfe0",
    "vfe0_fast_ahb",
    "vfe1",
    "vfe1_fast_ahb",
    "vfe2",
    "vfe2_fast_ahb",
    "vfe_lite",
    "vfe_lite_ahb",
    "vfe_lite_cphy_rx",
    "vfe_lite_csid",
    "qdss_debug_xo",
)
CAMSS_INTERRUPTS = (601, 603, 431, 605, 376, 433, 436, 457, 606, 377)
CAMSS_IRQ_NAMES = (
    "csid0",
    "csid1",
    "csid2",
    "csid_lite0",
    "csid_lite1",
    "vfe0",
    "vfe1",
    "vfe2",
    "vfe_lite0",
    "vfe_lite1",
)
PHYS = tuple(
    {
        "id": index,
        "path": SOC + f"/phy@{0x0ADA9000 + index * 0x2000:08x}",
        "reg": 0x0ADA9000 + index * 0x2000,
        "irq": 477 + index,
        "csiphy_clk": 45 + index,
        "timer_clk": 30 + index * 2,
    }
    for index in range(6)
)


def _safe_strings(value):
    try:
        return strings(value)
    except ValueError:
        return []


def derive(blob):
    base, probe_report = probe.derive(blob)
    tree = Fdt(base)
    before = copy.deepcopy(tree.nodes)
    fresh = max(tree.phandles) + 1

    def phandle(path):
        nonlocal fresh
        if path not in tree.nodes:
            raise ValueError(f"Missing provider: {path}")
        if "phandle" not in tree.nodes[path]:
            if fresh >= 0xFFFFFF00:
                raise ValueError("No bounded phandle space")
            tree.nodes[path]["phandle"] = u32(fresh)
            fresh += 1
        value = cells(tree.nodes[path]["phandle"])
        if len(value) != 1:
            raise ValueError(f"Wrong phandle: {path}")
        return value[0]

    def add(path, properties):
        if path in tree.nodes:
            raise ValueError(f"Capture node already exists: {path}")
        tree.nodes[path] = properties
        return phandle(path)

    for path, compatible in (
        (GCC, "qcom,sm8750-gcc"),
        (CAMCC, "qcom,sm8750-camcc"),
        (RPMHPD, "qcom,sm8750-rpmhpd"),
        (APPS_SMMU, "qcom,sm8750-smmu-500"),
        (CNOC_MAIN, "qcom,sm8750-cnoc-main"),
        (CONFIG_NOC, "qcom,sm8750-config-noc"),
        (MMSS_NOC, "qcom,sm8750-mmss-noc"),
        (GEM_NOC, "qcom,sm8750-gem-noc"),
        (MC_VIRT, "qcom,sm8750-mc-virt"),
        (CSI_0P9, "regulator"),
        (CSI_1P2, "regulator"),
        (SENSOR_CORE, "regulator"),
        (RPMHPD_OPP_LOW_SVS_D1, "operating-point"),
    ):
        if compatible not in _safe_strings(
            tree.nodes[path].get("compatible", b"")
        ) and path not in (CSI_0P9, CSI_1P2, SENSOR_CORE, RPMHPD_OPP_LOW_SVS_D1):
            actual = _safe_strings(tree.nodes[path].get("compatible", b""))
            if compatible not in actual:
                raise ValueError(f"Wrong capture provider: {path}: {actual}")

    gcc = phandle(GCC)
    camcc = phandle(CAMCC)
    rpmhpd = phandle(RPMHPD)
    apps_smmu = phandle(APPS_SMMU)
    cnoc_main = phandle(CNOC_MAIN)
    config_noc = phandle(CONFIG_NOC)
    mmss_noc = phandle(MMSS_NOC)
    gem_noc = phandle(GEM_NOC)
    mc_virt = phandle(MC_VIRT)
    rpmhpd_low_svs_d1 = phandle(RPMHPD_OPP_LOW_SVS_D1)
    phy_0p9 = phandle(CSI_0P9)
    phy_1p2 = phandle(CSI_1P2)
    sensor_core = phandle(SENSOR_CORE)

    # The public driver consumes five supplies; this phone uses a 1.8V I/O
    # rail and the 1.0V core rail also supplies VDDM.
    sensor = tree.nodes[probe.SENSOR]
    sensor["vddd-supply"] = u32(sensor_core)
    sensor["vddm-supply"] = u32(sensor_core)
    sensor_endpoint = tree.nodes[probe.SENSOR + "/port/endpoint"]
    tree.nodes[probe.SENSOR + "/port"]["#address-cells"] = u32(1)
    tree.nodes[probe.SENSOR + "/port"]["#size-cells"] = u32(0)
    sensor_endpoint["data-lanes"] = u32(0, 1, 2)
    sensor_endpoint["bus-type"] = u32(V4L2_FWNODE_BUS_TYPE_CSI2_CPHY)
    sensor_endpoint["reg"] = u32(0)
    sensor_endpoint["link-frequencies"] = u32(
        (1400000000 >> 32) & 0xFFFFFFFF, 1400000000 & 0xFFFFFFFF
    )
    tree.nodes[probe.CCI + "/i2c-bus@0"]["clock-frequency"] = u32(1000000)

    phy_refs = {}
    for phy in PHYS:
        path = phy["path"]
        phy_refs[phy["id"]] = add(
            path,
            {
                "compatible": text(
                    "oneplus,dodge-ultrawide-csi2-phy-experimental"
                ),
                "reg": u32(0, phy["reg"], 0, 0x2000),
                "clocks": u32(
                    camcc,
                    phy["csiphy_clk"],
                    camcc,
                    phy["timer_clk"],
                    camcc,
                    25,
                ),
                "clock-names": text("core", "timer", "ahb"),
                "interrupts": u32(0, phy["irq"], 1),
                "power-domains": u32(
                    camcc, 0, rpmhpd, 6, rpmhpd, 10
                ),
                "power-domain-names": text("top", "mmcx", "mx"),
                "vdda-0p9-supply": u32(phy_0p9),
                "vdda-1p2-supply": u32(phy_1p2),
                "operating-points-v2": u32(0),
                "#phy-cells": u32(1),
                "status": text("okay"),
            },
        )
        # Replace the temporary self-reference with the actual child table.
        tree.nodes[path].pop("operating-points-v2")
        tree.nodes[path + "/opp-table"] = {
            "compatible": text("operating-points-v2"),
        }
        tree.nodes[path + "/opp-table/opp-300000000"] = {
            "opp-hz": u32(0, 300000000),
            "required-opps": u32(rpmhpd_low_svs_d1, rpmhpd_low_svs_d1),
        }
        tree.nodes[path + "/opp-table/opp-400000000"] = {
            "opp-hz": u32(0, 400000000),
            "required-opps": u32(rpmhpd_low_svs_d1, rpmhpd_low_svs_d1),
        }
        tree.nodes[path]["operating-points-v2"] = u32(
            phandle(path + "/opp-table")
        )
        tree.nodes[path + "/ports"] = {
            "#address-cells": u32(1),
            "#size-cells": u32(0),
        }
        tree.nodes[path + "/ports/port@0"] = {
            "reg": u32(0),
            "#address-cells": u32(1),
            "#size-cells": u32(0),
        }
        tree.nodes[path + "/ports/port@0/endpoint@0"] = {
            "reg": u32(0),
            "bus-type": u32(V4L2_FWNODE_BUS_TYPE_CSI2_CPHY),
            "data-lanes": u32(0, 1, 2),
        }
        tree.nodes[path + "/ports/port@1"] = {
            "reg": u32(1),
            "#address-cells": u32(1),
            "#size-cells": u32(0),
        }
        tree.nodes[path + "/ports/port@1/endpoint@0"] = {
            "reg": u32(0),
        }

    # FWNODE_GRAPH filters unconnected input endpoints. Keep unused PHY inputs
    # paired to their own outputs, then connect the selected CSIPHY1 input to
    # the S5KJN5. CAMSS discovers that sensor through the single enabled CAMSS
    # port and creates the immutable sensor->CSIPHY media link.
    for phy in PHYS:
        tree.nodes[
            phy["path"] + "/ports/port@0/endpoint@0"
        ]["remote-endpoint"] = u32(
            phandle(phy["path"] + "/ports/port@1/endpoint@0")
        )
        tree.nodes[
            phy["path"] + "/ports/port@1/endpoint@0"
        ]["remote-endpoint"] = u32(
            phandle(phy["path"] + "/ports/port@0/endpoint@0")
        )

    sensor_phy_endpoint = (
        PHYS[1]["path"] + "/ports/port@0/endpoint@0"
    )
    sensor_endpoint["remote-endpoint"] = u32(
        phandle(sensor_phy_endpoint)
    )
    tree.nodes[sensor_phy_endpoint]["remote-endpoint"] = u32(
        phandle(probe.SENSOR + "/port/endpoint")
    )

    camss_regs = []
    for address, size in CAMSS_REGS:
        camss_regs.extend((0, address, 0, size))
    camss_clocks = []
    for provider, ident in CAMSS_CLOCKS:
        camss_clocks.extend((phandle(provider), ident))
    camss_interrupts = []
    for irq in CAMSS_INTERRUPTS:
        camss_interrupts.extend((0, irq, 1))
    camss_phys = []
    for phy in PHYS:
        camss_phys.extend((phy_refs[phy["id"]], PHY_TYPE_CPHY))

    add(
        CAMSS,
        {
            "compatible": text("qcom,sm8750-camss"),
            "reg": u32(*camss_regs),
            "reg-names": text(*CAMSS_REG_NAMES),
            "clocks": u32(*camss_clocks),
            "clock-names": text(*CAMSS_CLOCK_NAMES),
            "interrupts": u32(*camss_interrupts),
            "interrupt-names": text(*CAMSS_IRQ_NAMES),
            "interconnects": u32(
                gem_noc,
                2,
                QCOM_ICC_TAG_ACTIVE_ONLY,
                config_noc,
                3,
                QCOM_ICC_TAG_ACTIVE_ONLY,
                mmss_noc,
                0,
                QCOM_ICC_TAG_ALWAYS,
                mc_virt,
                1,
                QCOM_ICC_TAG_ALWAYS,
                mmss_noc,
                3,
                QCOM_ICC_TAG_ALWAYS,
                mc_virt,
                1,
                QCOM_ICC_TAG_ALWAYS,
                mmss_noc,
                1,
                QCOM_ICC_TAG_ALWAYS,
                mc_virt,
                1,
                QCOM_ICC_TAG_ALWAYS,
            ),
            "interconnect-names": text(
                "ahb", "hf_mnoc", "sf_mnoc", "sf_icp_mnoc"
            ),
            "iommus": u32(apps_smmu, 0x1C00, 0),
            "power-domains": u32(
                camcc, 3, camcc, 4, camcc, 5, camcc, 0
            ),
            "power-domain-names": text(
                "ife0", "ife1", "ife2", "top"
            ),
            "phys": u32(*camss_phys),
            "phy-names": text(
                "csiphy0",
                "csiphy1",
                "csiphy2",
                "csiphy3",
                "csiphy4",
                "csiphy5",
            ),
            "#address-cells": u32(2),
            "#size-cells": u32(2),
            "ranges": b"",
            "status": text("okay"),
        },
    )
    tree.nodes[CAMSS + "/ports"] = {
        "#address-cells": u32(1),
        "#size-cells": u32(0),
    }
    for phy in (PHYS[1],):
        port = CAMSS + f"/ports/port@{phy['id']}"
        endpoint = port + "/endpoint@0"
        tree.nodes[port] = {
            "reg": u32(phy["id"]),
            "#address-cells": u32(1),
            "#size-cells": u32(0),
        }
        tree.nodes[endpoint] = {
            "reg": u32(0),
            "bus-type": u32(V4L2_FWNODE_BUS_TYPE_CSI2_CPHY),
            "data-lanes": u32(*(range(3) if phy["id"] == 1 else range(4))),
        }
        tree.nodes[endpoint]["remote-endpoint"] = u32(
            phandle(
                PHYS[phy["id"]]["path"]
                + "/ports/port@1/endpoint@0"
            )
        )
        tree.nodes[PHYS[phy["id"]]["path"] + "/ports/port@1/endpoint@0"][
            "remote-endpoint"
        ] = u32(phandle(endpoint))

    final = tree.to_blob()
    check = Fdt(final)
    if check.nodes != tree.nodes or check.reservations != tree.reservations:
        if set(check.nodes) != set(tree.nodes):
            missing = sorted(set(tree.nodes) - set(check.nodes))[:4]
            extra = sorted(set(check.nodes) - set(tree.nodes))[:4]
            raise ValueError(
                f"Capture tree node readback mismatch: {missing} {extra}"
            )
        for path in tree.nodes:
            if check.nodes[path] != tree.nodes[path]:
                expected = tree.nodes[path]
                actual = check.nodes[path]
                keys = sorted(set(expected) | set(actual))
                wrong = [
                    key
                    for key in keys
                    if expected.get(key) != actual.get(key)
                ][:8]
                raise ValueError(
                    f"Capture tree property readback mismatch: "
                    f"{path} {wrong}"
                )
        raise ValueError("Capture tree reservation readback mismatch")
    report = {
        "scope": "ISOLATED_S5KJN5_3TRIO_CPHY_CAPTURE_GRAPH",
        "base_sha256": hashlib.sha256(blob).hexdigest(),
        "output_sha256": hashlib.sha256(final).hexdigest(),
        "probe": probe_report,
        "camera_node": probe.SENSOR,
        "cci": probe.CCI,
        "camss": CAMSS,
        "csiphy": PHYS[1]["path"],
        "csiphy_index": 1,
        "csid_index": 1,
        "vfe_index": 1,
        "camss_ports_present": [1],
        "cphy_trios": 3,
        "phy_opp_rates_hz": [300000000, 400000000],
        "link_frequency_hz": 1400000000,
        "symbol_rate_hz": 2800000000,
        "settle_count_expected": 45,
        "resolution": [4096, 3072],
        "default_boot_tree_changed": False,
        "capture_pipeline_present": True,
        "hardware_qualified": False,
    }
    return final, report


if __name__ == "__main__":
    raise SystemExit("Import this module from the RAM boot builder")
