# SPDX-License-Identifier: GPL-2.0-only
"""Derive a PRIVATE native-display initramfs; no shell or device operations."""
import gzip
import hashlib
from io import BytesIO
from pathlib import PurePosixPath
import re
import stat
import struct
import tarfile
import zlib

from common import make_cpio, read_cpio
from spr import encode_profile


def module_closure(blob, release):
    """Read only regular matching modules from a bounded, validated dep graph."""
    if not re.fullmatch(r'[A-Za-z0-9._+\-]{1,160}', release):
        raise ValueError('Unsafe kernel release')
    prefix = 'lib/modules/'+release+'/'
    with tarfile.open(fileobj=BytesIO(blob), mode='r:*') as archive:
        members = {}
        for entry in archive.getmembers():
            path = PurePosixPath(entry.name)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.name:
                raise ValueError('Unsafe module archive path')
            name = path.as_posix()
            if name in members:
                raise ValueError('Duplicate module archive member')
            members[name] = entry
        def read(name):
            entry = members.get(name)
            if entry is None or not entry.isfile() or entry.size > 32*1024*1024:
                raise ValueError('Missing/nonregular/oversized module input: '+name)
            return archive.extractfile(entry).read()
        graph = {}
        for line in read(prefix+'modules.dep').decode().splitlines():
            if not line.strip():
                continue
            key, sep, tail = line.partition(':')
            if not sep or key in graph:
                raise ValueError('Malformed or duplicate module dependency')
            deps = tail.split()
            for path in [key, *deps]:
                if PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or '\\' in path:
                    raise ValueError('Unsafe module dependency')
            graph[key] = deps
        chosen, visiting = set(), set()
        def add(key):
            if key in visiting:
                raise ValueError('Cyclic module dependency')
            if key in chosen:
                return
            if key not in graph or not key.endswith('.ko'):
                raise ValueError('Missing or unsupported compressed module: '+key)
            visiting.add(key)
            for dep in graph[key]:
                add(dep)
            visiting.remove(key)
            chosen.add(key)
        for target in ['msm.ko', 'panel-oneplus-aa569.ko']:
            matches = [name for name in graph if PurePosixPath(name).name == target]
            if len(matches) != 1:
                raise ValueError('Missing/ambiguous native module: '+target)
            add(matches[0])
        files = {}
        for path in sorted(chosen):
            data = read(prefix+path)
            vermagic = re.search(rb'vermagic=([^\0]+)', data)
            if (data[:6] != b'\x7fELF\x02\x01' or len(data) < 64 or
                    int.from_bytes(data[18:20], 'little') != 183 or not vermagic or
                    not vermagic[1].decode().startswith(release+' ')):
                raise ValueError('Module architecture/release mismatch: '+path)
            files[prefix+path] = data
        files[prefix+'modules.dep'] = ''.join(key+': '+ ' '.join(graph[key])+'\n'
                                                 for key in sorted(chosen)).encode()
        # BusyBox uses textual dependencies. No unrelated aliases or softdeps
        # are installed; the helper loads only these two explicit entry points.
        if prefix+'modules.builtin' in members:
            files[prefix+'modules.builtin'] = read(prefix+'modules.builtin')
        return files, sorted(chosen)


def build_initramfs(original, modules, release, xml, helper, *, encoded_profile=None):
    entries = read_cpio(gzip.decompress(original))
    if ('bin/modprobe' not in entries or 'init' not in entries or
            b'sleep 300' not in entries['init']['data'] or b'protect_storage' not in entries['init']['data']):
        raise ValueError('Missing original diagnostics/recovery/modprobe')
    files, closure = module_closure(modules, release)
    if encoded_profile is None:
        profile = encode_profile(xml)
    else:
        if xml is not None:
            raise ValueError('Supply either XML or a previously verified private profile')
        profile = encoded_profile
        if len(profile) != 176 or profile[:8] != b'AA569S2\0':
            raise ValueError('Invalid private profile framing')
        version,width,height,hz,words,crc,r1,r2 = struct.unpack_from('<8I',profile,8)
        if (version,width,height,hz,words,r1,r2) != (1,1440,3168,120,34,0,0) or zlib.crc32(profile[40:]) != crc:
            raise ValueError('Invalid private profile header or checksum')
    if helper.count(b'@KERNEL_RELEASE@') != 1:
        raise ValueError('Missing/duplicate native kernel guard template')
    files['lib/firmware/qcom/sm8750/oneplus/dodge/aa569-spr-v1.bin'] = profile
    files['sbin/start-native-display'] = helper.replace(b'@KERNEL_RELEASE@', release.encode())
    if b'@KERNEL_RELEASE@' in files['sbin/start-native-display']:
        raise ValueError('Unresolved native helper template')
    files['etc/oneplus13-native-candidate'] = (release+'\n').encode()
    files['etc/oneplus13-native-handoff'] = ('ONEPLUS13_NATIVE_HANDOFF_V1\n'+release+'\n'+
        hashlib.sha256(profile).hexdigest()+'\n'+
        hashlib.sha256(files['sbin/start-native-display']).hexdigest()+'\n').encode()
    for name, data in files.items():
        if name in entries:
            raise ValueError('Native input would replace an existing ramdisk entry')
        for parent in reversed(PurePosixPath(name).parents):
            if str(parent) == '.':
                continue
            key = str(parent)
            if key in entries and not stat.S_ISDIR(entries[key]['mode']):
                raise ValueError('Native input traverses a nondirectory')
            entries.setdefault(key, {'mode': stat.S_IFDIR|0o755, 'data': b'', 'major': 0, 'minor': 0})
        entries[name] = {'mode': stat.S_IFREG|(0o755 if name == 'sbin/start-native-display' else 0o600),
                         'data': data, 'major': 0, 'minor': 0}
    packed = gzip.compress(make_cpio([(name, p['mode'], p['data'], p['major'], p['minor'])
                                     for name, p in sorted(entries.items())]), mtime=0)
    check = read_cpio(gzip.decompress(packed))
    for name, expected in files.items():
        if check[name]['data'] != expected:
            raise ValueError('Native initramfs roundtrip mismatch')
    original_entries = read_cpio(gzip.decompress(original))
    if any(check[name] != original_entries[name] for name in original_entries):
        raise ValueError('Original diagnostics changed during native packaging')
    return packed, {'scope': 'PRIVATE_HOST_NATIVE_INITRAMFS_ONLY', 'hardware_tested': False,
                    'original_init_and_recovery_unchanged': True, 'modules': closure,
                    'private_profile_bytes': len(profile), 'private_profile_sha256': hashlib.sha256(profile).hexdigest(),
                    'manual_load_only_after_usb': True, 'dma_used_by_spr_backend': False,
                    'native_handoff_version': 1, 'handoff_binds_helper_and_profile': True,
                    'initramfs_sha256': hashlib.sha256(packed).hexdigest(),
                    'qemu_this_derived_image': False}
