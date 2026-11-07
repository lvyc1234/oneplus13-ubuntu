#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded synthetic RPC userspace checks, without a FastRPC device/client."""
import argparse,hashlib,json,os,stat,struct,subprocess,tarfile
from pathlib import Path

NAMES=('test_apps_std_ram','test_phone_ramfs')
STATE=Path('/run/oneplus13/rpc-mock-selftest')
UNIT='oneplus13-rpc-mock-selftest.service'
MARKERS=('APPS_STD_RAM_WIRE_OPEN_READ_WRITE_SEEK_FLUSH_CLOSE_REJECT_CLEANUP_PASS',
         'RAMFS_DEFAULT_OFF_EXACT_NAMES_CURSORS_CAPS_ENOMEM_STALE_GENERATION_CLEANUP_PASS')

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def archive_entries(archive,manifest):
    if manifest.get('scope')!='PUBLIC_ARM64_RPC_MOCK_SELFTESTS' or set(manifest.get('files',{}))!=set(NAMES):
        raise ValueError('Wrong selftest inventory')
    if manifest.get('synthetic_data_only') is not True or manifest.get('fastrpc_call_mocked_to_fail') is not True or manifest.get('hardware_qualification_implied') is not False:
        raise ValueError('Unreviewed test scope')
    if sha(archive)!=manifest['archive_sha256']:raise ValueError('Selftest archive changed')
    entries={}
    with tarfile.open(archive) as tf:
        for member in tf:
            if member.name not in NAMES or member.name in entries or not member.isfile() or member.mode!=0o755:
                raise ValueError('Unexpected selftest archive member')
            info=manifest['files'][member.name]
            if not 20<=member.size<=1024*1024 or member.size!=info['bytes'] or info['mode']!=0o755:
                raise ValueError('Invalid selftest size or mode')
            data=tf.extractfile(member).read()
            if data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183 or hashlib.sha256(data).hexdigest()!=info['sha256']:
                raise ValueError('Selftest ELF/hash mismatch')
            entries[member.name]=data
    if set(entries)!=set(NAMES):raise ValueError('Incomplete selftest archive')
    return entries

def command(state=STATE):
    properties=['Type=exec','User=65534','Group=65534','NoNewPrivileges=yes',
      'CapabilityBoundingSet=','ProtectSystem=strict','ProtectHome=yes','PrivateTmp=yes',
      'PrivateDevices=yes','DevicePolicy=closed','PrivateNetwork=yes',
      'RestrictAddressFamilies=AF_UNIX','ProtectKernelTunables=yes','ProtectKernelModules=yes',
      'ProtectControlGroups=yes','RuntimeMaxSec=12s','TimeoutStopSec=3s',
      'KillMode=control-group','MemoryMax=96M','TasksMax=8','ReadWritePaths='+str(state)]
    return ['/usr/bin/systemd-run','--quiet','--wait','--pipe','--collect','--unit='+UNIT,
            *['--property='+p for p in properties],'/bin/sh','-eu','-c','"$1" "$2"; "$3"',
            '--',str(state/NAMES[0]),str(state/'fixture'),str(state/NAMES[1])]

def run_tests(state=STATE,run=subprocess.run):
    try:
        result=run(command(state),capture_output=True,text=True,timeout=18)
    finally:
        # This also covers a launch timeout after the unit was created.
        run(['/usr/bin/systemctl','stop',UNIT],capture_output=True,text=True,timeout=5)
    status=run(['/usr/bin/systemctl','is-active',UNIT],capture_output=True,text=True,timeout=4)
    if status.returncode not in (3,4) or status.stdout.strip() not in ('inactive','unknown'):
        raise ValueError('Synthetic selftest unit still active or unqualified')
    output=result.stdout+result.stderr
    if len(output)>32768 or result.returncode or any(output.count(x)!=1 for x in MARKERS):
        raise ValueError('Synthetic userspace checks failed')
    print(output,end='' if output.endswith('\n') else '\n')
    return {'scope':'SYNTHETIC_RPC_USERSPACE_CHECKS_ONLY','checks_passed':2,
            'unit_inactive':True,'uid':65534,'devices_hidden_and_denied':True,
            'phone_data_used':False,'fastrpc_client_or_sensor_stream_requested':False,
            'hardware_peripheral_qualification_implied':False}

def main():
    p=argparse.ArgumentParser()
    for name in ['archive','manifest']:p.add_argument('--'+name,type=Path,required=True)
    for name in ['archive-sha256','manifest-sha256']:p.add_argument('--'+name,required=True)
    a=p.parse_args()
    if os.geteuid()!=0:raise ValueError('Root must install the sandbox fixture')
    if a.manifest.stat().st_size>65536 or a.archive.stat().st_size>2*1024*1024:raise ValueError('Oversized selftest delivery')
    if sha(a.archive)!=a.archive_sha256 or sha(a.manifest)!=a.manifest_sha256:raise ValueError('Delivery hash mismatch')
    manifest=json.loads(a.manifest.read_text())
    entries=archive_entries(a.archive,manifest)
    if STATE.exists() or STATE.is_symlink():raise ValueError('Preserve existing selftest directory')
    for parent in STATE.parents:
        if parent==Path('/'):break
        if parent.is_symlink() or not parent.is_dir() or not parent.stat().st_mode&stat.S_IXOTH:
            raise ValueError('Unexpected or inaccessible selftest ancestry')
    STATE.mkdir(mode=0o700);os.chown(STATE,65534,65534)
    for name,data in entries.items():
        path=STATE/name
        with path.open('xb') as stream:stream.write(data)
        path.chmod(0o755)
    result=run_tests()
    print('RAM_RPC_MOCK_SELFTEST_RESULT '+json.dumps(result,sort_keys=True),flush=True)

if __name__=='__main__':main()
