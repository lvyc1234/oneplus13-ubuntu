#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline AA569 SPR2 semantic conversion. No device access or register writes.

The XML remains private. This handles the observed PenTile/2DAvg/YYGM profile
only. The generated payload/relative register plan is not live register state
and does not implement DPU resource ownership, clocks, flush or REGDMA.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zlib

SOURCE_PIN = 'd50b305f7da9e14715a25120a4ac7b1a4b8b97c3'
TAGS = {'AdaptiveMode', 'FilterType', 'FilterCoeffs', 'PhaseInc', 'PhaseRepeat',
        'ColorPhaseOffsetsRed', 'ColorPhaseOffsetsGreen', 'ColorPhaseOffsetsBlue',
        'ColorPhaseOffsetsWhite', 'AdaptiveFilterCoeffs', 'FilterWidth',
        'SharpeningStrength', 'Pba'}


def numbers(element, tag, count, low, high):
    matches = element.findall(tag)
    if len(matches) != 1 or matches[0].text is None or list(matches[0]):
        raise ValueError('Missing, duplicate or nested SPR field: ' + tag)
    try:
        values = [int(x.strip(), 10) for x in matches[0].text.split(',')]
    except ValueError as error:
        raise ValueError('Invalid decimal SPR field: ' + tag) from error
    if len(values) != count or any(not low <= v <= high for v in values):
        raise ValueError('SPR field size/range: ' + tag)
    return values


