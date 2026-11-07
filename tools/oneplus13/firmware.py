#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Stage hash-matched phone firmware; leave unidentified WLAN board data inactive."""
import argparse
import json
from pathlib import Path
import shutil
from common import sha256

REPO = Path(__file__).resolve().parents[2]


def verify(source):
    pin = json.loads((REPO/'docs/oneplus13/stock-firmware.json').read_text())
    manifest = json.loads((source/'manifest.json').read_text())
    if (manifest['version'] != pin['version'] or manifest['hardware_tested'] is not False or
            manifest['selected_partition_sha256_verified'] is not True or
            manifest['dumper_source']['commit'] != pin['dumper']['commit']):
        raise ValueError('Unexpected firmware extraction provenance')
    for item in pin['files']:
        path = source/item['output']
        if (not path.resolve().is_relative_to(source.resolve()) or
                not item['owner_hash_observed'] or path.stat().st_size != item['size'] or
                sha256(path) != item['sha256']):
            raise ValueError('Owner firmware equality failed: '+item['output'])
    return pin


def stage(source, out):
    pin = verify(source)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Select an empty staging directory')
    for item in pin['files']:
        name = Path(item['output']).name
        if item['partition'] == 'vendor':
            target = ('qcom/sm8750/oneplus/dodge/'+name if name.endswith('_zap.mbn')
                      else 'qcom/'+name)
            dest = out/'linux-firmware'/target
        else:
            # Deliberately outside firmware search paths: board selection is not
            # established by the PCI revision or the existence of these files.
            dest = out/'pending-wlan-inputs'/item['output'].removeprefix('wlan/')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source/item['output'], dest)
    report = {'scope': 'MATCHING_GENERIC_FIRMWARE_NOT_HARDWARE_TESTED',
              'hardware_tested': False, 'version': pin['version'], 'owner_hash_matches': 9,
              'graphics_search_paths_prepared': True, 'wlan_search_paths_prepared': False,
              'wlan_active_board_id_sar_verified': False, 'firmware_authentication_verified': False,
              'contains_private_calibration': False,
              'source_manifest_sha256': sha256(source/'manifest.json'),
              'files': {p.relative_to(out).as_posix(): {'bytes': p.stat().st_size, 'sha256': sha256(p)}
                        for p in sorted(out.rglob('*')) if p.is_file()}}
    (out/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    shutil.copy2(source/'SOURCES.md', out/'SOURCES.md')
    (out/'README.txt').write_text(
        'Offline firmware inputs, not a working or flashable system.\n'
        'All nine files match observed owner hashes. Vendor terms apply.\n'
        'linux-firmware/ contains the four exact graphics paths.\n'
        'pending-wlan-inputs/ is NOT a Linux firmware search path.\n'
        'Do not rename or force a BDF until hardware-major/QMI board selection and SAR are verified.\n')
    (out/'SHA256SUMS').write_text(''.join(f'{sha256(p)}  {p.relative_to(out).as_posix()}\n'
                                        for p in sorted(out.rglob('*'))
                                        if p.is_file() and p.name != 'SHA256SUMS'))
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    print(json.dumps(stage(a.source.resolve(), a.output.resolve()), indent=2))
