#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Inspect the separately compiled AA569 DTB; no device access or boot claim."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from stock import Fdt


def inspect(blob):
    tree = Fdt(blob)
    def cells(data):
        if len(data) % 4:
            raise ValueError('Unaligned DT cell list')
        return list(struct.unpack('>'+str(len(data)//4)+'I', data))
    def enabled(path):
        return all(tree.nodes.get('/'.join(path.split('/')[:i]) or '/', {}).get('status', b'okay\0')
                   != b'disabled\0' for i in range(1, len(path.split('/'))+1))
    def compatible(value, active=True):
        matches = [p for p, props in tree.nodes.items()
                   if value.encode() in props.get('compatible', b'').split(b'\0')
                   and (not active or enabled(p))]
        if len(matches) != 1:
            raise ValueError('Expected exactly one enabled '+value)
        return matches[0]
    handles = {cells(p['phandle'])[0]: path for path, p in tree.nodes.items() if 'phandle' in p}
    def ref(path, prop):
        value = cells(tree.nodes[path][prop])
        if len(value) != 1 or value[0] not in handles:
            raise ValueError('Invalid DT phandle '+prop)
        return handles[value[0]]
    if tree.nodes['/']['compatible'].split(b'\0')[:2] != [b'oneplus,dodge', b'qcom,sm8750']:
        raise ValueError('Wrong native board')
    mdss = compatible('qcom,sm8750-mdss')
    mdp = compatible('qcom,sm8750-dpu')
    dsi = compatible('qcom,sm8750-dsi-ctrl')
    phy = compatible('qcom,sm8750-dsi-phy-3nm')
    panel = compatible('oneplus,dodge-aa569')
    if 'oneplus,aa569-static-spr' not in tree.nodes[mdp] or not tree.nodes[mdss].get('resets'):
        raise ValueError('Missing explicit SPR opt-in or MDSS reset')
    if not dsi.endswith('@ae94000') or not phy.endswith('@ae95000') or not panel.startswith(dsi+'/'):
        raise ValueError('AA569 must use single DSI0')
    if any(x in tree.nodes[dsi] for x in ['qcom,dual-dsi-mode', 'qcom,sync-dual-dsi']):
        raise ValueError('Unexpected bonded DSI')
    if any(b'oneplus,dodge-s3910-lbp' in props.get('compatible', b'') for props in tree.nodes.values()):
        raise ValueError('Display-only candidate must not add touchscreen')
    for prop, name, voltage in [('vddio-supply', b'vreg_l12b_1p8\0', 1800000),
                                 ('vci-supply', b'vreg_l13b_3p0\0', 3000000)]:
        supply = tree.nodes[ref(panel, prop)]
        if (supply['regulator-name'] != name or
                cells(supply['regulator-min-microvolt']) != [voltage] or
                cells(supply['regulator-max-microvolt']) != [voltage]):
            raise ValueError('Incorrect AA569 supply '+prop)
    for path, prop, name in [(dsi, 'vdda-supply', b'vreg_l3g_1p2\0'),
                             (phy, 'vdds-supply', b'vreg_l3i_0p88\0')]:
        if tree.nodes[ref(path, prop)]['regulator-name'] != name:
            raise ValueError('Incorrect DSI/PHY supply')
    reset = cells(tree.nodes[panel]['reset-gpios'])
    vddr = cells(tree.nodes[panel]['vddr-gpios'])
    if reset[1:] != [98, 1] or vddr[1:] != [7, 0]:
        raise ValueError('Incorrect reset/VDDR polarity or GPIO')
    gpio = handles[vddr[0]]
    if cells(tree.nodes[gpio]['gpio-ranges']) != [vddr[0], 0, 0, 8]:
        raise ValueError('GPIO7 is outside controller range')
    if cells(tree.nodes[gpio.rsplit('/', 1)[0]]['reg'])[0] != 5:
        raise ValueError('VDDR must use the F PMIC SID5')
    pins = [tree.nodes[handles[x]] for x in cells(tree.nodes[panel]['pinctrl-0'])]
    te = next(p for p in pins if p.get('pins') == b'gpio86\0')
    if te['function'] != b'mdp_vsync\0' or cells(te['drive-strength']) != [2] or 'bias-pull-down' not in te:
        raise ValueError('Incorrect TE routing')
    pin = next(p for p in pins if p.get('pins') == b'gpio7\0')
    if cells(pin['power-source']) != [1] or 'output-low' not in pin or pin['function'] != b'normal\0':
        raise ValueError('Incorrect VDDR pin configuration')
    endpoint = next(path for path, props in tree.nodes.items()
                    if path.startswith(panel+'/') and 'remote-endpoint' in props)
    peer = ref(endpoint, 'remote-endpoint')
    if ref(peer, 'remote-endpoint') != endpoint or not peer.startswith(dsi+'/'):
        raise ValueError('Panel endpoint is not reciprocal')
    if cells(tree.nodes[peer]['data-lanes']) != [0, 1, 2, 3]:
        raise ValueError('Incorrect DSI lanes')
    return {'scope': 'COMPILED_NATIVE_DTB_ONLY', 'hardware_tested': False,
            'dtb_sha256': hashlib.sha256(blob).hexdigest(),
            'mdss_reset_and_static_spr_optin': True, 'single_dsi0_aa569': True,
            'supplies_gpio_te_and_endpoints_checked': True,
            'vddr_gpio7_sid5_range8': True, 'display_only_touch_absent': True,
            'private_spr_profile_in_dtb': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dtb', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--negative-controls', action='store_true')
    a = p.parse_args()
    blob = a.dtb.read_bytes()
    result = inspect(blob)
    if a.negative_controls:
        result['negative_controls_rejected'] = negative_controls(blob)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2)+'\n')
    print('Separate native DTB wiring checked offline; not hardware tested.')


def negative_controls(blob):
    original = Fdt(blob)
    find = lambda key: next(p for p, v in original.nodes.items() if key in v.get('compatible', b'').split(b'\0'))
    mdp, mdss = find(b'qcom,sm8750-dpu'), find(b'qcom,sm8750-mdss')
    dsi, panel = find(b'qcom,sm8750-dsi-ctrl'), find(b'oneplus,dodge-aa569')
    handles = {p['phandle']: path for path, p in original.nodes.items() if 'phandle' in p}
    vci = handles[original.nodes[panel]['vci-supply']]
    te = next(p for p, v in original.nodes.items() if v.get('pins') == b'gpio86\0')
    controls = [(mdp, 'oneplus,aa569-static-spr', None), (mdss, 'status', b'disabled\0'),
                (vci, 'regulator-max-microvolt', struct.pack('>I', 1800000)),
                (panel, 'reset-gpios', original.nodes[panel]['reset-gpios'][:-4]+struct.pack('>I', 0)),
                (panel, 'vddr-gpios', original.nodes[panel]['vddr-gpios'][:4]+struct.pack('>II', 6, 0)),
                (dsi, 'qcom,dual-dsi-mode', b''), (te, 'function', b'gpio\0')]
    for path, prop, value in controls:
        tree = Fdt(blob)
        if value is None:
            del tree.nodes[path][prop]
        else:
            tree.nodes[path][prop] = value
        try:
            inspect(tree.to_blob())
        except ValueError:
            continue
        raise ValueError('Native DTB negative control unexpectedly accepted: '+prop)
    return len(controls)


if __name__ == '__main__':
    main()
