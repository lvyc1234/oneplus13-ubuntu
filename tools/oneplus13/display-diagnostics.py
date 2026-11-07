#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Collect bounded, read-only display state for a RAM desktop session."""
import argparse
import json
import os
from pathlib import Path
import re

MAX_TEXT = 65536
SAMPLE_BYTES = 4096
SAMPLE_COUNT = 16
MAX_FRAMEBUFFER_BYTES = 64 * 1024 * 1024
PROCESS_NAMES = {
    'gdm3', 'gdm-session-wor', 'gnome-shell', 'gnome-initial-s',
    'gnome-session-b', 'Xwayland',
}


def bounded_text(path, limit=MAX_TEXT):
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            return {'available': True, 'truncated': True}
        return {'available': True, 'value': data.decode('utf-8', errors='replace').strip(),
                'truncated': False}
    except OSError:
        return {'available': False}


def framebuffer(root):
    base = root / 'sys/class/graphics/fb0'
    result = {key: bounded_text(base / filename, 128)
              for key, filename in [('name', 'name'), ('virtual_size', 'virtual_size'),
                                    ('bits_per_pixel', 'bits_per_pixel'), ('stride', 'stride'),
                                    ('blank', 'blank')]}
    try:
        width, height = map(int, result['virtual_size']['value'].split(','))
        bpp = int(result['bits_per_pixel']['value'])
        total = width * height * ((bpp + 7) // 8)
        if not (1 <= width <= 8192 and 1 <= height <= 8192 and 1 <= bpp <= 64
                and 0 < total <= MAX_FRAMEBUFFER_BYTES):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        result['pixel_samples'] = {'available': False, 'reason': 'invalid-or-missing-mode'}
        return result

    samples = []
    try:
        with (root / 'dev/fb0').open('rb') as stream:
            for index in range(SAMPLE_COUNT):
                offset = (total - SAMPLE_BYTES) * index // (SAMPLE_COUNT - 1)
                stream.seek(offset)
                data = stream.read(SAMPLE_BYTES)
                if not data:
                    break
                samples.append({'offset': offset, 'sample_bytes': len(data),
                                'nonzero_bytes': sum(byte != 0 for byte in data)})
        result['pixel_samples'] = {'available': bool(samples), 'sample_count': len(samples),
                                   'sample_bytes_each': SAMPLE_BYTES, 'samples': samples,
                                   'pixel_values_retained': False}
    except OSError:
        result['pixel_samples'] = {'available': False, 'reason': 'framebuffer-unreadable'}
    return result


def collect(root=Path('/')):
    root = Path(root)
    connectors = {}
    for path in sorted((root / 'sys/class/drm').glob('card*-*')):
        if not re.fullmatch(r'card[0-9]+-[A-Za-z0-9_.-]{1,96}', path.name):
            continue
        connectors[path.name] = {
            'status': bounded_text(path / 'status', 128),
            'modes': bounded_text(path / 'modes', 4096),
        }

    planes = {}
    for path in sorted((root / 'sys/kernel/debug/dri').glob('*/state')):
        if not re.fullmatch(r'[0-9]{1,3}', path.parent.name):
            continue
        planes[path.parent.name] = bounded_text(path)

    processes = {}
    for path in (root / 'proc').glob('[0-9]*'):
        if not path.name.isdecimal():
            continue
        comm = bounded_text(path / 'comm', 256)
        name = comm.get('value')
        if name in PROCESS_NAMES:
            processes[name] = processes.get(name, 0) + 1

    return {
        'schema': 'oneplus13-display-diagnostics-v1',
        'read_only': True,
        'drm_connectors': connectors,
        'drm_debug_state': planes,
        'framebuffer': framebuffer(root),
        'display_process_counts': processes,
    }


def write_report(output, report):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    temporary.write_text(json.dumps(report, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temporary, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    write_report(args.output, collect(args.root))


if __name__ == '__main__':
    main()
