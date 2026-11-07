#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""One secure ADSP SNS attach ioctl and immediate local close, no clients.

Explicitly bound optional experiment, never an installation or sensor service.
Kernel attach and release RPCs each have a 10-second timeout in the reviewed
source. The kernel ignores release RPC status: local close is NOT a DSP release
acknowledgement. No retry, unload, ADSP restart, reverse server or stream.
"""
import argparse, hashlib, json, os, re, stat, subprocess, sys, time
from pathlib import Path

POLICY='etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf'
MARKER='run/oneplus13/sensor-attach-only-attempted'
NODE='dev/fastrpc-adsp-secure'
OF_NODE='soc@0/remoteproc@6800000/glink-edge/fastrpc'
UNITS=('iio-sensor-proxy.service','hexagonrpcd.service','erhai-sensors.service',
       'erhai-hexagonrpcd.service','hexagonrpcd-adsp.service')


def command(args):
    return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=8).strip()


def guard(root,run):
    if not (root/'run/oneplus13/ram-session').is_file():raise ValueError('RAM marker missing')
    if not any(len(p:=line.split())>=3 and p[1:3]==['/','overlay']
               for line in (root/'proc/mounts').read_text().splitlines()):raise ValueError('RAM overlay missing')
    blocks=[p for p in (root/'sys/class/block').iterdir() if not p.name.startswith(('ram','zram','loop'))]
    if not blocks or any((p/'ro').read_text().strip()!='1' for p in blocks):raise ValueError('Storage protection missing')
    for unit in ('oneplus13-ram-guard.service','oneplus13-ram-deadline.timer'):
        if run(['/usr/bin/systemctl','is-active',unit])!='active':raise ValueError('Recovery inactive')
    for path in ('sys/class/bluetooth','sys/class/ieee80211'):
        if (root/path).exists() and any((root/path).iterdir()):raise ValueError('Unexpected active radio')
    if (root/'sys/module/snd_soc_sc8280xp').exists():raise ValueError('Unexpected sound activation')
    for entry in (root/'proc').glob('[0-9]*/cmdline'):
        try:args=entry.read_bytes().split(b'\0')
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
        # Compare executables, not arbitrary command arguments or this helper.
        if any(Path(arg.decode(errors='replace')).name in ('ssccli','hexagonrpcd','iio-sensor-proxy')
               for arg in args[:1]):raise ValueError('Sensor client already running')


def validate(root,release,digest,policy_sha,run=command):
    root=Path(root)
    if not re.fullmatch(r'[A-Za-z0-9._+-]{1,160}',release) or not all(re.fullmatch('[0-9a-f]{64}',x) for x in (digest,policy_sha)):
        raise ValueError('Invalid identities')
    guard(root,run)
    policy=root/POLICY
    if policy.is_symlink() or hashlib.sha256(policy.read_bytes()).hexdigest()!=policy_sha:raise ValueError('Policy changed')
    rules=[x.strip() for x in run(['/usr/sbin/modprobe','-c']).splitlines() if x.strip().startswith('install fastrpc ')]
    if rules!=['install fastrpc /bin/false']:raise ValueError('Exact FastRPC denial required')
    module=root/('usr/lib/modules/'+release+'/kernel/drivers/misc/fastrpc.ko')
    if module.is_symlink() or module.stat().st_size>1024*1024 or hashlib.sha256(module.read_bytes()).hexdigest()!=digest:
        raise ValueError('Module identity changed')
    if not run(['/usr/sbin/modinfo','-k',release,'-F','vermagic','fastrpc']).startswith(release+' '):raise ValueError('Module release mismatch')
    if run(['/usr/sbin/modinfo','-k',release,'-F','depends','fastrpc']):raise ValueError('Unreviewed dependency')
    if (root/'sys/module/fastrpc').exists() or (root/NODE).exists():raise ValueError('FastRPC already present')
    # Reject every automatic FastRPC hook; masking just one known unit is not
    # enough when an unknown udev RUN command could attach a second client.
    for directory in ('usr/lib/udev/rules.d','lib/udev/rules.d','etc/udev/rules.d','run/udev/rules.d'):
        for rule in (root/directory).glob('*.rules'):
            value=rule.read_text(errors='replace')
            if any(re.search(r'fastrpc|hexagonrpc|ssccli',line,re.I)
                   for line in value.splitlines() if not line.lstrip().startswith('#')):
                raise ValueError('Automatic FastRPC udev rule present')
    return module


def device_ready(root,node_stat=None):
    path=root/NODE
    info=path.stat() if node_stat is None else node_stat
    if path.is_symlink() or not stat.S_ISCHR(info.st_mode):raise ValueError('Expected secure ADSP character node')
    base=root/'sys/class/misc/fastrpc-adsp-secure'
    if (base/'dev').read_text().strip()!=f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}':raise ValueError('Device number mismatch')
    # This pinned driver's misc_register() does not set miscdev.parent. There
    # is deliberately no misc/device/of_node link. Verify the independently
    # bound unique RPMSG channel, rather than inventing a misc ancestry.
    if sorted(p.name for p in (root/'dev').glob('fastrpc-*'))!=['fastrpc-adsp-secure']:
        raise ValueError('Unexpected FastRPC domain device')
    channels=[]
    for entry in (root/'sys/bus/rpmsg/devices').iterdir():
        if (entry/'driver').resolve().name=='qcom,fastrpc':channels.append(entry)
    if len(channels)!=1:raise ValueError('Expected one bound FastRPC channel')
    expected=root/'sys/firmware/devicetree/base'/OF_NODE
    if (channels[0]/'of_node').resolve()!=expected.resolve() or (expected/'label').read_bytes()!=b'adsp\0':
        raise ValueError('Wrong bound ADSP channel')
    if (base/'device/of_node').exists() and (base/'device/of_node').resolve()!=expected.resolve():
        raise ValueError('Unexpected conflicting misc ancestry')


def worker(open_fd=os.open,ioctl=None,close_fd=os.close,clock=time.monotonic):
    if ioctl is None:
        import fcntl
        ioctl=fcntl.ioctl
    started=clock(); fd=open_fd('/'+NODE,os.O_RDWR|os.O_CLOEXEC)
    rc=None;error=None
    try:
        try:rc=ioctl(fd,0x5208,0)
        except OSError as exc:error=exc.errno
    finally:
        close_fd(fd)
    return {'ioctl':0x5208,'attach_return':rc,'attach_errno':error,'local_fd_closed':True,
        'elapsed_seconds':round(clock()-started,3),'dsp_release_acknowledgement_observed':False,
        'client_or_listener_started':False,'sensor_stream_requested':False}


def actual_worker():
    root=Path('/')
    if not (root/MARKER).is_file():raise ValueError('No prior single-attempt marker')
    guard(root,command);device_ready(root)
    result=worker()
    print('RAM_SENSOR_ATTACH_WORKER '+json.dumps(result,sort_keys=True),flush=True)
    return 0 if result['attach_return']==0 and result['attach_errno'] is None else 1


def invoke_worker():
    proc=subprocess.Popen([sys.executable,__file__,'--internal-worker'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:output,_=proc.communicate(timeout=24)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:proc.communicate(timeout=12)
        except subprocess.TimeoutExpired:proc.kill();raise RuntimeError('Sensor worker exit timeout; immediate recovery required')
        raise RuntimeError('Sensor worker exceeded reviewed deadline')
    matches=re.findall(r'^RAM_SENSOR_ATTACH_WORKER (\{.*\})$',output,re.M)
    if len(matches)!=1:raise ValueError('Missing exact ioctl result')
    result=json.loads(matches[0])
    if proc.returncode!=0 or result.get('attach_return')!=0 or result.get('attach_errno') is not None:
        print('RAM_SENSOR_ATTACH_WORKER_FAILURE '+json.dumps(result,sort_keys=True),flush=True)
        raise ValueError('First attach failed; no retry')
    if not result.get('local_fd_closed') or result.get('elapsed_seconds',99)>22:
        raise ValueError('Local close or elapsed gate failed')
    return result


def prepare(root,release,digest,policy_sha,run=command,probe=invoke_worker,ready=device_ready):
    root=Path(root)
    if run is command and (root!=Path('/') or os.uname().release!=release):raise ValueError('Wrong running kernel/root')
    validate(root,release,digest,policy_sha,run)
    with (root/MARKER).open('x') as out:out.write('ONE_ATTEMPT_NO_RETRY\n')
    for unit in UNITS:
        run(['/usr/bin/systemctl','mask','--runtime',unit])
        if (root/'run/systemd/system'/unit).resolve()!=root/'dev/null':raise ValueError('Runtime mask not effective')
    # Remain denied to normal modprobe requests; only this explicit invocation
    # bypasses its install rule after hash/dependency/recovery qualification.
    run(['/usr/sbin/modprobe','--ignore-install','fastrpc'])
    run(['/usr/bin/udevadm','settle','--timeout=5'])
    if not (root/'sys/module/fastrpc').is_dir():raise ValueError('FastRPC not registered')
    ready(root);guard(root,run)
    result=probe()
    if hashlib.sha256((root/POLICY).read_bytes()).hexdigest()!=policy_sha:raise ValueError('Peripheral policy mutated')
    guard(root,run)
    return {'scope':'ONE_ADSP_SNS_ATTACH_IOCTL_AND_LOCAL_CLOSE_ONLY','module_sha256':digest,
        'module_policy_unchanged':True,'attempts':1,'worker':result,
        'reverse_rpc_or_registry_validated':False,'sensor_data_or_axes_validated':False,
        'adsp_restart_or_module_unload_requested':False,'automatic_sensor_clients_masked':True}


def main():
    if sys.argv[1:]==['--internal-worker']:
        if os.geteuid()!=0:raise ValueError('Root required')
        return actual_worker()
    p=argparse.ArgumentParser()
    for flag in ('release','sha256','policy-sha256'):p.add_argument('--'+flag,required=True)
    a=p.parse_args()
    try:
        if os.geteuid()!=0:raise ValueError('Root required')
        result=prepare(Path('/'),a.release,a.sha256,a.policy_sha256)
        print('RAM_SENSOR_ATTACH_RESULT '+json.dumps(result,sort_keys=True),flush=True)
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError) as exc:
        # Qualification messages are fixed strings; retain the failed gate,
        # without printing paths, command output or arbitrary OSError details.
        reason=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
        print('RAM_SENSOR_ATTACH_FAILED '+json.dumps({'type':type(exc).__name__,'gate':reason}),flush=True)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
