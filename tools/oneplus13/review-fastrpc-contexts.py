#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare saved ADSP FastRPC memory/contexts after resolving phandle identity.

Host-only. Matching DT data does not prove kernel/DSP ABI or qualify hardware.
Reject untranslatable buses instead of comparing unrelated numeric phandles.
"""
import argparse, hashlib, json
from pathlib import Path
from stock import Fdt, cells, strings


def parent(path):
    return (path.rsplit('/', 1)[0] or '/') if path != '/' else ''


def enabled(nodes, path):
    while path:
        if nodes[path].get('status', b'okay\0') not in (b'okay\0', b'ok\0'):
            return False
        path = parent(path)
    return True


def number(values):
    result = 0
    for value in values:
        result = (result << 32) | value
    return result


def count(nodes, path, key, default):
    values = cells(nodes[path].get(key, default.to_bytes(4, 'big')))
    if len(values) != 1 or values[0] not in (1, 2):
        raise ValueError('Unsupported bus cell geometry')
    return values[0]


def register_ranges(nodes, path):
    bus = parent(path)
    a = count(nodes, bus, '#address-cells', 2)
    s = count(nodes, bus, '#size-cells', 1)
    data = cells(nodes[path]['reg'])
    if not data or len(data) % (a + s):
        raise ValueError('Malformed register geometry')
    result = [[number(data[i:i+a]), number(data[i+a:i+a+s])]
              for i in range(0, len(data), a+s)]
    while bus != '/':
        if 'ranges' not in nodes[bus]:
            raise ValueError('Unresolved register address translation')
        ranges = cells(nodes[bus]['ranges'])
        if ranges:
            a = count(nodes, bus, '#address-cells', 2)
            p = count(nodes, parent(bus), '#address-cells', 2)
            s = count(nodes, bus, '#size-cells', 1)
            width = a+p+s
            if len(ranges) % width:
                raise ValueError('Malformed address translation')
            translated = []
            for address, size in result:
                matches = []
                for i in range(0,len(ranges),width):
                    child = number(ranges[i:i+a]); host = number(ranges[i+a:i+a+p])
                    length = number(ranges[i+a+p:i+width])
                    if child <= address and size > 0 and address+size <= child+length:
                        matches.append([host+address-child, size])
                if len(matches) != 1:
                    raise ValueError('Unmapped or ambiguous register translation')
                translated.append(matches[0])
            result = translated
        bus = parent(bus)
    return result


def normalize(nodes, path):
    props = nodes[path]
    if not enabled(nodes, path) or b'qcom,fastrpc' not in props.get('compatible', b'').split(b'\0'):
        raise ValueError('Enabled FastRPC domain required')
    handles = {}
    for name, value in nodes.items():
        if 'phandle' in value:
            refs = cells(value['phandle'])
            if len(refs) != 1 or refs[0] in handles:
                raise ValueError('Ambiguous phandle')
            handles[refs[0]] = name
    memories = []
    for ref in cells(props['memory-region']):
        mem = nodes[handles[ref]]
        memories.append({key: mem[key].hex() for key in
            ('compatible', 'reg', 'size', 'alignment', 'alloc-ranges', 'reusable', 'no-map') if key in mem})
    contexts = []
    for name, cb in nodes.items():
        if parent(name) != path:
            continue
        if b'qcom,fastrpc-compute-cb' not in cb.get('compatible', b'').split(b'\0'):
            raise ValueError('Unreviewed FastRPC child')
        if not enabled(nodes, name):
            raise ValueError('Disabled context bank')
        regs = cells(cb['reg'])
        if len(regs) != 1:
            raise ValueError('Unexpected context index')
        args = cells(cb['iommus']); targets = []
        while args:
            controller = handles[args.pop(0)]
            n = cells(nodes[controller]['#iommu-cells'])
            if n != [2] or len(args) < 2 or not enabled(nodes, controller):
                raise ValueError('Unsupported or disabled IOMMU controller')
            targets.append({'controller_mmio': register_ranges(nodes, controller), 'arguments': args[:2]})
            del args[:2]
        if not targets:
            raise ValueError('Missing IOMMU mapping')
        contexts.append({'index': regs[0], 'iommus': targets,
            'flags': {k: cb[k].hex() for k in ('qcom,shared-cb', 'qcom,secure-context-bank',
                'qcom,untrusted-process', 'qcom,iommu-dma-addr-pool') if k in cb}})
    contexts.sort(key=lambda item: item['index'])
    if not contexts or len({c['index'] for c in contexts}) != len(contexts):
        raise ValueError('Missing or duplicate context index')
    return {'memory': memories, 'vmids': cells(props.get('qcom,vmids', b'')),
        'non_secure_domain': 'qcom,non-secure-domain' in props, 'contexts': contexts}


def compare(stock, candidate, stock_path, candidate_path):
    before = normalize(Fdt(stock).nodes, stock_path)
    after = normalize(Fdt(candidate).nodes, candidate_path)
    return {'scope': 'OFFLINE_ADSP_FASTRPC_DT_COMPARISON_ONLY', 'phone_operations': 0,
        'stock_sha256': hashlib.sha256(stock).hexdigest(), 'candidate_sha256': hashlib.sha256(candidate).hexdigest(),
        'normalized_equal': before == after, 'stock': before, 'candidate': after,
        'sensor_attach_or_stream_eligible': False}


def main():
    p = argparse.ArgumentParser()
    for flag in ('stock', 'candidate', 'stock-path', 'candidate-path', 'report'):
        p.add_argument('--' + flag, required=True)
    a = p.parse_args()
    result = compare(Path(a.stock).read_bytes(), Path(a.candidate).read_bytes(), a.stock_path, a.candidate_path)
    Path(a.report).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('stock','candidate')}, indent=2))
    return 0 if result['normalized_equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
