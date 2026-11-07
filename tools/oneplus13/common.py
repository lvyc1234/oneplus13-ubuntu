# SPDX-License-Identifier: GPL-2.0-only
"""Small, device-independent archive and boot-header helpers."""
import hashlib
from pathlib import PurePosixPath
import stat
import struct


def sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def make_cpio(entries):
    """Create deterministic newc without requiring mknod/root on the build host.

    entries: (name, mode, bytes, device-major, device-minor).
    """
    output = bytearray()
    names = set()
    for ino, (name, mode, data, major, minor) in enumerate(
            [*entries, ('TRAILER!!!', stat.S_IFREG, b'', 0, 0)], 1):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not name or name in names:
            raise ValueError(f'Unsafe or duplicate cpio path: {name}')
        names.add(name)
        raw_name = name.encode() + b'\0'
        fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor,
                  len(raw_name), 0]
        output.extend(b'070701' + ''.join(f'{n:08x}' for n in fields).encode())
        output.extend(raw_name)
        output.extend(b'\0' * (-len(output) % 4))
        output.extend(data)
        output.extend(b'\0' * (-len(output) % 4))
    return bytes(output)


def boot_sections(data):
    """Read Android v2 header independently of mkbootimg."""
    if len(data) < 1660 or data[:8] != b'ANDROID!':
        raise ValueError('Not an Android boot image')
    kernel_size, _, ramdisk_size, _, second_size, _, _, page, version = \
        struct.unpack_from('<9I', data, 8)
    if version != 2 or page != 4096 or second_size:
        raise ValueError('Unexpected Android boot image format')
    dtb_size = struct.unpack_from('<I', data, 1648)[0]
    offset = page
    result = {}
    for name, size in [('kernel', kernel_size), ('ramdisk', ramdisk_size), ('dtb', dtb_size)]:
        if not size or offset + size > len(data):
            raise ValueError(f'Truncated or empty boot section: {name}')
        result[name] = data[offset:offset + size]
        offset += (size + page - 1) // page * page
    return result


def read_cpio(data):
    """Bounded independent newc reader for artifact inspection (never extracts)."""
    offset, result = 0, {}
    while offset + 110 <= len(data):
        header = data[offset:offset + 110]
        if header[:6] != b'070701':
            raise ValueError('Invalid newc magic')
        try:
            fields = [int(header[6 + n*8:14 + n*8], 16) for n in range(13)]
        except ValueError as error:
            raise ValueError('Invalid newc fields') from error
        size, namesize = fields[6], fields[11]
        offset += 110
        if namesize < 1 or offset + namesize > len(data):
            raise ValueError('Truncated newc name')
        raw = data[offset:offset + namesize]
        if raw[-1:] != b'\0' or b'\0' in raw[:-1]:
            raise ValueError('Invalid newc name')
        name = raw[:-1].decode('utf-8')
        offset = (offset + namesize + 3) & ~3
        if offset + size > len(data):
            raise ValueError('Truncated newc content')
        if name == 'TRAILER!!!':
            return result
        path = PurePosixPath(name)
        if not name or path.is_absolute() or '..' in path.parts or name in result:
            raise ValueError('Unsafe or duplicate newc path')
        result[name] = {'mode': fields[1], 'major': fields[9], 'minor': fields[10],
                        'data': data[offset:offset + size]}
        offset = (offset + size + 3) & ~3
    raise ValueError('Missing newc trailer')