def prepare_mode(mode):
    if {x.tag for x in mode} != TAGS or len(mode) != len(TAGS):
        raise ValueError('Unexpected/duplicate SPR mode fields')
    if mode.findtext('FilterType') != '2DAvg' or mode.findtext('AdaptiveMode') != 'YYGM':
        raise ValueError('Only the investigated 2DAvg/YYGM profile is supported')
    # These legacy tags have no setter in the inspected Android parser. They
    # MUST NOT replace its FilterType/AdaptiveMode enum fields (4 and 0).
    for tag in ('FilterWidth', 'SharpeningStrength'):
        if numbers(mode, tag, 1, 0, 0) != [0]:
            raise ValueError('Uninvestigated legacy SPR field: ' + tag)
    cfg = {'flags': 0, **{f'cfg{i}': 1 for i in range(4)}, 'cfg4': 0,
           'cfg5': numbers(mode, 'PhaseInc', 1, 0, 15)[0],
           'cfg6': numbers(mode, 'PhaseRepeat', 1, 0, 7)[0],
           'cfg7': 4, 'cfg8': 0, 'cfg9': 0, 'cfg10': 0,
           'cfg11': [1, 0, 1, 0], 'cfg12': [0]*4,
           'cfg13': [341, 341, 341, 0],
           'cfg14': numbers(mode, 'AdaptiveFilterCoeffs', 5, 0, 31),
           # Default identity OPR curve in the inspected parser. The endpoint
           # saturates at 4095; this is not an inferred panel calibration LUT.
           'cfg15': [min(4095, (i*8192+31)//62) for i in range(32)],
           'cfg16': numbers(mode, 'FilterCoeffs', 16, -1024, 1023),
           'cfg17': []}
    for color in ('Red', 'Green', 'Blue', 'White'):
        cfg['cfg17'] += numbers(mode, 'ColorPhaseOffsets'+color, 6, -16, 15)
    # The fourth colour's entries 2..5 are discarded by the vendor's final
    # cfg17 register mask (0x3ff). Reject rather than silently discard data.
    if any(cfg['cfg17'][20:]):
        raise ValueError('Unrepresentable fourth-colour phase offsets')
    pba = numbers(mode, 'Pba', 7, 0, 7)
    if any(v > 3 for v in pba[3:]):
        raise ValueError('PBA polarity entries must be in 0..3')
    cfg['cfg18_en'] = int(any(pba))
    cfg['cfg18'] = pba[:3] + [{0: 0, 1: 2, 2: 4, 3: 0}[v] for v in pba[3:]]
    return cfg


def parse_xml(blob):
    if len(blob) > 65536 or b'<!' in blob:
        raise ValueError('SPR XML too large or contains a declaration/entity')
    root = ET.fromstring(blob)
    if root.tag != 'Calib_Data' or len(root) != 1 or root[0].tag != 'SprConfig':
        raise ValueError('Unexpected SPR XML root')
    spr = root[0]
    count, default = int(spr.attrib['NumModes']), int(spr.attrib['DefaultMode'])
    if not 1 <= count <= 16 or len(spr) != count or any(m.tag != 'Mode' for m in spr):
        raise ValueError('SPR mode count mismatch')
    modes = {}
    for mode in spr:
        index = int(mode.attrib['ModeID'])
        if index < 0 or index in modes:
            raise ValueError('Duplicate/negative SPR mode identifier')
        modes[index] = prepare_mode(mode)
    if default not in modes:
        raise ValueError('SPR default mode is absent')
    return default, modes


def register_plan(cfg, mixers=2):
    """Mirror vendor SPR2 setup at relative offsets, including transport order.

    No undocumented absolute address or write to cfg14/0x54 is invented. The
    pinned vendor v2 common path does not program cfg14 for this filter mode.
    Preserve bit25 (UDC) in the configuration register, as REG_SINGLE_MODIFY
    does. Actual safe acquisition/disable of an inherited UDC owner is pending.
    """
    if mixers != 2:
        raise ValueError('Only the investigated two-mixer topology is supported')
    operations = []
    def add(offset, values, transport='regdma', preserve_mask=0):
        operations.append({'relative_offset': offset, 'values': values,
                           'transport': transport, 'preserve_mask': preserve_mask})
    if cfg['cfg18_en']:
        p = cfg['cfg18']
        pba = p[0] << 16 | p[1] << 20 | p[2] << 24
        for i, v in enumerate(p[3:]):
            pba |= {0: 0, 2: 1, 4: 2}[v] << (i*4)
        add(0x7c, [pba], 'direct-before-regdma-kickoff')
    phase = [sum((cfg['cfg17'][i+j] & 31) << (5*j) for j in range(6))
             for i in range(0, 24, 6)]
    phase[-1] &= 0x3ff
    add(0x24, phase)
    add(0x34, [(cfg['cfg16'][i] & 0x7ff) | ((cfg['cfg16'][i+1] & 0x7ff) << 16)
               for i in range(0, 16, 2)])
    add(0x70, [sum((cfg['cfg13'][i] & 0x3ff) << (10*i) for i in range(3)),
               cfg['cfg10'] & 0x3fffffff])
    add(0xa0, [cfg['cfg15'][i] | cfg['cfg15'][i+1] << 16 for i in range(0, 32, 2)])
    opmode = (cfg['cfg0'] | cfg['cfg1'] << 1 | cfg['cfg2'] << 2 |
              cfg['cfg3'] << 24 | cfg['cfg5'] << 16 | cfg['cfg6'] << 20 |
              (cfg['cfg7'] & 3) << 4 | cfg['cfg8'] << 6 |
              sum(cfg['cfg11'][i] << (8+2*i) for i in range(4)) |
              (bool(cfg['cfg7'] & 4) << 15) | cfg['cfg18_en'] << 26)
    add(0x04, [opmode], preserve_mask=1 << 25)
    add(0x08, [1])
    # Full-frame partial-update origin; separately owned SPR_PU_CFG in vendor.
    add(0x20, [0], 'separate-pu-regdma')
    return operations


def encode_profile(blob):
    """Private v1 planner input; no arbitrary offsets or executable firmware."""
    default, modes = parse_xml(blob)
    if any(cfg != modes[default] for cfg in modes.values()):
        raise ValueError('A fixed 120Hz profile requires identical stock modes')
    values = {}
    for op in register_plan(modes[default]):
        for i, word in enumerate(op['values']):
            values[op['relative_offset'] + i*4] = word
    offsets = [*range(0x24, 0x34, 4), *range(0x34, 0x54, 4),
               0x70, 0x74, *range(0xa0, 0xe0, 4), 0x7c, 4, 8, 0x20]
    words = [values.get(off, 0) if off == 0x7c else values[off] for off in offsets]
    payload = struct.pack('<34I', *words)
    return b'AA569S2\0' + struct.pack('<8I', 1, 1440, 3168, 120, 34,
                                    zlib.crc32(payload), 0, 0) + payload


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--xml', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--oracle', type=Path, help='Private optional host-emulated payload for comparison')
    p.add_argument('--firmware-output', type=Path,
                   help='Private host-only planner input; never upload to Git/Actions')
    a = p.parse_args()
    blob = a.xml.read_bytes()
    default, modes = parse_xml(blob)
    if a.oracle:
        expected = json.loads(a.oracle.read_text(encoding='utf-8'))['cfg']
        if modes[default] != expected:
            raise ValueError('Semantic conversion differs from host ARM64 oracle')
    report = {'scope': 'OFFLINE_CONVERSION_ONLY', 'hardware_tested': False,
              'source_commit': SOURCE_PIN, 'xml_sha256': hashlib.sha256(blob).hexdigest(),
              'default_mode': default,
              'oracle_payload_match': bool(a.oracle),
              'modes': {i: {'cfg': cfg, 'relative_register_plan': register_plan(cfg)}
                        for i, cfg in modes.items()},
              'remaining': ['independent live register comparison', 'DPU/REGDMA lifecycle and UDC ownership',
                            'native422 DSC and panel integration', 'physical display acceptance']}
    firmware = encode_profile(blob) if a.firmware_output else None
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    if a.firmware_output:
        a.firmware_output.parent.mkdir(parents=True, exist_ok=True)
        a.firmware_output.write_bytes(firmware)
    print(f'Checked {len(modes)} private SPR modes; no device/register access.')


if __name__ == '__main__':
    main()
