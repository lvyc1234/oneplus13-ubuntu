#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Decode and round-trip the PUBLIC AA569 DSC PPS, without recomputing RC.

Linux's msm/DPU config uses physical-pixel bpp. Native422 PPS and the DPU DSC
register encode twice that rate. Keep these representations separate. This
does not program the DSI host, DSC engine or panel.
"""
import argparse
import hashlib
import json
from pathlib import Path
from panel import vendor_pps

WORDS = {'pic_height': 6, 'pic_width': 8, 'slice_height': 10, 'slice_width': 12,
         'slice_chunk_size': 14, 'initial_xmit_delay': 16, 'initial_dec_delay': 18,
         'scale_increment_interval': 22, 'scale_decrement_interval': 24,
         'nfl_bpg_offset': 28, 'slice_bpg_offset': 30, 'initial_offset': 32,
         'final_offset': 34, 'rc_model_size': 38, 'nsl_bpg_offset': 90,
         'second_line_offset_adj': 92}
BYTES = {'initial_scale_value': 21, 'first_line_bpg_offset': 27,
         'flatness_min_qp': 36, 'flatness_max_qp': 37, 'rc_edge_factor': 40,
         'rc_quant_incr_limit0': 41, 'rc_quant_incr_limit1': 42,
         'second_line_bpg_offset': 89}


def pack_pps(cfg):
    raw = bytearray(128)
    raw[0] = cfg['dsc_version_major'] << 4 | cfg['dsc_version_minor']
    raw[3] = cfg['bits_per_component'] << 4 | cfg['line_buf_depth']
    wire_bpp = cfg['bits_per_pixel'] * (2 if cfg['native_422'] or cfg['native_420'] else 1)
    if not 0 < wire_bpp < 1024:
        raise ValueError('Unrepresentable PPS group rate')
    raw[4] = ((wire_bpp >> 8) | cfg['vbr_enable'] << 2 | cfg['simple_422'] << 3 |
              cfg['convert_rgb'] << 4 | cfg['block_pred_enable'] << 5)
    raw[5] = wire_bpp & 255
    for name, offset in WORDS.items():
        raw[offset:offset+2] = cfg[name].to_bytes(2, 'big')
    for name, offset in BYTES.items():
        raw[offset] = cfg[name]
    raw[43] = cfg['rc_tgt_offset_high'] << 4 | cfg['rc_tgt_offset_low']
    raw[44:58] = bytes(cfg['rc_buf_thresh'])
    for i, entry in enumerate(cfg['rc_range_params']):
        word = entry['range_min_qp'] << 11 | entry['range_max_qp'] << 6 | entry['range_bpg_offset']
        raw[58+2*i:60+2*i] = word.to_bytes(2, 'big')
    raw[88] = cfg['native_422'] | cfg['native_420'] << 1
    return bytes(raw)


def decode_pps(raw):
    if len(raw) not in (94, 128) or any(raw[94:]):
        raise ValueError('PPS must have 94 meaningful bytes with optional zero padding')
    wire_bpp = (raw[4] & 3) << 8 | raw[5]
    native_422, native_420 = bool(raw[88] & 1), bool(raw[88] & 2)
    if native_422 or native_420:
        if wire_bpp & 1:
            raise ValueError('Nonintegral Q4 physical rate')
        physical_bpp = wire_bpp // 2
    else:
        physical_bpp = wire_bpp
    cfg = {'dsc_version_major': raw[0] >> 4, 'dsc_version_minor': raw[0] & 15,
           'bits_per_component': raw[3] >> 4, 'line_buf_depth': raw[3] & 15,
           'bits_per_pixel': physical_bpp, 'vbr_enable': bool(raw[4] & 4),
           'simple_422': bool(raw[4] & 8), 'convert_rgb': bool(raw[4] & 16),
           'block_pred_enable': bool(raw[4] & 32),
           'native_422': native_422, 'native_420': native_420,
           **{name: int.from_bytes(raw[offset:offset+2], 'big') for name, offset in WORDS.items()},
           **{name: raw[offset] for name, offset in BYTES.items()},
           'rc_tgt_offset_high': raw[43] >> 4, 'rc_tgt_offset_low': raw[43] & 15,
           'rc_buf_thresh': list(raw[44:58]), 'rc_range_params': []}
    for i in range(15):
        word = int.from_bytes(raw[58+2*i:60+2*i], 'big')
        cfg['rc_range_params'].append({'range_min_qp': word >> 11,
                                      'range_max_qp': word >> 6 & 31,
                                      'range_bpg_offset': word & 63})
    if not cfg['slice_width'] or not cfg['slice_height']:
        raise ValueError('Zero DSC slice geometry')
    cfg['slice_count'], remainder = divmod(cfg['pic_width'], cfg['slice_width'])
    if remainder or not cfg['slice_count'] or cfg['pic_height'] % cfg['slice_height']:
        raise ValueError('PPS geometry does not tile whole slices')
    if pack_pps(cfg) != raw.ljust(128, b'\0'):
        raise ValueError('PPS reserved bits, identifier or unsupported fields would be lost')
    # This path intentionally accepts one investigated encoding/board geometry.
    expected = {'dsc_version_major': 1, 'dsc_version_minor': 2, 'bits_per_component': 10,
                'line_buf_depth': 11, 'bits_per_pixel': 128, 'native_422': True,
                'native_420': False, 'convert_rgb': False, 'simple_422': False,
                'vbr_enable': False, 'block_pred_enable': True, 'pic_width': 1440,
                'pic_height': 3168, 'slice_width': 720, 'slice_height': 22,
                'slice_count': 2, 'slice_chunk_size': 720, 'rc_model_size': 8192,
                'rc_edge_factor': 6, 'rc_tgt_offset_high': 3, 'rc_tgt_offset_low': 3}
    if any(cfg[k] != v for k, v in expected.items()):
        raise ValueError('Uninvestigated DSC encoding/geometry; refuse a silent fallback')
    if cfg['initial_xmit_delay'] > 1023 or cfg['scale_decrement_interval'] > 4095:
        raise ValueError('DSC delay/scale reserved bits are set')
    for name in ('initial_scale_value', 'first_line_bpg_offset', 'flatness_min_qp',
                 'flatness_max_qp', 'rc_quant_incr_limit0', 'rc_quant_incr_limit1',
                 'second_line_bpg_offset'):
        limit = 63 if name == 'initial_scale_value' else 31
        if cfg[name] > limit:
            raise ValueError('DSC field exceeds register width: '+name)
    if (cfg['rc_buf_thresh'] != sorted(set(cfg['rc_buf_thresh'])) or
            not 0 < cfg['initial_offset'] < cfg['rc_model_size'] or
            not 0 < cfg['final_offset'] < cfg['rc_model_size'] or
            any(r['range_min_qp'] > r['range_max_qp'] for r in cfg['rc_range_params'])):
        raise ValueError('Invalid DSC rate-control bounds')
    return cfg


def c_initializer(cfg):
    lines = ['/* Public AA569 PPS; physical-pixel Q4 bpp=128. Not a working panel driver. */',
             'static const struct drm_dsc_config aa569_dsc = {']
    for key, value in cfg.items():
        if key == 'rc_range_params':
            lines.append('\t.rc_range_params = {')
            lines.extend('\t\t{ '+', '.join(f'.{k} = {v}' for k, v in entry.items())+' },'
                         for entry in value)
            lines.append('\t},')
        elif isinstance(value, list):
            lines.append(f'\t.{key} = {{ '+', '.join(map(str, value))+' },')
        else:
            text = str(value).lower() if isinstance(value, bool) else str(value)
            lines.append(f'\t.{key} = {text},')
    return '\n'.join(lines+['};', ''])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--panel-json', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--c-output', type=Path)
    a = p.parse_args()
    fixture = json.loads(a.panel_json.read_text(encoding='utf-8'))
    raw = vendor_pps(fixture['on_packets'])
    cfg = decode_pps(raw)
    report = {'scope': 'OFFLINE_PPS_CONVERSION_ONLY', 'hardware_tested': False,
              'vendor_pps_sha256': hashlib.sha256(raw).hexdigest(),
              'physical_bpp_q4': cfg['bits_per_pixel'],
              'pps_group_bpp_q4': cfg['bits_per_pixel']*2,
              'dpu_dsc_main_conf_bpp_field_q4': cfg['bits_per_pixel']*2,
              'roundtrip_all_94_bytes': True, 'drm_dsc_config': cfg}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    if a.c_output:
        a.c_output.write_text(c_initializer(cfg), encoding='utf-8')
    print('Checked all 94 public PPS bytes; physical 8 bpp / encoded 16 bpp group.')


if __name__ == '__main__':
    main()
