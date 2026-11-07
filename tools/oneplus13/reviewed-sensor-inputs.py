#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Assemble reviewed text data and newly built native userspace, no host imports."""
import argparse,hashlib,io,json,re,struct,tarfile
from pathlib import Path,PurePosixPath
DATA=('var/lib/erhai/sensors/','usr/share/erhai-sensors/hexagonrpcd/',
      'usr/share/oneplus13/sensor-rom-reference/')
PREFIX='usr/lib/erhai/sensors/'
NATIVE={'bin/hexagonrpcd','bin/ssccli','bin/phone-sensor-bus-check',
        'libexec/iio-sensor-proxy','lib/libssc.so.2','lib/libhexagonrpc.so.0.5',
        'lib/libmbim-glib.so.4','lib/libprotobuf-c.so.1','lib/libqmi-glib.so.5','lib/libqrtr-glib.so.0'}
LINKS={
 'usr/share/erhai-sensors/hexagonrpcd/sensors/registry':'/var/lib/erhai/sensors/persist/registry',
 'usr/share/erhai-sensors/hexagonrpcd/sensors/sns_reg_version':'/var/lib/erhai/sensors/persist/sns_reg_version',
 'usr/share/erhai-sensors/hexagonrpcd/socinfo':'/var/lib/erhai/sensors/socinfo'}
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def canonical(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or str(path)!=name or not re.fullmatch(r'[A-Za-z0-9_./+-]+',name):raise ValueError('Unsafe sensor data path')
    return name
def check_keys(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if re.search(r'(?i)serial|uuid|imei|imsi|mac.?addr|password|ssid|token|private.?key',key):raise ValueError('Identifier-like sensor field')
            check_keys(item)
    elif isinstance(value,list):
        for item in value:check_keys(item)
def data_entries(path):
    doc=json.loads(Path(path).read_text(encoding='utf-8'))
    if doc.get('format')!='ONEPLUS13_REVIEWED_SENSOR_RUNTIME_DATA_V1' or doc.get('links')!=LINKS:raise ValueError('Wrong reviewed dataset')
    facts=doc.get('provenance',{})
    if facts.get('target_device')!='oneplus,dodge' or facts.get('unique_network_or_device_identities_included') is not False or facts.get('all_serial_registry_files_excluded') is not True:raise ValueError('Unreviewed data provenance')
    if len(doc.get('files',{}))!=922:raise ValueError('Reviewed sensor allowlist count changed')
    result={};total=0
    for name,entry in doc['files'].items():
        canonical(name)
        if not name.startswith(DATA) or name.endswith('/import.json') or 'serial' in name.lower():raise ValueError('Unreviewed sensor destination')
        if entry.get('mode') not in (0o640,0o644) or not isinstance(entry.get('text'),str):raise ValueError('Executable/nontext sensor data')
        raw=entry['text'].encode('utf-8');total+=len(raw)
        if len(raw)!=entry['bytes'] or hashlib.sha256(raw).hexdigest()!=entry['sha256'] or len(raw)>1024*1024 or total>2*1024*1024:raise ValueError('Sensor data hash/size mismatch')
        if '\0' in entry['text'] or re.search(r'(?i)github_pat_|gh[pousr]_|PRIVATE KEY|(?:[0-9a-f]{2}:){5}[0-9a-f]{2}',entry['text']):raise ValueError('Identity/credential or binary sensor data')
        try:value=json.loads(entry['text'])
        except ValueError:value=None
        if value is not None:check_keys(value)
        result[name]=(raw,entry['mode'])
    return result
def assemble(data,native,manifest,out):
    files=data_entries(data);m=json.loads(Path(manifest).read_text())
    if (m.get('scope')!='PUBLIC_PHONE_SENSOR_MANUAL_RUNTIME_CANDIDATE' or set(m.get('files',{}))!=NATIVE or
        not m.get('phone_property_and_matrix_fixes') or not m.get('bus_client_variant_tests_passed') or
        sha(native)!=m['archive_sha256']):raise ValueError('Wrong native runtime input')
    with tarfile.open(native) as tf:
        seen=set()
        for entry in tf:
            name=canonical(entry.name)
            if name not in NATIVE or name in seen or not entry.isfile():raise ValueError('Unexpected native entry')
            seen.add(name);raw=tf.extractfile(entry).read();info=m['files'][name]
            if len(raw)!=info['bytes'] or hashlib.sha256(raw).hexdigest()!=info['sha256'] or entry.mode!=info['mode']:raise ValueError('Native input hash changed')
            if raw[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',raw,18)[0]!=183:raise ValueError('Not ARM64 native code')
            files[PREFIX+name]=(raw,entry.mode)
        if seen!=NATIVE:raise ValueError('Incomplete native runtime')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    target=out/'phone-sensor-inputs.tar.gz';inventory={}
    with tarfile.open(target,'x:gz') as tf:
        for name,(raw,mode) in sorted(files.items()):
            entry=tarfile.TarInfo(name);entry.size=len(raw);entry.mode=mode;entry.mtime=0
            tf.addfile(entry,io.BytesIO(raw));inventory[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'mode':mode}
        for name,dest in LINKS.items():
            entry=tarfile.TarInfo(name);entry.type=tarfile.SYMTYPE;entry.linkname=dest;entry.mode=0o777;tf.addfile(entry)
    proof={'scope':'PUBLIC_REVIEWED_PHONE_SENSOR_RUNTIME_INPUTS','files':inventory,'links':LINKS,
        'archive_sha256':sha(target),'integration_commit':m['integration_commit'],
        'reviewed_data_sha256':sha(data),'native_runtime_sha256':sha(native),
        'native_elf_count':len(NATIVE),'text_data_files':922,'owner_identifiers_included':False,
        'data_services_auto_started':False,'hardware_qualified':False}
    import importlib
    importlib.import_module('ram-sensor-fileserver').archive_entries(target,proof)
    (out/'phone-sensor-inputs-manifest.json').write_text(json.dumps(proof,indent=2)+'\n')
    return proof
def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('data','native','manifest','output'):p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args();result=assemble(a.data,a.native,a.manifest,a.output)
    print(json.dumps({k:result[k] for k in ('scope','integration_commit','text_data_files','native_elf_count','archive_sha256')}))
if __name__=='__main__':main()
