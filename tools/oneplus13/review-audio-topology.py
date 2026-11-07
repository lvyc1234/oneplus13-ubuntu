#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Review saved FDT enablement; optionally quarantine an unjustified WSA branch.

Host files only. Never controls a phone, amplifier, mixer or stream. An enabled
node needs every ancestor enabled. A quarantine is not a working audio port.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from stock import Fdt, cells, strings

WSA = {'qcom,wsa884x', 'qcom,wsa884x_2', 'qcom,wsa883x', 'qcom,wsa883x_2', 'sdw20217020400'}
AW = 'awinic,aw882xx_smartpa'


def compatibles(props):
    return set(strings(props['compatible'])) if 'compatible' in props else set()


def enabled(nodes, path):
    while True:
        if path not in nodes:
            raise ValueError('missing ancestor in FDT')
        status = strings(nodes[path].get('status', b'okay\0'))
        if len(status) != 1 or status[0] not in ('ok', 'okay'):
            return False
        if path == '/':
            return True
        path = path.rsplit('/', 1)[0] or '/'


def inventory(nodes):
    wsa = [p for p, v in nodes.items() if compatibles(v) & WSA]
    aw = [p for p, v in nodes.items() if AW in compatibles(v)]
    return {'wsa_nodes': len(wsa), 'enabled_wsa_nodes': sum(enabled(nodes, p) for p in wsa),
            'aw_nodes': len(aw), 'enabled_aw_nodes': sum(enabled(nodes, p) for p in aw)}


def review(stock, candidate):
    a, b = inventory(stock.nodes), inventory(candidate.nodes)
    return {'scope': 'HOST_SAVED_FDT_ONLY', 'stock': a, 'candidate': b,
            'candidate_wsa_enablement_not_supported_by_stock': a['enabled_wsa_nodes'] == 0 and b['enabled_wsa_nodes'] > 0,
            'eligible_for_wsa_stream': False, 'phone_operations': 0,
            'reason': 'Enabled stock nodes, exact silicon, clocks, protected tuning and gain are separate prerequisites; template presence and PCM open are insufficient.'}


def quarantine(stock, candidate):
    facts = review(stock, candidate)
    if not facts['candidate_wsa_enablement_not_supported_by_stock'] or facts['stock']['enabled_aw_nodes'] != 2:
        raise ValueError('expected disabled-stock WSA and two enabled AWINIC nodes required')
    result = copy.deepcopy(candidate)
    speakers = [p for p, v in result.nodes.items() if compatibles(v) & WSA and enabled(result.nodes, p)]
    if len(speakers) != 2:
        raise ValueError('exactly two candidate WSA speaker nodes required')
    parents = {p.rsplit('/', 1)[0] for p in speakers}
    if len(parents) != 1:
        raise ValueError('one WSA SoundWire controller required')
    parent = next(iter(parents))
    if not any(c.startswith('qcom,soundwire-') for c in compatibles(result.nodes[parent])):
        raise ValueError('WSA parent must be a SoundWire controller')
    links = [p for p in result.nodes if p.endswith('/wsa-playback-dai-link')]
    if len(links) != 1:
        raise ValueError('one explicit WSA playback link required')
    link = links[0]
    phandles = {value for value, path in result.phandles.items() if path in speakers}
    codec = result.nodes.get(link+'/codec', {})
    if len(phandles) != 2 or not phandles <= set(cells(codec.get('sound-dai', b''))):
        raise ValueError('WSA playback must reference both speaker phandles')
    removed = {p for p in result.nodes if p == link or p.startswith(link+'/')}
    for p, v in result.nodes.items():
        if p not in removed and 'sound-dai' in v and phandles & set(cells(v['sound-dai'])):
            raise ValueError('another DAI link references a WSA speaker')
    disabled = set(speakers) | {parent}
    for path in disabled:
        result.nodes[path]['status'] = b'disabled\0'
    for path in removed:
        del result.nodes[path]
    # No unrelated node, regulator, GPIO, clock, panel, memory or calibration
    # property may change. Keep the LPASS macro clock provider itself intact.
    expected = copy.deepcopy(candidate.nodes)
    for path in disabled:
        expected[path]['status'] = b'disabled\0'
    for path in removed:
        del expected[path]
    if result.nodes != expected or result.reservations != candidate.reservations:
        raise ValueError('unexpected device-tree mutation')
    packed = result.to_blob()
    readback = Fdt(packed)
    if readback.nodes != expected or readback.reservations != candidate.reservations:
        raise ValueError('quarantined FDT read-back differs')
    facts.update({'quarantined_candidate': inventory(readback.nodes),
                  'disabled_nodes': sorted(disabled), 'removed_link_nodes': sorted(removed),
                  'unrelated_properties_preserved': True, 'reservations_preserved': True,
                  'lpass_macro_clock_provider_preserved': True,
                  'nonaudio_quarantine_complete': True, 'audio_supported': False})
    return packed, facts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--quarantine-output', type=Path)
    args = p.parse_args()
    if args.quarantine_output and args.quarantine_output.resolve() in {args.stock.resolve(), args.candidate.resolve()}:
        raise ValueError('never overwrite input evidence')
    stock_bytes, candidate_bytes = args.stock.read_bytes(), args.candidate.read_bytes()
    stock, candidate = Fdt(stock_bytes), Fdt(candidate_bytes)
    if args.quarantine_output:
        blob, facts = quarantine(stock, candidate)
        args.quarantine_output.write_bytes(blob)
        facts['output_sha256'] = hashlib.sha256(blob).hexdigest()
    else:
        facts = review(stock, candidate)
    facts['stock_sha256'] = hashlib.sha256(stock_bytes).hexdigest()
    facts['candidate_sha256'] = hashlib.sha256(candidate_bytes).hexdigest()
    args.report.write_text(json.dumps(facts, indent=2)+'\n')
    print(json.dumps(facts, indent=2))


if __name__ == '__main__':
    main()
