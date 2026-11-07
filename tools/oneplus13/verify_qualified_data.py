#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the complete allowlisted, scrubbed hardware dataset without a phone."""
import hashlib,json,re,sys
from pathlib import Path
from stock import Fdt

def verify(root):
    root=Path(root).resolve()
    pin=json.loads((root/'manifest.json').read_text())
    if pin['format']!='ONEPLUS13_QUALIFIED_DATA_V1':raise ValueError('Wrong data format')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual!=set(pin['files'])|{'manifest.json','PROVENANCE.md'}:raise ValueError('Missing or unreviewed data files')
    for name,item in pin['files'].items():
        p=root/name
        if Path(name).is_absolute() or '..' in Path(name).parts or p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('Unsafe data path')
        if name not in {'boot-geometry.json','calibrated.dtb','display-profile.bin','sensors/reviewed-runtime-data.json'} and not name.startswith('firmware/'):raise ValueError('Data allowlist violated')
        blob=p.read_bytes()
        if len(blob)!=item['bytes'] or hashlib.sha256(blob).hexdigest()!=item['sha256']:raise ValueError('Data hash mismatch: '+name)
        if re.search(rb'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|-----BEGIN OPENSSH PRIVATE KEY-----',blob):raise ValueError('Private key material')
    dt=Fdt((root/'calibrated.dtb').read_bytes())
    for path,props in dt.nodes.items():
        for key in props:
            if re.search(r'(^|,)(serial-number|local-mac-address|mac-address|rng-seed|kaslr-seed|uuid|imei)$',key,re.I):raise ValueError('Unique device identity/seed property')
    if 'bootargs' in dt.nodes.get('/chosen',{}):raise ValueError('Old boot arguments must not be published')
    if 'sensors/reviewed-runtime-data.json' in pin['files']:
        import importlib
        importlib.import_module('reviewed-sensor-inputs').data_entries(root/'sensors/reviewed-runtime-data.json')
    return {'files':len(pin['files']),'firmware_files':sum(n.startswith('firmware/') for n in pin['files']),
            'sha256':hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()}

if __name__=='__main__':print(json.dumps(verify(sys.argv[1]),indent=2))
