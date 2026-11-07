#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""One reviewed reverse service and an explicitly selected SSC diagnostic."""
import argparse,hashlib,importlib.util,json,os,re,subprocess,time
from pathlib import Path

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def load(name,path,digest):
    path=Path(path)
    if path.is_symlink() or not re.fullmatch('[0-9a-f]{64}',digest) or sha(path)!=digest:raise ValueError('Composite helper identity mismatch')
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def metadata_callback(fs,ssc,run=subprocess.run,clock=time.monotonic,sleep=time.sleep,sample_mode=False,proxy=None,proxy_manifest=None):
    # The probe is installed BEFORE the reverse-service window. Bound the
    # Query/status/stop/transient reset/state: 16+1+3+1+2s; <=3s readiness.
    # Keep the original independent 20s systemd cap and 28s service window.
    until=clock()+3
    while True:
        path=Path(fs.STATE+'/syscalls.log')
        try:
            with path.open('rb') as stream:data=stream.read(4*1024*1024+1)
            if len(data)>4*1024*1024:raise ValueError('Reverse trace bound exceeded')
            facts=fs.trace_facts(data.decode(errors='replace'))
            ready=(facts['attach_calls']==1 and facts['attach_ioctl_trace_success'] and
                   facts['config_mapping_open_observed'] and facts['successful_invoke_calls_observed']>=3)
        except FileNotFoundError:ready=False
        except ValueError:
            if clock()>=until:raise
            ready=False
        if ready:break
        if clock()>=until:raise ValueError('Reverse service was not ready for metadata queries')
        sleep(min(0.1,max(0,until-clock())))
    selected_unit='oneplus13-ssc-samples-sample.service' if sample_mode else 'oneplus13-ssc-metadata-discover.service'
    def bounded_run(command,**kwargs):
        if command[0]=='/usr/bin/systemd-run':limit=16
        elif command==['/usr/bin/systemctl','stop',selected_unit]:limit=3
        elif command==['/usr/bin/systemctl','is-active',selected_unit]:limit=2
        elif command[:3]==['/usr/bin/systemctl','show',selected_unit]:limit=1
        elif command==['/usr/bin/systemctl','reset-failed',selected_unit]:limit=1
        else:raise ValueError('Unreviewed metadata command')
        kwargs['timeout']=min(kwargs['timeout'],limit)
        return run(command,**kwargs)
    answer=ssc.run_probe('sample' if sample_mode else 'discover',run=bounded_run)
    if sample_mode:
        if answer.get('mode')!='sample' or set(answer.get('samples',{}))!={'accel','gyro','mag'} or answer.get('unit_inactive') is not True or answer.get('cleanup_returned') is not True:
            raise ValueError('Incomplete bounded sample result')
        if proxy is not None:
            print('RAM_SSC_PRE_PROXY_SAMPLES_RESULT '+json.dumps(answer,sort_keys=True),flush=True)
            answer['proxy']=proxy.run_proxy(proxy_manifest)
        return answer
    if (answer.get('mode')!='discover' or answer.get('metadata_available')!=sorted(ssc.TYPES) or
        answer.get('unit_inactive') is not True or answer.get('cleanup_returned') is not True or
        answer.get('measurement_commands_allowed') is not False or answer.get('sample_data_or_axes_accepted') is not False):
        raise ValueError('Unqualified metadata result')
    return answer

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--probe-mode',choices=['metadata','samples','proxy'],default='metadata')
    p.add_argument('--proxy-helper');p.add_argument('--proxy-helper-sha256')
    for name in ('release','module-sha256','policy-sha256','attach-helper','attach-helper-sha256',
                 'fileserver-helper','fileserver-helper-sha256','archive','archive-sha256','manifest','manifest-sha256',
                 'trace-tool','trace-sha256','ssc-helper','ssc-helper-sha256','ssc-archive','ssc-archive-sha256',
                 'ssc-manifest','ssc-manifest-sha256','ssc-samples-helper','ssc-samples-helper-sha256',
                 'ssc-samples-archive','ssc-samples-archive-sha256','ssc-samples-manifest',
                 'ssc-samples-manifest-sha256'):p.add_argument('--'+name,required=True)
    a=p.parse_args()
    if os.geteuid()!=0 or os.uname().release!=a.release:raise ValueError('Wrong running root/kernel')
    fs=load('reviewed_reverse_service',a.fileserver_helper,a.fileserver_helper_sha256)
    guard=load('reviewed_attach_guard',a.attach_helper,a.attach_helper_sha256)
    ssc=load('reviewed_metadata_guard',a.ssc_helper,a.ssc_helper_sha256)
    samples=load('reviewed_samples_guard',a.ssc_samples_helper,a.ssc_samples_helper_sha256)
    proxy=None
    if a.probe_mode=='proxy':
        if not a.proxy_helper or not a.proxy_helper_sha256:p.error('proxy mode requires its bound helper')
        proxy=load('reviewed_sensor_proxy',a.proxy_helper,a.proxy_helper_sha256)
    root=Path('/')
    guard.validate(root,a.release,a.module_sha256,a.policy_sha256)
    for path,digest in [(a.archive,a.archive_sha256),(a.manifest,a.manifest_sha256),
                        (a.trace_tool,a.trace_sha256),(a.ssc_archive,a.ssc_archive_sha256),
                        (a.ssc_manifest,a.ssc_manifest_sha256),(a.ssc_samples_archive,a.ssc_samples_archive_sha256),
                        (a.ssc_samples_manifest,a.ssc_samples_manifest_sha256)]:
        if Path(path).is_symlink() or not re.fullmatch('[0-9a-f]{64}',digest) or sha(path)!=digest:raise ValueError('Composite input mismatch')
    probe_files=ssc.entries(a.ssc_archive,json.loads(Path(a.ssc_manifest).read_text()))
    sample_files=samples.entries(a.ssc_samples_archive,json.loads(Path(a.ssc_samples_manifest).read_text()))
    runtime_manifest=json.loads(Path(a.manifest).read_text())
    files=fs.archive_entries(a.archive,runtime_manifest)
    if proxy is not None and runtime_manifest.get('scope')!='PUBLIC_REVIEWED_PHONE_SENSOR_RUNTIME_INPUTS':raise ValueError('Proxy requires freshly built public native inputs')
    fs.install(root,files)
    ssc.install(probe_files)
    samples.install(sample_files)
    for name,(data,mode,target) in files.items():
        if data is not None and sha(root/name)!=hashlib.sha256(data).hexdigest():raise ValueError('Reverse input changed before attach')
    guard.guard(root,guard.command)
    sample_mode=a.probe_mode in ('samples','proxy')
    diagnostic=samples if sample_mode else ssc
    with (root/guard.MARKER).open('x') as stream:stream.write('ONE_REVERSE_ATTACH_THREE_VECTOR_SAMPLE_DIAGNOSTIC\n' if sample_mode else 'ONE_REVERSE_ATTACH_FOUR_METADATA_QUERIES_NO_STREAM_NO_RETRY\n')
    for unit in guard.UNITS:
        fs.execute(['/usr/bin/systemctl','mask','--runtime',unit])
        if (root/'run/systemd/system'/unit).resolve()!=root/'dev/null':raise ValueError('Automatic client mask ineffective')
    fs.execute(['/usr/sbin/modprobe','--ignore-install','fastrpc'])
    fs.execute(['/usr/bin/udevadm','settle','--timeout=5'])
    guard.device_ready(root);guard.guard(root,guard.command)
    os.chown('/dev/fastrpc-adsp-secure',fs.UID,fs.UID);os.chmod('/dev/fastrpc-adsp-secure',0o600)
    state=Path(fs.STATE);state.mkdir(mode=0o700);os.chown(state,fs.UID,fs.UID)
    metadata=[]
    def callback():metadata.append(metadata_callback(fs,diagnostic,sample_mode=sample_mode,proxy=proxy,proxy_manifest=runtime_manifest))
    try:worker=fs.serve(a.trace_tool,diagnostic=True,during_active=callback,samples_requested=sample_mode,**({'proxy_mode':True} if proxy is not None else {}))
    finally:
        guard.guard(root,guard.command)
        if sha(root/guard.POLICY)!=a.policy_sha256:raise ValueError('Module policy changed')
        for name,(data,mode,target) in files.items():
            if data is not None and sha(root/name)!=hashlib.sha256(data).hexdigest():raise ValueError('Reverse input bytes changed')
        print('RAM_SENSOR_DISCOVERY_POSTCONDITIONS '+json.dumps({'private_inputs_unchanged':True,'module_policy_unchanged':True,
              'file_service_and_metadata_cleanup_required':True,'samples_accepted':False}),flush=True)
    if len(metadata)!=1:raise ValueError('Metadata callback count mismatch')
    result={'scope':'ONE_REVERSE_ATTACH_THREE_VECTOR_SAMPLES' if sample_mode else 'ONE_REVERSE_ATTACH_FOUR_METADATA_TYPES_NO_SAMPLES','worker':worker,'metadata':metadata[0],
      'private_data_bytes_unchanged':True,'module_policy_unchanged':True,'data_files':len(files)-len(fs.LINKS)-(10 if runtime_manifest.get('scope')=='PUBLIC_REVIEWED_PHONE_SENSOR_RUNTIME_INPUTS' else 5),
      'sensor_stream_requested':sample_mode,'audio_camera_or_radio_activated':False,
      'adsp_restarted':False,'dsp_release_acknowledgement_claimed':False,'file_server_fully_accepted':False}
    if proxy is not None:result['scope']='ONE_REVERSE_ATTACH_THREE_VECTOR_SAMPLES_AND_DBUS'
    print('RAM_SENSOR_DISCOVERY_RESULT '+json.dumps(result,sort_keys=True),flush=True)

if __name__=='__main__':main()
