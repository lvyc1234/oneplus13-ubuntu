#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check isolated WLAN wiring, with explicit unresolved firmware/PHY evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from stock import Fdt, cells


def inspect(blob):
    tree = Fdt(blob)
    def enabled(path):
        return all(tree.nodes.get('/'.join(path.split('/')[:i]) or '/', {}).get('status', b'okay\0')
                   != b'disabled\0' for i in range(1, len(path.split('/'))+1))
    def find(compatible):
        paths = [p for p, v in tree.nodes.items() if compatible.encode() in
                 v.get('compatible', b'').split(b'\0') and enabled(p)]
        if len(paths) != 1:
            raise ValueError('Expected one active '+compatible)
        return paths[0]
    def ref(path, prop):
        values = cells(tree.nodes[path][prop])
        if len(values) != 1 or values[0] not in tree.phandles:
            raise ValueError('Invalid supply reference')
        return tree.phandles[values[0]]
    if tree.nodes['/']['compatible'].split(b'\0')[:2] != [b'oneplus,dodge', b'qcom,sm8750']:
        raise ValueError('Wrong board')
    pmu, pcie, phy, wifi = (find(c) for c in ['qcom,wcn7860-pmu', 'qcom,pcie-sm8750',
                                            'qcom,sm8750-qmp-gen3x2-pcie-phy', 'pci17cb,110e'])
    if not pcie.endswith('@1c00000') or not phy.endswith('@1c06000') or not wifi.startswith(pcie+'/'):
        raise ValueError('Wrong PCIe0/PHY/device hierarchy')
    for path, props in tree.nodes.items():
        if enabled(path) and any(c in props.get('compatible', b'').split(b'\0') for c in
                [b'qcom,sm8750-mdss', b'oneplus,dodge-aa569', b'oneplus,dodge-s3910-lbp', b'qcom,wcn7860-bt']):
            raise ValueError('WLAN-only tree must not enable display/touch/Bluetooth')
        if any(k in props for k in ['local-mac-address', 'mac-address', 'nvmem-cell-names', 'firmware-name']):
            if path.startswith(pmu) or path.startswith(wifi):
                raise ValueError('No fabricated address/firmware/calibration selection')
    supplies = [('vddio', 'vreg_l3f_1p8', 1800000, 1800000),
                ('vddio1p2', 'vreg_l2f_1p2', 1200000, 1200000),
                ('vddaon', 'vreg_s4d_0p85', 876000, 1036000),
                ('vdddig', 'vreg_s5f_0p5', 876000, 1000000),
                ('vddrfa1p2', 'vreg_s7i_1p2', 1312000, 1340000),
                ('vddrfa1p8', 'vreg_s3g_1p8', 1860000, 2000000)]
    for prop, name, low, high in supplies:
        v = tree.nodes[ref(pmu, prop+'-supply')]
        if (v['regulator-name'] != name.encode()+b'\0' or cells(v['regulator-min-microvolt']) != [low]
                or cells(v['regulator-max-microvolt']) != [high]):
            raise ValueError('Phone WLAN regulator constraints: '+prop)
        if prop in ['vddio', 'vddio1p2'] and (cells(v['regulator-system-load']) != [30000]
                or cells(v['regulator-initial-mode']) != [3]):  # RPMH_REGULATOR_MODE_HPM
            raise ValueError('Phone IO load/HPM constraint')
    wlan = cells(tree.nodes[pmu]['wlan-enable-gpios'])
    bt = cells(tree.nodes[pmu]['bt-enable-gpios'])
    tlmm, pmic = tree.phandles[wlan[0]], tree.phandles[bt[0]]
    if (wlan[1:] != [16, 0] or bt[1:] != [3, 0] or b'qcom,sm8750-tlmm' not in
            tree.nodes[tlmm].get('compatible', b'').split(b'\0')):
        raise ValueError('Wrong WLAN/BT enable')
    if cells(tree.nodes[pmic.rsplit('/', 1)[0]]['reg'])[0] != 5:
        raise ValueError('BT enable must be on F PMIC SID5')
    pins = [tree.nodes[tree.phandles[h]] for h in cells(tree.nodes[pmu]['pinctrl-0'])]
    wp = next(p for p in pins if p.get('pins') == b'gpio16\0')
    bp = next(p for p in pins if p.get('pins') == b'gpio3\0')
    if (wp['function'] != b'gpio\0' or cells(wp['drive-strength']) != [16]
            or 'bias-pull-up' not in wp or 'output-high' in wp):
        raise ValueError('WLAN electrical settings or premature pinctrl enable')
    if bp['function'] != b'normal\0' or cells(bp['power-source']) != [1] or 'bias-disable' not in bp:
        raise ValueError('BT/PMU enable pin settings')
    port = tree.nodes[wifi.rsplit('/', 1)[0]]
    if cells(port['reset-gpios']) != [wlan[0], 102, 1] or cells(port['wake-gpios']) != [wlan[0], 104, 0]:
        raise ValueError('Wrong PCIe reset/wake routing')
    clock = cells(tree.nodes[pmu]['clocks'])
    if (len(clock) != 2 or clock[1] != 6 or b'qcom,sm8750-rpmh-clk' not in
            tree.nodes[tree.phandles[clock[0]]].get('compatible', b'').split(b'\0')):
        raise ValueError('Reference RF_CLK1 graph changed')
    pci_pins = ref(pcie, 'pinctrl-0')
    for pin, function, bias in [('gpio102', 'gpio', 'bias-pull-down'),
                                ('gpio103', 'pcie0_clk_req_n', 'bias-pull-up'),
                                ('gpio104', 'gpio', 'bias-pull-up')]:
        matches = [v for p, v in tree.nodes.items() if p.startswith(pci_pins+'/')
                   and v.get('pins') == pin.encode()+b'\0']
        if (len(matches) != 1 or matches[0]['function'] != function.encode()+b'\0'
                or cells(matches[0]['drive-strength']) != [2] or bias not in matches[0]):
            raise ValueError('Incorrect PCIe electrical settings')
    if not tree.nodes[pcie].get('iommu-map') or b'iommu.passthrough' in tree.nodes['/chosen'].get('bootargs', b''):
        raise ValueError('Normal PCIe IOMMU mapping is required')
    outputs = ['rfa_cmn', 'aon_0p59', 'wlcx_0p8', 'wlmx_0p85', 'rfa_0p8', 'rfa_1p2', 'rfa_1p8', 'pcie_0p9', 'pcie_1p8']
    for prop, name in zip(['vddrfacmn', 'vddaon', 'vddwlcx', 'vddwlmx', 'vddrfa0p8',
                           'vddrfa1p2', 'vddrfa1p8', 'vddpcie0p9', 'vddpcie1p8'], outputs):
        target = ref(wifi, prop+'-supply')
        if not target.startswith(pmu+'/regulators/') or tree.nodes[target]['regulator-name'] != ('vreg_pmu_'+name).encode()+b'\0':
            raise ValueError('Incorrect WCN PMU output graph')
    for prop, name in [('vdda-phy-supply', b'vreg_l1f_0p88\0'), ('vdda-pll-supply', b'vreg_l3g_1p2\0')]:
        if tree.nodes[ref(phy, prop)]['regulator-name'] != name:
            raise ValueError('Reference PHY supply changed')
    return {'scope': 'COMPILED_WLAN_WIRING_ONLY', 'hardware_tested': False,
            'dtb_sha256': hashlib.sha256(blob).hexdigest(), 'phone_six_rail_constraints': True,
            'enable_pmic_gpio_and_pcie0_checked': True, 'pmu_output_graph_checked': True,
            'display_touch_bluetooth_absent': True, 'wlan_pin16_16ma_pullup': True,
            'firmware_board_revision_sar_verified': False, 'phy_rails_and_rf_clock_reference_only': True,
            'contains_owner_address_or_calibration': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dtb', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    blob = a.dtb.read_bytes()
    report = inspect(blob)
    config = set(a.config.read_text().splitlines())
    names = ['ATH12K', 'PCI_PWRCTRL_PWRSEQ', 'POWER_SEQUENCING_QCOM_WCN']
    if any('CONFIG_'+name+'=m' not in config for name in names):
        raise ValueError('WLAN RF stack must remain modular')
    original = Fdt(blob)
    pmu = next(p for p, v in original.nodes.items() if v.get('compatible') == b'qcom,wcn7860-pmu\0')
    wifi = next(p for p, v in original.nodes.items() if v.get('compatible') == b'pci17cb,110e\0')
    dig = original.phandles[cells(original.nodes[pmu]['vdddig-supply'])[0]]
    io = original.phandles[cells(original.nodes[pmu]['vddio-supply'])[0]]
    pin = next(original.phandles[h] for h in cells(original.nodes[pmu]['pinctrl-0'])
               if original.nodes[original.phandles[h]].get('pins') == b'gpio16\0')
    port = wifi.rsplit('/', 1)[0]
    controls = [(dig, 'regulator-min-microvolt', struct.pack('>I', 852000)),
                (wifi, 'compatible', b'pci17cb,1107\0'),
                (pmu, 'wlan-enable-gpios', original.nodes[pmu]['wlan-enable-gpios'][:4]+struct.pack('>II', 17, 0)),
                (port, 'reset-gpios', original.nodes[port]['reset-gpios'][:-4]+struct.pack('>I', 0)),
                (io, 'regulator-system-load', struct.pack('>I', 0)),
                (pin, 'drive-strength', struct.pack('>I', 8))]
    for path, prop, value in controls:
        tree = Fdt(blob)
        tree.nodes[path][prop] = value
        try:
            inspect(tree.to_blob())
        except ValueError:
            continue
        raise ValueError('WLAN negative control accepted: '+prop)
    report.update(negative_controls_rejected=len(controls), rf_stack_modular=True)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2)+'\n')
    print('WLAN wiring candidate checked; firmware/revision/SAR and hardware remain unverified.')


if __name__ == '__main__':
    main()
