#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded isolated SSC guard checks or explicitly selected metadata discovery."""
import argparse,hashlib,json,os,re,stat,struct,subprocess,tarfile
from pathlib import Path

STATE=Path('/run/oneplus13/ssc-metadata')
NAMES={'probe/phone-ssc-metadata-probe','probe/libssc.so.2',
       'tests/phone-probe-policy','tests/phone-probe-send-guard',
       'probe/libprotobuf-c.so.1','probe/libqmi-glib.so.5','probe/libqrtr-glib.so.0','probe/libmbim-glib.so.4'}
RUNTIME_LIBRARIES={'libprotobuf-c.so.1','libqmi-glib.so.5','libqrtr-glib.so.0','libmbim-glib.so.4'}
TYPES={'accel','ambient_light','gyro','mag'}
REASONS={'discovery_timeout','ssc_service_lookup','sensor_service_unavailable',
         'metadata_policy_refused','cancelled','g_io_error','qmi_error',
         'qmi_protocol_error','ssc_client_other','ssc_sensor_other','unlisted_error_domain'}

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def entries(archive,manifest):
    if manifest.get('scope')!='PUBLIC_PHONE_SSC_METADATA_ONLY_PROBE' or set(manifest.get('files',{}))!=NAMES:
        raise ValueError('Wrong metadata probe scope')
    if (manifest.get('one_way_send_guard') is not True or manifest.get('samples_or_streams_allowed') is not False
        or manifest.get('private_inputs_included') is not False or manifest.get('hardware_qualified') is not False):
        raise ValueError('Unreviewed probe capability')
    if sha(archive)!=manifest['archive_sha256']:raise ValueError('Probe archive changed')
    if set(manifest.get('runtime_libraries',{}))!=RUNTIME_LIBRARIES:raise ValueError('Incomplete runtime library closure')
    result={};total=0
    with tarfile.open(archive) as tf:
        for member in tf:
            if member.name not in NAMES or member.name in result or not member.isfile() or member.mode!=0o755:
                raise ValueError('Unexpected probe member')
            info=manifest['files'][member.name]
            total+=member.size
            if not 20<=member.size<=8*1024*1024 or total>16*1024*1024 or member.size!=info['bytes'] or info['mode']!=0o755:
                raise ValueError('Probe member bound/mode mismatch')
            value=tf.extractfile(member).read()
            if (value[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',value,18)[0]!=183
                or hashlib.sha256(value).hexdigest()!=info['sha256']):raise ValueError('Probe ELF mismatch')
            result[member.name]=value
    if set(result)!=NAMES:raise ValueError('Incomplete probe')
    return result

def command(mode,state=STATE):
    if mode not in ('verify','discover'):raise ValueError('Unknown probe mode')
    unit='oneplus13-ssc-metadata-'+mode+'.service'
    properties=['Type=exec','User=65534','Group=65534','NoNewPrivileges=yes','CapabilityBoundingSet=',
       'ProtectSystem=strict','ProtectHome=yes','PrivateTmp=yes','PrivateDevices=yes','DevicePolicy=closed',
       'ProtectKernelTunables=yes','ProtectKernelModules=yes','ProtectControlGroups=yes',
       'RestrictSUIDSGID=yes','LockPersonality=yes','RestrictRealtime=yes',
       'KillMode=control-group','MemoryMax=128M','TasksMax=16','RuntimeMaxSec=20s','TimeoutStopSec=2s',
       'PrivateNetwork='+('yes' if mode=='verify' else 'no'),
       'RestrictAddressFamilies='+('AF_UNIX' if mode=='verify' else 'AF_UNIX AF_QIPCRTR')]
    # Retain failed units until their execution status has been inspected.
    args=['/usr/bin/systemd-run','--quiet','--wait','--pipe','--unit='+unit,
          '--setenv=LD_LIBRARY_PATH='+str(state/'probe'),*['--property='+x for x in properties]]
    if mode=='verify':
        args+=['/bin/sh','-eu','-c','"$1" --verify-lock; "$2"; "$3"','--',
               str(state/'probe/phone-ssc-metadata-probe'),str(state/'tests/phone-probe-policy'),
               str(state/'tests/phone-probe-send-guard')]
    else:args+=[str(state/'probe/phone-ssc-metadata-probe')]
    return unit,args

def diagnostics(output):
    if len(output)>32768:raise ValueError('Oversized probe output')
    rows=re.findall(r'^SSC_PHONE_METADATA_DIAGNOSTIC type=([a-z_]+) reason=([a-z_]+)$',output,re.M)
    if any(x[0] not in TYPES|{'all'} for x in rows):raise ValueError('Unexpected diagnostic type')
    if any(x[1] not in REASONS for x in rows):raise ValueError('Unexpected diagnostic reason')
    if len({x[0] for x in rows})!=len(rows):raise ValueError('Duplicate diagnostic type')
    return {name:reason for name,reason in rows}

def discovery_stages(output):
    """Bounded enum/count evidence; UIDs and arbitrary vendor text stay private."""
    if len(output)>32768:raise ValueError('Oversized probe output')
    lookups=re.findall(r'^SSC_PHONE_LOOKUP type=([a-z_]+) default=([01]) count=([0-9]{1,6})$',output,re.M)
    attrs=re.findall(r'^SSC_PHONE_ATTRIBUTES type=([a-z_]+) candidate=([0-9]{1,2}) available=([01]) matrix=([01])$',output,re.M)
    if len(lookups)>8 or len(attrs)>64:raise ValueError('Discovery diagnostic bound exceeded')
    if any(t not in TYPES or int(n)>65536 for t,d,n in lookups):raise ValueError('Unrecognized discovery type/count')
    if any(t not in TYPES or not 1<=int(n)<=16 for t,n,a,m in attrs):raise ValueError('Unrecognized attribute diagnostic')
    return {'lookups':[{'type':t,'default_only':d=='1','uid_count':int(n)} for t,d,n in lookups],
            'attributes':[{'type':t,'candidate':int(n),'available':a=='1','mount_matrix_present':m=='1'} for t,n,a,m in attrs]}

def execution_status(unit,run):
    fields={'LoadState':{'loaded','not-found','error','masked'},
            'ActiveState':{'active','inactive','failed','activating','deactivating'},
            'SubState':{'dead','failed','running','exited','start','stop','stop-sigterm','stop-sigkill'},
            'Result':{'success','exit-code','signal','core-dump','timeout','resources','protocol',
                      'start-limit-hit','oom-kill','exec-condition','assert','condition'},
            'ExecMainCode':None,'ExecMainStatus':None}
    try:
        result=run(['/usr/bin/systemctl','show',unit,*['--property='+n for n in fields]],
                   capture_output=True,text=True,timeout=2)
    except (OSError,subprocess.TimeoutExpired):return {'availability':'query_failed'}
    if result.returncode or len(result.stdout)>4096:return {'availability':'unit_unavailable'}
    facts={}
    for line in result.stdout.splitlines():
        name,sep,value=line.partition('=')
        if not sep or name not in fields or name in facts:return {'availability':'unrecognized_status'}
        if fields[name] is None:
            if not re.fullmatch(r'[0-9]{1,3}',value):return {'availability':'unrecognized_status'}
            facts[name]=int(value)
        elif value in fields[name]:facts[name]=value
        else:return {'availability':'unrecognized_status'}
    if set(facts)!=set(fields):return {'availability':'incomplete_status'}
    if facts['LoadState']=='not-found':return {'availability':'unit_unavailable'}
    return {'availability':'observed',**facts}

def save_private_failure(state,result):
    # Only an installed RAM state directory permits local raw evidence. Never
    # include these bytes in stdout or exception text; the host may copy them
    # into private records before automatic recovery.
    if not state.exists():return {'saved':False}
    if state.is_symlink() or not state.is_dir() or state.stat().st_uid!=0 or state.stat().st_mode&0o022:
        raise ValueError('Unsafe private diagnostic ancestry')
    target=state/'failure-private';target.mkdir(mode=0o700);target.chmod(0o700)
    facts={'saved':True,'files':{}}
    for name,value in [('stdout',result.stdout),('stderr',result.stderr)]:
        data=value.encode('utf-8',errors='replace');bounded=data[:32768]
        fd=os.open(target/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(bounded)
        facts['files'][name]={'bytes_saved':len(bounded),'truncated':len(data)>len(bounded),
                              'sha256':hashlib.sha256(bounded).hexdigest()}
    return facts

def interpret(mode,output):
    if len(output)>32768:raise ValueError('Oversized probe output')
    if mode=='verify':
        for marker in ('SSC_PHONE_METADATA_LOCK_VERIFIED_NO_QRTR','ok 6 /phone/06-one-way-lock',
                       'PHONE_METADATA_SEND_GUARD_BLOCKED_ALL_UNREVIEWED_REQUESTS_BEFORE_QMI'):
            if output.count(marker)!=1:raise ValueError('Missing native guard result')
        return {'native_guard_checks_passed':3,'qrtr_query_requested':False}
    rows=re.findall(r'^SSC_PHONE_METADATA type=(\w+) available=(yes|no) completed=(yes|no)$',output,re.M)
    if len(rows)!=4 or {r[0] for r in rows}!=TYPES:raise ValueError('Incomplete metadata result inventory')
    final='SSC_PHONE_METADATA_RESULT complete=yes samples_requested=no cleanup_returned=yes'
    if output.count(final)!=1 or any(r[1:]!=('yes','yes') for r in rows):raise ValueError('Discovery or cleanup incomplete')
    return {'metadata_available':sorted(TYPES),'qrtr_query_requested':True,'cleanup_returned':True,
            'sample_data_or_axes_accepted':False,'dsp_release_acknowledgement_claimed':False}

def partial_inventory(output):
    """Expose only the four exact public result rows on a failed discovery."""
    if len(output)>32768:return {'availability':'oversized'}
    rows=re.findall(r'^SSC_PHONE_METADATA type=(\w+) available=(yes|no) completed=(yes|no)$',output,re.M)
    if len(rows)!=4 or {r[0] for r in rows}!=TYPES:
        return {'availability':'incomplete_or_unrecognized'}
    return {'availability':'observed', 'types':{
        name:{'available':available=='yes','completed':completed=='yes'}
        for name,available,completed in rows},'samples_requested':False,
        'all_types_accepted':all(r[1:]==('yes','yes') for r in rows)}

def run_probe(mode,state=STATE,run=subprocess.run):
    unit,args=command(mode,state)
    status=None
    failed=True
    try:
        result=run(args,capture_output=True,text=True,timeout=26)
        failed=bool(result.returncode)
        if result.returncode:status=execution_status(unit,run)
    finally:
        try:
            stopped=run(['/usr/bin/systemctl','stop',unit],capture_output=True,text=True,timeout=5)
            if failed:
                # Execution status is already captured. This clears only the
                # transient unit state; hardware stop ledgers are never reset.
                reset=run(['/usr/bin/systemctl','reset-failed',unit],capture_output=True,text=True,timeout=1)
                if reset.returncode not in (0,1,5):raise ValueError('SSC transient unit cleanup failed')
        finally:
            inactive=run(['/usr/bin/systemctl','is-active',unit],capture_output=True,text=True,timeout=4)
        if stopped.returncode not in (0,5) or inactive.returncode not in (3,4) or inactive.stdout.strip() not in ('inactive','unknown'):
            raise ValueError('SSC metadata unit did not stop cleanly')
    if result.returncode:
        if mode=='verify':detail=(result.stdout+result.stderr)[-1800:]
        else:
            private=save_private_failure(state,result)
            detail=json.dumps({'probe':diagnostics(result.stdout+result.stderr),
                               'discovery_stages':discovery_stages(result.stdout+result.stderr),
                               'metadata_inventory':partial_inventory(result.stdout),
                               'execution':status,'private_output':private},sort_keys=True)
        raise ValueError('SSC metadata process failed, exit='+str(result.returncode)+': '+detail)
    facts=interpret(mode,result.stdout+result.stderr)
    if mode=='discover':facts['discovery_stages']=discovery_stages(result.stdout+result.stderr)
    return {'scope':'BOUNDED_SSC_METADATA_ONLY','mode':mode,'uid':65534,'unit_inactive':True,
            'measurement_commands_allowed':False,'peripheral_hardware_qualification_implied':False,**facts}

def install(files,state=STATE):
    if state.exists() or state.is_symlink():raise ValueError('Preserve prior probe evidence')
    for parent in state.parents:
        if parent==Path('/'):break
        if (parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid!=0
            or parent.stat().st_mode&0o022 or not parent.stat().st_mode&stat.S_IXOTH):
            raise ValueError('Unexpected probe ancestry')
    state.mkdir(mode=0o755);state.chmod(0o755)
    for name,data in files.items():
        path=state/name
        path.parent.mkdir(mode=0o755,exist_ok=True);path.parent.chmod(0o755)
        with path.open('xb') as stream:stream.write(data)
        path.chmod(0o755)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['verify','discover'],required=True)
    for name in ('archive','manifest'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('archive-sha256','manifest-sha256'):p.add_argument('--'+name,required=True)
    a=p.parse_args()
    if os.geteuid()!=0:raise ValueError('Root installs the low-privilege probe')
    if a.manifest.stat().st_size>65536 or a.archive.stat().st_size>8*1024*1024:raise ValueError('Delivery bound exceeded')
    if sha(a.archive)!=a.archive_sha256 or sha(a.manifest)!=a.manifest_sha256:raise ValueError('Delivery hash mismatch')
    files=entries(a.archive,json.loads(a.manifest.read_text()))
    install(files)
    facts=run_probe(a.mode)
    print('RAM_SSC_METADATA_RESULT '+json.dumps(facts,sort_keys=True),flush=True)

if __name__=='__main__':main()
