#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate an AA569 component header from the pinned PUBLIC command fixture."""
import argparse
import hashlib
import json
from pathlib import Path
from dsc import decode_pps, c_initializer
from panel import PIN, vendor_pps


def generate(fixture):
    if fixture.get('source_commit') != PIN:
        raise ValueError('Unexpected public panel source revision')
    lines = ['/* SPDX-License-Identifier: GPL-2.0-only */',
             '/* Generated public OnePlusOSS '+PIN+' AA569 data. */',
             '/* No private XML, identifiers or factory calibration. */',
             '#ifndef DODGE_AA569_DATA_H', '#define DODGE_AA569_DATA_H',
             'struct aa569_cmd { u16 offset, length; u8 delay_ms, type; bool batch; };']
    for name in ('on', 'off'):
        raw, commands, flattened = bytearray(), [], bytearray()
        for packet in fixture[name+'_packets']:
            data = bytes.fromhex(packet['payload'])
            expected = 5 if len(data) == 1 else 21 if len(data) == 2 else 57
            # mipi_dsi_dcs_write_buffer chooses exactly this wire packet type.
            if (packet['type'] != expected or packet['channel'] != 0 or
                    packet['vendor_flags'] not in (0, 64) or packet['last'] != 0 or
                    not 0 <= packet['delay_ms'] <= 255 or not 1 <= len(data) <= 512):
                raise ValueError('Uninvestigated packet type/channel/flags/length')
            raw.extend(bytes([expected, 0, 0, packet['vendor_flags'], packet['delay_ms'],
                              len(data) >> 8, len(data) & 255])+data)
            batch = packet['vendor_flags'] == 64
            if batch and packet['delay_ms']:
                raise ValueError('Delay before batch completion')
            commands.append((len(flattened), len(data), packet['delay_ms'], expected, batch))
            flattened.extend(data)
        if commands[-1][-1]:
            raise ValueError('Unterminated command group')
        if hashlib.sha256(raw).hexdigest() != fixture[name+'_sha256']:
            raise ValueError('Public command fixture digest mismatch')
        lines.append('static const u8 aa569_'+name+'_bytes[] = {')
        for i in range(0,len(flattened),16):
            lines.append('\t'+', '.join(f'0x{x:02x}' for x in flattened[i:i+16])+',')
        lines += ['};', 'static const struct aa569_cmd aa569_'+name+'[] = {']
        lines.extend(f'\t{{ {o}, {n}, {d}, {t}, {str(b).lower()} }},' for o,n,d,t,b in commands)
        lines.append('};')
    if fixture['on_packets'][-1]['payload'] != '29':
        raise ValueError('Display-on is not the last init packet')
    lines.append(c_initializer(decode_pps(vendor_pps(fixture['on_packets']))))
    lines += ['#endif', '']
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    fixture = json.loads((repo/'devices/oneplus13/panel-aa569-120hz.json').read_text())
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(generate(fixture), encoding='utf-8', newline='\n')
    print('Generated public AA569 packet/DSC component header.')


if __name__ == '__main__':
    main()
