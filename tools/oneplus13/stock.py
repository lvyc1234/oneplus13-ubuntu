#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline stock image/FDT readers. No ADB, device writes or flash operations.

Original evidence and the detailed JSON contain private data: keep them out of
Git. Readers reject truncated sections rather than guessing boot geometry.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def align(value, page):
    if not page or page & (page - 1):
        raise ValueError('Alignment must be a positive power of two')
    return (value + page - 1) & -page


def bounded(blob, offset, size):
    if offset < 0 or size < 0 or offset + size > len(blob):
        raise ValueError('Image section is truncated or out of bounds')
    return blob[offset:offset + size]


def cells(value):
    if len(value) % 4:
        raise ValueError('FDT cells are not 32-bit aligned')
    return list(struct.unpack('>' + 'I' * (len(value) // 4), value))


def strings(value):
    if not value or value[-1] != 0:
        raise ValueError('FDT string is not NUL terminated')
    return value[:-1].decode('utf-8').split('\0')


class Fdt:
    def __init__(self, blob):
        header = struct.unpack('>10I', bounded(blob, 0, 40))
        magic, total, off, str_off, reserve_off, version, compatible, _, str_size, size = header
        if magic != 0xd00dfeed or version < 17 or compatible > 17 or total < 40:
            raise ValueError('Unsupported FDT header')
        self.blob = bounded(blob, 0, total)
        self.nodes = {}
        self.phandles = {}
        names = bounded(self.blob, str_off, str_size)
        structure = bounded(self.blob, off, size)
        pos, stack, ended = 0, [], False
        while pos < len(structure):
            token, = struct.unpack('>I', bounded(structure, pos, 4))
            pos += 4
            if token == 1:
                stop = structure.find(b'\0', pos)
                if stop < 0:
                    raise ValueError('Unterminated FDT node')
                name = structure[pos:stop].decode('ascii')
                if '/' in name or (not stack and (name or self.nodes)):
                    raise ValueError('Invalid or duplicate FDT root')
                path = (stack[-1].rstrip('/') + '/' + name) if stack else '/'
                if path in self.nodes:
                    raise ValueError('Duplicate FDT node')
                stack.append(path)
                self.nodes[path] = {}
                pos = align(stop + 1, 4)
            elif token == 2:
                if not stack:
                    raise ValueError('Unbalanced FDT nodes')
                stack.pop()
            elif token == 3:
                if not stack:
                    raise ValueError('FDT property outside node')
                length, name_off = struct.unpack('>II', bounded(structure, pos, 8))
                pos += 8
                if name_off >= len(names):
                    raise ValueError('FDT property name outside strings')
                stop = names.find(b'\0', name_off)
                if stop < 0:
                    raise ValueError('Unterminated FDT property name')
                name = names[name_off:stop].decode('ascii')
                if name in self.nodes[stack[-1]]:
                    raise ValueError('Duplicate FDT property')
                self.nodes[stack[-1]][name] = bounded(structure, pos, length)
                pos = align(pos + length, 4)
            elif token == 4:
                continue
            elif token == 9:
                if stack or '/' not in self.nodes:
                    raise ValueError('Unbalanced or missing FDT root')
                ended = True
                break
            else:
                raise ValueError('Invalid FDT token')
        if not ended:
            raise ValueError('Missing FDT_END')
        for path, props in self.nodes.items():
            for key in ('phandle', 'linux,phandle'):
                if key in props:
                    value = cells(props[key])
                    if len(value) != 1 or value[0] in (0, 0xffffffff):
                        raise ValueError('Invalid phandle')
                    if value[0] in self.phandles and self.phandles[value[0]] != path:
                        raise ValueError('Duplicate phandle')
                    self.phandles[value[0]] = path
        self.reservations = []
        while True:
            address, length = struct.unpack('>QQ', bounded(self.blob, reserve_off, 16))
            reserve_off += 16
            if not address and not length:
                break
            self.reservations.append([address, length])

    def text(self, path, key):
        return strings(self.nodes[path][key])

    def ranges(self, path):
        parent = path.rsplit('/', 1)[0] or '/'
        props = self.nodes[parent]
        ac = cells(props.get('#address-cells', struct.pack('>I', 2)))[0]
        sc = cells(props.get('#size-cells', struct.pack('>I', 1)))[0]
        if ac not in (1, 2) or sc not in (1, 2):
            raise ValueError('Unsupported address/size cell count')
        values = cells(self.nodes[path]['reg'])
        if len(values) % (ac + sc):
            raise ValueError('Truncated FDT reg tuple')
        result = []
        for off in range(0, len(values), ac + sc):
            number = lambda seq: sum(v << (32 * (len(seq) - i - 1)) for i, v in enumerate(seq))
            result.append([number(values[off:off+ac]), number(values[off+ac:off+ac+sc])])
        return result

    def decoded(self):
        def decode(value):
            if not value:
                return True
            try:
                if 32 <= value[0] < 127 and all(c in (0, 9, 10, 13) or 32 <= c < 127 for c in value):
                    return strings(value)
            except (ValueError, UnicodeError):
                pass
            return {'hex': value.hex(), 'cells': cells(value) if not len(value) % 4 else None}
        return {p: {k: decode(v) for k, v in props.items()} for p, props in self.nodes.items()}

    def to_blob(self):
        """Serialize edited trees; validate the result through the same reader."""
        names, offsets = bytearray(), {}
        for props in self.nodes.values():
            for name in props:
                if name not in offsets:
                    offsets[name] = len(names)
                    names.extend(name.encode('ascii') + b'\0')
        children = {p: [] for p in self.nodes}
        for p in self.nodes:
            if p != '/':
                parent = p.rsplit('/', 1)[0] or '/'
                if parent not in children:
                    raise ValueError('Missing FDT parent')
                children[parent].append(p)
        structure = bytearray()

        def emit(path):
            structure.extend(struct.pack('>I', 1))
            structure.extend((path.rsplit('/', 1)[-1] if path != '/' else '').encode('ascii') + b'\0')
            structure.extend(b'\0' * (-len(structure) % 4))
            for name, value in self.nodes[path].items():
                structure.extend(struct.pack('>III', 3, len(value), offsets[name]))
                structure.extend(value)
                structure.extend(b'\0' * (-len(structure) % 4))
            for child in children[path]:
                emit(child)
            structure.extend(struct.pack('>I', 2))

        emit('/')
        structure.extend(struct.pack('>I', 9))
        reserves = b''.join(struct.pack('>QQ', *r) for r in self.reservations + [[0, 0]])
        off = 40 + len(reserves)
        total = off + len(structure) + len(names)
        header = struct.pack('>10I', 0xd00dfeed, total, off, off + len(structure), 40,
                             17, 16, 0, len(names), len(structure))
        result = header + reserves + structure + names
        Fdt(result)
        return result


def split_dtbs(blob):
    """Split a vendor_boot DTB section; only zero alignment padding is allowed."""
    result, offset = [], 0
    while offset < len(blob):
        if blob[offset] == 0:
            offset += 1
            continue
        tree = Fdt(blob[offset:])
        result.append(tree)
        offset += len(tree.blob)
    if not result:
        raise ValueError('Empty vendor_boot DTB section')
    return result


def boot_image(blob):
    """Return checked offsets/sizes; do not transplant stock command lines."""
    if blob[:8] == b'ANDROID!':
        ver, = struct.unpack('<I', bounded(blob, 40, 4))
        if ver in (3, 4):
            kernel, ramdisk = struct.unpack('<II', bounded(blob, 8, 8))
            hdr_size, = struct.unpack('<I', bounded(blob, 20, 4))
            if hdr_size != (1584 if ver == 4 else 1580):
                raise ValueError('Unexpected boot header size')
            page = 4096
            offset = page
            sections = {'kernel': [offset, kernel]}
            offset = align(offset + kernel, page)
            sections['ramdisk'] = [offset, ramdisk]
            if ver == 4:
                signature, = struct.unpack('<I', bounded(blob, 1580, 4))
                sections['signature'] = [align(offset + ramdisk, page), signature]
        elif ver in (0, 1, 2):
            kernel, _, ramdisk, _, second = struct.unpack('<5I', bounded(blob, 8, 20))
            page, = struct.unpack('<I', bounded(blob, 36, 4))
            align(0, page)
            sections = {'kernel': [page, kernel]}
            offset = align(page + kernel, page)
            sections['ramdisk'] = [offset, ramdisk]
            offset = align(offset + ramdisk, page)
            sections['second'] = [offset, second]
            offset = align(offset + second, page)
            if ver >= 1:
                recovery, rec_offset = struct.unpack('<IQ', bounded(blob, 1632, 12))
                sections['recovery_dtbo'] = [rec_offset, recovery]
                offset = align(max(offset, rec_offset + recovery), page)
            if ver == 2:
                dtb_size, = struct.unpack('<I', bounded(blob, 1648, 4))
                sections['dtb'] = [offset, dtb_size]
        else:
            raise ValueError('Unsupported Android boot version')
        kind = 'boot'
    elif blob[:8] == b'VNDRBOOT':
        ver, page = struct.unpack('<II', bounded(blob, 8, 8))
        if ver not in (3, 4):
            raise ValueError('Unsupported vendor boot version')
        hdr_size, dtb_size = struct.unpack('<II', bounded(blob, 2096, 8))
        if hdr_size != (2128 if ver == 4 else 2112):
            raise ValueError('Unexpected vendor boot header size')
        ramdisk, = struct.unpack('<I', bounded(blob, 24, 4))
        offset = align(hdr_size, page)
        sections = {'vendor_ramdisk': [offset, ramdisk]}
        offset = align(offset + ramdisk, page)
        sections['dtb'] = [offset, dtb_size]
        if ver == 4:
            table_size, entries, entry_size, config_size = struct.unpack('<4I', bounded(blob, 2112, 16))
            if (entries and entry_size < 108) or table_size != entries * entry_size:
                raise ValueError('Invalid vendor ramdisk table geometry')
            offset = align(offset + dtb_size, page)
            sections['ramdisk_table'] = [offset, table_size]
            sections['bootconfig'] = [align(offset + table_size, page), config_size]
        kind = 'vendor_boot'
    else:
        raise ValueError('Not an Android boot image')
    for offset, size in sections.values():
        bounded(blob, offset, size)
    result = {'type': kind, 'header_version': ver, 'page_size': page, 'sections': sections,
              'partition_bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()}
    if kind == 'vendor_boot':
        result['kernel_addr'], result['ramdisk_addr'] = struct.unpack('<II', bounded(blob, 16, 8))
        result['tags_addr'], = struct.unpack('<I', bounded(blob, 2080, 4))
        result['dtb_addr'], = struct.unpack('<Q', bounded(blob, 2104, 8))
    return result


def dtbo_entries(blob):
    magic, total, hdr_size, entry_size, count, off, page, version = struct.unpack('>8I', bounded(blob, 0, 32))
    if magic != 0xd7b7ab1e or hdr_size < 32 or entry_size < 32 or total > len(blob):
        raise ValueError('Invalid DTBO table')
    bounded(blob[:total], off, count * entry_size)
    result = []
    for i in range(count):
        size, start, ident, rev, *custom = struct.unpack('>8I', bounded(blob, off + i * entry_size, 32))
        data = bounded(blob[:total], start, size)
        tree = Fdt(data)
        result.append({'index': i, 'id': ident, 'rev': rev, 'custom': custom,
                       'offset': start, 'size': size, 'tree': tree})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='PRIVATE local output, outside Git')
    args = parser.parse_args()
    # Reuse the collector privacy gate; the analyser never communicates with ADB.
    import importlib.util
    spec = importlib.util.spec_from_file_location('collector', Path(__file__).with_name('collect-stock.py'))
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    out = collector.private_destination(args.output)
    out.mkdir(parents=True, exist_ok=True)
    tree = Fdt((args.stock / 'runtime.dtb').read_bytes())
    (out / 'runtime.json').write_text(json.dumps(tree.decoded(), indent=2) + '\n', encoding='utf-8')
    report = {'runtime_sha256': hashlib.sha256(tree.blob).hexdigest(), 'images': {},
              'memory': {p: tree.ranges(p) for p in tree.nodes if p == '/memory' or p.startswith('/memory@')},
              'reserved': {p: tree.ranges(p) for p in tree.nodes if p.startswith('/reserved-memory/') and 'reg' in tree.nodes[p]}}
    for name in ('boot', 'init_boot', 'vendor_boot'):
        blob = (args.stock / (name + '.img')).read_bytes()
        info = boot_image(blob)
        report['images'][name] = info
        if 'dtb' in info['sections']:
            off, size = info['sections']['dtb']
            (out / (name + '-dtbs.bin')).write_bytes(bounded(blob, off, size))
    entries = dtbo_entries((args.stock / 'dtbo.img').read_bytes())
    report['dtbo'] = [{k: v for k, v in entry.items() if k != 'tree'} for entry in entries]
    for entry in entries:
        (out / f"dtbo-{entry['index']:02d}.dtb").write_bytes(entry['tree'].blob)
    (out / 'geometry.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('Parsed stock evidence offline; detailed output is PRIVATE.')


if __name__ == '__main__':
    main()
