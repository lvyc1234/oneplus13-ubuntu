#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Review saved phone ROM against the original project's loader contract only."""
import argparse
import hashlib
import json
from pathlib import Path
import re

SOCINFO=('hw_platform','platform_subtype','platform_subtype_id','platform_version','soc_id','revision')
FILES={name:'/sys/devices/soc0/'+name for name in SOCINFO}
FILES.update(input='json.lst',config='/odm/etc/sensor/config',output='/mnt/vendor/persist/sensors/registry/registry')
PROPERTIES={
    'persist.vendor.sensors.enable.property':'/mnt/vendor/persist/sensors/registry/file1',
    'persist.vendor.sensors.enable.property1':'/mnt/vendor/persist/sensors/registry/file2'}

def parse_config(text):
    result={}
    for raw in text.splitlines():
        line=raw.strip()
        if not line or line.startswith('#'):continue
        parts=line.split('=',2)
        if parts[0]=='version' and len(parts)==2:key=('version','');value=parts[1]
        elif parts[0] in ('file','property') and len(parts)==3:key=tuple(parts[:2]);value=parts[2]
        else:raise ValueError('unsupported registry configuration syntax')
        if key in result:raise ValueError('duplicate registry configuration key')
        result[key]=value
    expected={('version',''):'2',**{('file',k):v for k,v in FILES.items()},
              **{('property',k):v for k,v in PROPERTIES.items()}}
    if result!=expected:raise ValueError('registry configuration differs from reviewed narrow contract')
    return result

def short_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1<<20:
        raise ValueError('bounded regular input file required')
    return path.read_bytes()

def index_report(path):
    text=short_file(path).decode('utf-8');entries=[s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith('#')]
    if len(entries)>4096:raise ValueError('oversized sensor index')
    if not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.json',s) for s in entries):
        raise ValueError('unsafe sensor index name')
    present=0;missing=0
    for name in entries:
        p=path.parent/name
        if p.is_symlink():raise ValueError('symlinked sensor ROM entry')
        if p.exists():json.loads(short_file(p));present+=1
        else:missing+=1
    return {'entries':len(entries),'unique_entries':len(set(entries)),
            'duplicate_occurrences':len(entries)-len(set(entries)),
            'present':present,'missing':missing,'all_present':missing==0}

def review(rom,reference,shim):
    config=short_file(rom/'vendor/sns_reg_config');baseline=short_file(reference)
    parse_config(config.decode());parse_config(baseline.decode())
    patch=short_file(shim).decode()
    allowed=set(re.findall(r'static const char property[0-9]+\[\] = "([^"]+)";',patch))
    if allowed!=set(PROPERTIES):raise ValueError('original property shim allowlist differs')
    vendor=index_report(rom/'vendor/config/json.lst');odm=index_report(rom/'odm/config/json_list')
    if not odm['all_present']:raise ValueError('phone ODM index is not closed')
    return {'scope':'HOST_SAVED_ROM_LOADER_CONTRACT_ONLY','phone_operations':0,
            'phone_config_sha256':hashlib.sha256(config).hexdigest(),
            'original_config_sha256':hashlib.sha256(baseline).hexdigest(),
            'config_byte_identical_to_original':config==baseline,
            'six_socinfo_paths_match':True,'two_property_names_match_narrow_shim':True,
            'vendor_index':vendor,'odm_index':odm,
            'index_names_preserved':['vendor:json.lst','odm:json_list'],
            'per_unit_registry_present':False,'sensor_stream_eligible':False,
            'remaining_gates':['owner-specific registry/calibration and socinfo inputs',
                               'phone DSP image and reverse RPC ABI identity',
                               'mapping/first-attach/recovery review before any sensor stream',
                               'physical axis and accuracy acceptance later'],
            'limitations':['Matching text configuration does not establish matching DSP skel ABI.',
                          'The original project has the same json.lst/json_list arrangement; no speculative rename is applied.',
                          'Vendor missing index entries are counted, never fabricated or silently treated as complete.']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--reference-config',type=Path,required=True);p.add_argument('--shim-patch',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    result=review(a.rom,a.reference_config,a.shim_patch)
    a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
