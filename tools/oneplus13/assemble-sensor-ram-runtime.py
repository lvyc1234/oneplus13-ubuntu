#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Combine verified userspace and private phone data without activation hooks.

Host-only packaging. No service, udev rule, mount matrix, firmware, module or
installer may enter this archive. The output contains private unit data and
must never be published. Creating it does not qualify a sensor attach/stream.
"""
import argparse,hashlib,io,json,struct,tarfile
from pathlib import Path,PurePosixPath

PREFIX='usr/lib/erhai/sensors/'
DATA_PREFIXES=('var/lib/erhai/sensors/','usr/share/erhai-sensors/hexagonrpcd/',
               'usr/share/oneplus13/sensor-rom-reference/')
DATA_LINKS={
 'usr/share/erhai-sensors/hexagonrpcd/sensors/registry':'/var/lib/erhai/sensors/persist/registry',
 'usr/share/erhai-sensors/hexagonrpcd/sensors/sns_reg_version':'/var/lib/erhai/sensors/persist/sns_reg_version',
 'usr/share/erhai-sensors/hexagonrpcd/socinfo':'/var/lib/erhai/sensors/socinfo'}

def digest(path):
 with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def safe_name(value):
 p=PurePosixPath(value)
 if p.is_absolute() or '..' in p.parts or '\\' in value or str(p)!=value:
  raise ValueError('Noncanonical archive path')
 return value

def read_archive(path,manifest,userspace):
 expected=({x['path']:x for x in manifest['files']} if userspace else manifest['files'])
 links={} if userspace else manifest['links']
 if not userspace and links!=DATA_LINKS:raise ValueError('Unexpected sensor path mapping')
 entries={};regular=set();total=0
 with tarfile.open(path) as tf:
  for member in tf:
   name=safe_name(member.name.rstrip('/') if member.isdir() else member.name)
   if member.isdir():continue
   if name in entries:raise ValueError('Duplicate archive path')
   if userspace:
    if not name.startswith(PREFIX):raise ValueError('Userspace archive includes integration hooks or reference data')
   elif not name.startswith(DATA_PREFIXES):raise ValueError('Data archive includes unreviewed paths')
   if member.issym():
    if userspace:
     target=member.linkname
     if '/' in target or target in ('.','..') or not target.startswith('lib'):
      raise ValueError('Unsafe userspace library link')
     if str(PurePosixPath(name).parent/target) not in expected:raise ValueError('Unbound library link')
    elif links.get(name)!=member.linkname:raise ValueError('Unexpected data link')
    entries[name]=(None,member.mode,member.linkname);continue
   if not member.isfile() or name not in expected:raise ValueError('Unexpected nonregular or unmanifested entry')
   info=expected[name]
   if not 0<=member.size<=4*1024*1024 or member.size!=info['bytes']:raise ValueError('Invalid file length')
   data=tf.extractfile(member).read();total+=len(data)
   if total>16*1024*1024:raise ValueError('Oversized runtime archive')
   if hashlib.sha256(data).hexdigest()!=info['sha256']:raise ValueError('File identity mismatch')
   if userspace:
    if data[:6]!=b'\x7fELF\x02\x01' or len(data)<20 or struct.unpack_from('<H',data,18)[0]!=183:
     raise ValueError('Expected AArch64 ELF userspace only')
    if member.mode not in (0o644,0o755):raise ValueError('Unexpected userspace permission mode')
   elif member.mode!=info['mode'] or member.mode not in (0o640,0o644) or data.startswith(b'\x7fELF'):
    raise ValueError('Executable or changed permissions in data-only archive')
   entries[name]=(data,member.mode,None);regular.add(name)
 if regular!=set(expected):raise ValueError('Incomplete manifested files')
 if not userspace and {n for n,v in entries.items() if v[2] is not None}!=set(links):
  raise ValueError('Incomplete path mappings')
 return entries

def assemble(userspace,data,userspace_manifest,data_manifest,output):
 if digest(userspace)!=userspace_manifest['archive_sha256'] or digest(data)!=data_manifest['archive_sha256']:
  raise ValueError('Archive identity mismatch')
 if userspace_manifest['scope']!='OFFLINE_ARM64_USERSPACE_ONLY_NOT_PHONE_ACCEPTANCE':
  raise ValueError('Userspace is not the separately reviewed generic artifact')
 if not userspace_manifest['reference_rom_excluded'] or not userspace_manifest['package_hooks_excluded']:
  raise ValueError('Generic runtime separation missing')
 if not data_manifest['original_importer_unchanged'] or not data_manifest['no_executable_or_service_included']:
  raise ValueError('Private phone data provenance missing')
 first=read_archive(userspace,userspace_manifest,True);second=read_archive(data,data_manifest,False)
 if first.keys()&second.keys():raise ValueError('Runtime/data path collision')
 entries={**first,**second};output=Path(output)
 with tarfile.open(output,'x:gz') as tf:
  for name,(value,mode,target) in sorted(entries.items()):
   item=tarfile.TarInfo(name);item.mode=mode;item.mtime=0
   if target is not None:item.type=tarfile.SYMTYPE;item.linkname=target;tf.addfile(item)
   else:item.size=len(value);tf.addfile(item,io.BytesIO(value))
 return {'scope':'PRIVATE_PHONE_SENSOR_RAM_RUNTIME_PREPARATION_ONLY','archive_sha256':digest(output),
  'archive_bytes':output.stat().st_size,'userspace_regular_files':sum(v[0] is not None for v in first.values()),
  'phone_data_regular_files':sum(v[0] is not None for v in second.values()),'mapping_links':sum(v[2] is not None for v in second.values()),
  'phone_operations':0,'firmware_or_modules_included':False,'service_or_udev_hooks_included':False,
  'mount_matrix_or_measured_sensor_claims_included':False,'sensor_attach_or_stream_eligible':False,'contains_private_unit_data':True}

def main():
 p=argparse.ArgumentParser();p.add_argument('--userspace',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
 p.add_argument('--userspace-manifest',type=Path,required=True);p.add_argument('--data-manifest',type=Path,required=True)
 p.add_argument('--output',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 result=assemble(a.userspace,a.data,json.loads(a.userspace_manifest.read_text()),json.loads(a.data_manifest.read_text()),a.output)
 a.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
