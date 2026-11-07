#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Mechanically decode the public AA569 120 Hz init sequence, offline only.

Optionally prove that the public bytes match the owner's runtime FDT. Output
contains only public-source commands, never calibration, panel serials or IDs.
This is porting evidence, not a native panel driver or display acceptance test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from stock import Fdt

PANEL = '/soc/qcom,mdss_mdp@ae00000/qcom,mdss_dsi_panel_AA569_P_3_A0019_dsc_cmd'
TIMING = PANEL + '/qcom,mdss-dsi-display-timings/timing@wqhd_sdc_120'
PIN = 'd50b305f7da9e14715a25120a4ac7b1a4b8b97c3'


def strip_comments(text):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', text, flags=re.S))


def byte_property(text, name):
    value = re.search(re.escape(name) + r'\s*=\s*\[([^\]]*)\]', strip_comments(text))
    if not value:
        raise ValueError('Missing public panel property: ' + name)
    tokens = value[1].split()
    if any(not re.fullmatch('[0-9a-fA-F]{2}', token) for token in tokens):
        raise ValueError('Non-byte token in panel sequence')
    return bytes(int(token, 16) for token in tokens)


def command_packets(blob):
    packets, offset = [], 0
    while offset < len(blob):
        if len(blob) - offset < 7:
            raise ValueError('Truncated Qcom DSI command header')
        dtype, last, channel, flags, delay, high, low = blob[offset:offset+7]
        length = high << 8 | low
        offset += 7
        if not length or offset + length > len(blob):
            raise ValueError('Truncated or empty Qcom DSI payload')
        if dtype not in (0x05, 0x15, 0x39) or channel > 3 or last > 1:
            raise ValueError('Unsupported DSI init packet type/channel')
        if (dtype == 0x05 and length != 1) or (dtype == 0x15 and length != 2):
            raise ValueError('Invalid DCS short packet length')
        packets.append({'type': dtype, 'last': last, 'channel': channel,
                        'vendor_flags': flags, 'delay_ms': delay,
                        'payload': blob[offset:offset+length].hex()})
        offset += length
    return packets


def vendor_pps(packets):
    # The 0x81 vendor register carries five prefix bytes followed by a DSC PPS.
    # Later page-local 0x81 writes are unrelated; require the actual DSC version.
    candidates = [bytes.fromhex(p['payload'])[6:] for p in packets
                  if len(bytes.fromhex(p['payload'])) >= 100
                  and bytes.fromhex(p['payload'])[0] == 0x81
                  and bytes.fromhex(p['payload'])[6] == 0x12]
    if len(candidates) != 1:
        raise ValueError('Missing or ambiguous vendor DSC PPS')
    return candidates[0]


def pps_summary(packets):
    pps = vendor_pps(packets)
    word = lambda off: int.from_bytes(pps[off:off+2], 'big')
    if len(pps) < 89:
        raise ValueError('Vendor PPS lacks native chroma flags')
    return {'version': f'{pps[0] >> 4}.{pps[0] & 15}',
            'bits_per_component': pps[3] >> 4,
            'line_buffer_depth': pps[3] & 15,
            'convert_rgb': bool(pps[4] & 0x10),
            'block_prediction': bool(pps[4] & 0x20),
            'encoded_bits_per_pixel_x16': ((pps[4] & 3) << 8) | pps[5],
            'height': word(6), 'width': word(8),
            'slice_height': word(10), 'slice_width': word(12),
            'chunk_bytes': word(14), 'native_422': bool(pps[88] & 1),
            'native_420': bool(pps[88] & 2),
            'vendor_pps_bytes': len(pps), 'vendor_pps_sha256': hashlib.sha256(pps).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel-source-dir', required=True, type=Path)
    parser.add_argument('--runtime-fdt', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    path = args.panel_source_dir / 'dsi-panel-AA569-P-3-A0019-dsc-cmd.dtsi'
    common = args.panel_source_dir / 'dsi-panel-AA569-P-3-A0019-common.dtsi'
    data, off_data = byte_property(path.read_text(), 'qcom,mdss-dsi-on-command'), byte_property(
        common.read_text(), 'qcom,mdss-dsi-off-command')
    packets = command_packets(data)
    report = {'scope': 'Public-source mechanical extraction; not a native display driver',
              'source_repository': 'OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750',
              'source_commit': PIN, 'mode': 'AA569 production WQHD SDC 120 Hz',
              'hardware_tested': False,
              'on_bytes': len(data), 'on_sha256': hashlib.sha256(data).hexdigest(),
              'off_sha256': hashlib.sha256(off_data).hexdigest(),
              'on_packets': packets, 'off_packets': command_packets(off_data),
              'dsc': pps_summary(packets),
              'remaining_requirements': ['DPU SPR/PenTile processing and its configuration',
                  'native 422 rate-control/PPS and DSI lane-rate consistency',
                  'two DSC encoders / two mixers on one DSI interface',
                  'PM8550VE_F GPIO7 range and regulator sequencing',
                  'real panel takeover and repeated power-cycle verification']}
    if args.runtime_fdt:
        runtime = Fdt(args.runtime_fdt.read_bytes())
        actual = runtime.nodes[TIMING]
        if actual['qcom,mdss-dsi-on-command'] != data or actual['qcom,mdss-dsi-off-command'] != off_data:
            raise ValueError('Public commands differ from the selected runtime panel; do not use this recipe')
        report['runtime_command_bytes_match'] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Checked {len(packets)} on-packets; commands are evidence, not a working panel driver.')


if __name__ == '__main__':
    main()
