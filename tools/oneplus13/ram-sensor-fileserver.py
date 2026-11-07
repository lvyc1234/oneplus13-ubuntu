#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded phone sensor reverse-file-server qualification, never a stream.

Reuse the reviewed original hexagonrpcd with private phone-only data in RAM.
No SSC/iio client, dynamic DSP ELF, service installation or ADSP restart.
"""
import argparse,hashlib,importlib.util,json,os,re,stat,struct,subprocess,tarfile,time
from pathlib import Path,PurePosixPath

PREFIX='usr/lib/erhai/sensors/'
DATA=('var/lib/erhai/sensors/','usr/share/erhai-sensors/hexagonrpcd/',
      'usr/share/oneplus13/sensor-rom-reference/')
LINKS={
 'usr/share/erhai-sensors/hexagonrpcd/sensors/registry':'/var/lib/erhai/sensors/persist/registry',
 'usr/share/erhai-sensors/hexagonrpcd/sensors/sns_reg_version':'/var/lib/erhai/sensors/persist/sns_reg_version',
 'usr/share/erhai-sensors/hexagonrpcd/socinfo':'/var/lib/erhai/sensors/socinfo'}
STATE='/run/oneplus13/sensor-fileserver'
UNIT='oneplus13-sensor-fileserver-qualification.service'
UID=65534

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def canonical(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or str(path)!=name:
        raise ValueError('Noncanonical archive path')
    return name

def archive_entries(archive,manifest):
    public=manifest.get('scope')=='PUBLIC_REVIEWED_PHONE_SENSOR_RUNTIME_INPUTS'
    if not public and manifest.get('scope')!='PRIVATE_PHONE_SENSOR_FILESERVER_INPUTS':raise ValueError('Wrong manifest scope')
    if public:
        if manifest.get('native_elf_count')!=10 or manifest.get('text_data_files')!=922 or manifest.get('owner_identifiers_included') is not False or sha(archive)!=manifest.get('archive_sha256'):
            raise ValueError('Unreviewed public sensor capability or archive')
        expected_native={'bin/hexagonrpcd','bin/ssccli','bin/phone-sensor-bus-check','libexec/iio-sensor-proxy',
            'lib/libssc.so.2','lib/libhexagonrpc.so.0.5','lib/libmbim-glib.so.4','lib/libprotobuf-c.so.1',
            'lib/libqmi-glib.so.5','lib/libqrtr-glib.so.0'}
        if {n.removeprefix(PREFIX) for n in manifest['files'] if n.startswith(PREFIX)}!=expected_native:
            raise ValueError('Unreviewed public native inventory')
    if manifest.get('links')!=LINKS:raise ValueError('Wrong path mappings')
    expected=manifest['files']
    if not 5<len(expected)<=1024:raise ValueError('Unexpected file count')
    entries={};total=0;binary_count=0
    with tarfile.open(archive) as tf:
        for member in tf:
            name=canonical(member.name)
            if name in entries:raise ValueError('Duplicate archive path')
            if member.issym():
                if LINKS.get(name)!=member.linkname:raise ValueError('Unexpected symlink')
                entries[name]=(None,member.mode,member.linkname);continue
            if not member.isfile() or name not in expected:raise ValueError('Unmanifested file or integration hook')
            binary=name.startswith(PREFIX)
            if not binary and not name.startswith(DATA):raise ValueError('Unreviewed destination')
            info=expected[name]
            limit=8*1024*1024 if public and name==PREFIX+'lib/libqmi-glib.so.5' else 4*1024*1024
            if member.size!=info['bytes'] or not 0<=member.size<=limit:raise ValueError('Invalid member size')
            value=tf.extractfile(member).read();total+=len(value)
            if total>16*1024*1024:raise ValueError('Archive too large')
            if hashlib.sha256(value).hexdigest()!=info['sha256'] or member.mode!=info['mode']:
                raise ValueError('Bytes or permissions changed')
            if binary:
                if member.mode not in (0o644,0o755) or value[:6]!=b'\x7fELF\x02\x01' or len(value)<20 or struct.unpack_from('<H',value,18)[0]!=183:
                    raise ValueError('Not reviewed ARM64 userspace')
                binary_count+=1
            elif member.mode not in (0o640,0o644) or value.startswith(b'\x7fELF'):
                raise ValueError('Executable phone data refused')
            entries[name]=(value,member.mode,None)
    if {n for n,v in entries.items() if v[0] is not None}!=set(expected) or {n for n,v in entries.items() if v[2] is not None}!=set(LINKS):
        raise ValueError('Incomplete archive')
    if binary_count!=(10 if public else 5):raise ValueError('Wrong reviewed userspace ELF count')
    if public and len(entries)!=935:raise ValueError('Incomplete public data allowlist')
    # No file/link may become another member's parent during extraction.
    for name in entries:
        if any(str(p) in entries for p in PurePosixPath(name).parents if str(p)!='.'):
            raise ValueError('Archive parent collision')
    return entries

def install(root,entries,chown=os.chown):
    root=Path(root).resolve()
    # Preflight every destination before creating any file; never overwrite an
    # existing target or follow a host filesystem link on the way to it.
    for name in entries:
        dest=root/name
        if dest.exists() or dest.is_symlink():raise ValueError('Runtime destination already exists')
        for parent in dest.parents:
            if parent==root:break
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError('Unexpected destination ancestry')
            if parent.exists() and not (parent.stat().st_mode&stat.S_IXOTH):
                raise ValueError('Unprivileged destination ancestor not traversable')
    def parents(dest):
        # The RAM delivery shell deliberately uses umask077. Explicitly set
        # traversable modes on NEW directories; never relax existing ones.
        relative=dest.parent.relative_to(root)
        current=root
        for component in relative.parts:
            current=current/component
            if not current.exists():
                current.mkdir(mode=0o755)
                current.chmod(0o755)
    for name,(value,mode,target) in entries.items():
        if value is None:continue
        dest=root/name;parents(dest)
        with dest.open('xb') as stream:stream.write(value)
        dest.chmod(mode)
        if not name.startswith(PREFIX):chown(dest,UID,UID)
    for name,(value,mode,target) in entries.items():
        if target is not None:
            dest=root/name;parents(dest);dest.symlink_to(target)

def trace_facts(text):
    # raw=ioctl keeps requests numeric across strace versions. Trace never
    # dereferences RPC arguments or records sensor samples.
    attach=[x for x in text.splitlines() if re.search(r'ioctl\(0x[0-9a-f]+, 0x5208,',x)]
    if len(attach)!=1 or not re.search(r'= 0(?:x0)?(?:\s|$)',attach[0]):
        raise ValueError('Missing single successful attach trace')
    opens=[x for x in text.splitlines() if 'openat(' in x and re.search(r'= [0-9]+(?:\s|$)',x)]
    # The original rpcd_builder's SENSORS_REGISTRY includes a trailing '/'.
    registry=any(re.search(r'/sensors/registry/?"',x) is not None for x in opens)
    config=any('/sensors/sns_reg.conf"' in x for x in opens)
    invoke=sum('ioctl(' in x and '0xc0105203,' in x and re.search(r'= 0(?:x0)?(?:\s|$)',x) is not None for x in text.splitlines())
    return {'attach_ioctl_trace_success':True,'attach_calls':1,'successful_invoke_calls_observed':invoke,
            'registry_mapping_open_observed':registry,'config_mapping_open_observed':config,
            'raw_rpc_arguments_or_samples_saved':False}

def service_command(trace,proxy_mode=False):
    props=['Type=exec','User=65534','Group=65534','Restart=no','RuntimeMaxSec=40s',
      'TimeoutStartSec=8s','TimeoutStopSec=12s','KillMode=control-group','SendSIGKILL=yes',
      'NoNewPrivileges=yes','CapabilityBoundingSet=','ProtectSystem=strict','ProtectHome=yes',
      'PrivateTmp=yes','PrivateNetwork=yes','ProtectKernelTunables=yes','ProtectKernelModules=yes',
      'ProtectKernelLogs=yes','ProtectControlGroups=yes','ProtectClock=yes','ProtectHostname=yes',
      'RestrictAddressFamilies=AF_UNIX','RestrictNamespaces=yes','RestrictRealtime=yes',
      'LockPersonality=yes','MemoryDenyWriteExecute=yes','DevicePolicy=closed',
      'DeviceAllow=/dev/fastrpc-adsp-secure rw','ReadWritePaths='+STATE,
      'StandardOutput=append:'+STATE+'/daemon.log','StandardError=append:'+STATE+'/daemon.log']
    if proxy_mode:props=[p.replace('RuntimeMaxSec=40s','RuntimeMaxSec=70s') for p in props]
    result=['/usr/bin/systemd-run','--quiet','--unit='+UNIT]
    result+=['--property='+p for p in props]
    result += [str(trace),'-f','-qq','-T','-s','160','-e','trace=openat,ioctl,close','-e','raw=ioctl',
      '-o',STATE+'/syscalls.log','/usr/lib/erhai/sensors/bin/hexagonrpcd',
      '-f','/dev/fastrpc-adsp-secure','-d','adsp','-s','-W',
      '-R','/usr/share/erhai-sensors/hexagonrpcd']
    return result

def execute(args,seconds=8):
    return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=seconds).strip()

def daemon_error_summary(log):
    lines=log.splitlines()
    writes=sum(bool(re.match(r'Tried to open .+ (?:for writing|in an unsupported mode)$',x)) for x in lines)
    errors=sum(bool(re.search(r'Could not|Unsupported|Refused request|Unable to|Error|failed|Unknown search directory',x,re.I)) for x in lines)
    missing_hashes=set()
    for line in lines:
        prefix='Could not open '
        suffix=': No such file or directory'
        if line.startswith(prefix) and line.endswith(suffix):
            path=line[len(prefix):-len(suffix)]
            if path:
                missing_hashes.add(hashlib.sha256(path.encode('utf-8')).hexdigest())
    return {'error_lines':errors,'write_refusals':writes,
            'missing_file_lookups':sum(x.startswith('Could not open ') and x.endswith(': No such file or directory') for x in lines),
            'missing_file_lookup_sha256':sorted(missing_hashes)[:64],
            'unsupported_or_refused_dispatches':sum(bool(re.search(r'Unsupported|Refused request',x,re.I)) for x in lines),
            'unknown_search_directories':sum(x.startswith('Unknown search directory ') for x in lines),
            'arbitrary_paths_or_file_contents_in_summary':False}

def serve(trace,run=execute,sleep=time.sleep,clock=time.monotonic,diagnostic=False,during_active=None,samples_requested=False,proxy_mode=False):
    window=55 if proxy_mode else 28
    started=False
    launch_failed=False
    exited_early=False
    status_query_failed=False
    pre_status=None
    callback_completed=False
    callback_error=None
    try:
        # A launch timeout can occur after systemd created the unit.
        started=True
        try:
            run(service_command(trace,proxy_mode=proxy_mode) if proxy_mode else service_command(trace))
        except (subprocess.CalledProcessError,OSError,subprocess.TimeoutExpired):
            launch_failed=True
        until=clock()+window
        while clock()<until:
            try:
                active=run(['/usr/bin/systemctl','is-active',UNIT])
            except subprocess.CalledProcessError:
                # systemctl returns nonzero for inactive/failed units.
                active='unknown'
                status_query_failed=True
            if active!='active':
                exited_early=True
                break
            # The original CLI supplies no callback. A separate source-bound
            # composite may run one explicitly selected bounded client here, while
            # retaining this same unit lifetime and unconditional stop path.
            if during_active is not None and not callback_completed:
                if launch_failed:raise ValueError('Cannot start callback after failed file server launch')
                try:
                    during_active()
                except Exception as error:
                    # Retain the original failure, stop the reverse service,
                    # then emit its derived facts before propagating it. A
                    # failed SSC client must not skip the only attach evidence.
                    callback_error=error
                    break
                callback_completed=True
                if clock()>until:raise ValueError('Metadata callback exceeded the observation window')
            sleep(min(1,max(0,until-clock())))
    finally:
        if started:
            try:
                pre_status=run(['/usr/bin/systemctl','show',UNIT,'-p','ActiveState','-p','SubState','-p','Result','-p','MainPID'])
            except (subprocess.CalledProcessError,OSError,subprocess.TimeoutExpired):
                status_query_failed=True
            finally:
                run(['/usr/bin/systemctl','stop',UNIT],16)
    status=run(['/usr/bin/systemctl','show',UNIT,'-p','ActiveState','-p','SubState','-p','Result','-p','MainPID'])
    facts=dict(x.split('=',1) for x in status.splitlines() if '=' in x)
    before=dict(x.split('=',1) for x in (pre_status or '').splitlines() if '=' in x)
    clean_stop=facts.get('ActiveState')=='inactive' and facts.get('MainPID')=='0' and facts.get('Result')=='success'
    log=Path(STATE+'/daemon.log').read_text(errors='replace')
    summary=daemon_error_summary(log)
    trace=Path(STATE+'/syscalls.log').read_text(errors='replace')
    try:
        result=trace_facts(trace);trace_failure=None
    except ValueError as exc:
        result=None;trace_failure=str(exc)
    # Save bounded derived facts even when a strict acceptance gate refuses
    # later. The complete trace was already captured on RAM; do not infer
    # attach or initial registry reads from an unrelated tail of that trace.
    print('RAM_SENSOR_FILESERVER_DIAGNOSTIC '+json.dumps({
       'file_server_cleanly_stopped':clean_stop,'service_launch_returned_error':launch_failed,
       'service_exited_during_observation':exited_early,
       'service_status_query_failed':status_query_failed,
       'pre_stop_service_state':{k:before.get(k) for k in ('ActiveState','SubState','Result','MainPID')},
       'daemon_errors':summary,
       'complete_local_trace_facts':result,'trace_qualification_error':trace_failure,
       'sensor_stream_requested':samples_requested,'acceptance_not_implied':True},sort_keys=True),flush=True)
    if not clean_stop:raise ValueError('File server stop not clean')
    if callback_error is not None:raise callback_error
    if launch_failed or exited_early or status_query_failed or before.get('ActiveState')!='active':
        raise ValueError('File server exited before the bounded observation window')
    if diagnostic:
        if trace_failure is not None:raise ValueError(trace_failure)
        if result['successful_invoke_calls_observed']<3:
            raise ValueError('No successful reverse RPC invokes observed')
        if summary['write_refusals']:
            raise ValueError('Reviewed RAM write backend still refused a write')
        classified=summary['missing_file_lookups']+summary['unsupported_or_refused_dispatches']+summary['unknown_search_directories']
        if summary['error_lines']!=classified:
            raise ValueError('Unclassified daemon error during diagnostic run')
        if summary['missing_file_lookups']>64 or summary['unsupported_or_refused_dispatches']>16 or summary['unknown_search_directories']:
            raise ValueError('Diagnostic error counts exceed reviewed bounds')
        return {**result,'bounded_observation_seconds':window,'file_server_cleanly_stopped':True,
                'file_server_accepted':False,'write_refusals':0,
                'diagnostic_missing_file_lookups':summary['missing_file_lookups'],
                'diagnostic_unsupported_dispatches':summary['unsupported_or_refused_dispatches'],
                'dsp_release_acknowledgement_observed':False}
    if summary['error_lines'] or summary['write_refusals']:
        raise ValueError('Reverse file service reported unsupported or failed operation')
    if trace_failure is not None:raise ValueError(trace_failure)
    if not result['registry_mapping_open_observed'] or not result['config_mapping_open_observed'] or result['successful_invoke_calls_observed']<3:
        raise ValueError('No successful phone configuration/registry reverse service evidence')
    return {**result,'bounded_observation_seconds':window,'file_server_cleanly_stopped':True,
            'file_server_accepted':True,'write_refusals':0,
            'dsp_release_acknowledgement_observed':False}

def main():
    parser=argparse.ArgumentParser()
    for name in ('release','module-sha256','policy-sha256','attach-helper','attach-helper-sha256',
                 'archive','archive-sha256','manifest','manifest-sha256','trace-tool','trace-sha256'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--diagnostic-ram-writes',action='store_true')
    a=parser.parse_args()
    try:
        if os.geteuid()!=0 or os.uname().release!=a.release:raise ValueError('Wrong root or kernel')
        for path,digest in ((a.attach_helper,a.attach_helper_sha256),(a.archive,a.archive_sha256),
                            (a.manifest,a.manifest_sha256),(a.trace_tool,a.trace_sha256)):
            if not re.fullmatch('[0-9a-f]{64}',digest) or Path(path).is_symlink() or sha(path)!=digest:
                raise ValueError('Runtime input identity mismatch')
        spec=importlib.util.spec_from_file_location('qualified_attach_guard',a.attach_helper)
        guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
        root=Path('/')
        guard.validate(root,a.release,a.module_sha256,a.policy_sha256)
        entries=archive_entries(a.archive,json.loads(Path(a.manifest).read_text()))
        install(root,entries)
        # Verify the unchanged private bytes before the only attachment.
        for name,(value,mode,target) in entries.items():
            if value is not None and sha(root/name)!=hashlib.sha256(value).hexdigest():raise ValueError('Installed bytes changed')
        guard.guard(root,guard.command)
        with (root/guard.MARKER).open('x') as marker:marker.write('ONE_FILESERVER_ATTACH_NO_STREAM_NO_RETRY\n')
        for unit in guard.UNITS:
            execute(['/usr/bin/systemctl','mask','--runtime',unit])
            if (root/'run/systemd/system'/unit).resolve()!=root/'dev/null':raise ValueError('Sensor mask ineffective')
        execute(['/usr/sbin/modprobe','--ignore-install','fastrpc'])
        execute(['/usr/bin/udevadm','settle','--timeout=5'])
        guard.device_ready(root);guard.guard(root,guard.command)
        os.chown('/dev/fastrpc-adsp-secure',UID,UID);os.chmod('/dev/fastrpc-adsp-secure',0o600)
        state=Path(STATE);state.mkdir(mode=0o700);os.chown(state,UID,UID)
        try:facts=serve(a.trace_tool,diagnostic=a.diagnostic_ram_writes)
        finally:
            # A refused RPC/file lookup still needs post-service protection
            # and exact-copy checks before the host requests recovery.
            guard.guard(root,guard.command)
            if sha(root/guard.POLICY)!=a.policy_sha256:raise ValueError('Module denial policy changed')
            for name,(value,mode,target) in entries.items():
                if value is not None and sha(root/name)!=hashlib.sha256(value).hexdigest():raise ValueError('Private data changed after read-only service')
            print('RAM_SENSOR_FILESERVER_POSTCONDITIONS '+json.dumps({
                'no_sensor_clients_remaining':True,'module_policy_unchanged':True,
                'private_data_bytes_unchanged':True,'acceptance_not_implied':True},sort_keys=True),flush=True)
        result={'scope':'BOUNDED_PRIVATE_PHONE_SENSOR_REVERSE_FILESERVER_ONLY','worker':facts,
                'diagnostic_ram_writes_only':a.diagnostic_ram_writes,
                'file_server_accepted':facts['file_server_accepted'],
                'sensor_stream_requested':False,'ssc_or_iio_client_started':False,'adsp_restarted':False,
                'module_unloaded':False,'firmware_or_kernel_replaced':False,'module_policy_unchanged':True,
                'private_data_bytes_unchanged':True,'data_files':len(entries)-len(LINKS)-5}
        print('RAM_SENSOR_FILESERVER_RESULT '+json.dumps(result,sort_keys=True),flush=True)
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError) as exc:
        reason=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
        print('RAM_SENSOR_FILESERVER_FAILED '+json.dumps({'gate':reason}),flush=True)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
