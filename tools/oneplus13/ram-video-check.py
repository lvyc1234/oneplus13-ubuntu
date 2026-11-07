#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""One manual phone Iris/AVC frame with source, RAM and teardown checks."""
import argparse, hashlib, importlib.util, json, os, re, stat, subprocess, time
from pathlib import Path

STATE=Path('/run/oneplus13/video')
BINDINGS=Path('/usr/lib/oneplus13-video/runtime-bindings.json')
POLICY=Path('/etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf')
TARGET='/soc@0/video-codec@aa00000'
FIXTURE_SHA='9abde2249b25b41fdd959f5d94901a0c865c5be2d242617991a77875cf3cb4d0'


def validate_awake_guard(receipt,now):
    if (receipt.get('scope')!='ONE_MANUAL_RAM_GUNYAH_AWAKE_VIDEO_GUARD' or
        receipt.get('active_wait_required') is not True or
        receipt.get('idle_or_suspend_recovery_qualified') is not False or
        receipt.get('guard_running') is not True or receipt.get('guard_seconds')!=90 or
        any(receipt.get(k) is not True for k in ('armed','countdown_verified','keepalive_verified')) or
        any(receipt.get(k) is not False for k in ('suspend_requested','reset_observed'))):
        raise ValueError('Missing qualified awake video guard')
    deadline=receipt.get('guard_deadline_monotonic')
    if type(deadline) not in (int,float) or not 45<deadline-now<=90:
        raise ValueError('Insufficient fixed awake guard window')
    values={k:receipt.get(k) for k in ('set_timeout_readback','get_timeout_readback',
                                     'first_timeleft','second_timeleft','third_timeleft')}
    if (any(type(v) is not int for v in values.values()) or
        values['set_timeout_readback']!=12 or values['get_timeout_readback']!=12 or
        not 0<values['second_timeleft']<values['first_timeleft']<=12 or
        not values['second_timeleft']<values['third_timeleft']<=12):
        raise ValueError('Awake guard ioctl gates failed')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_bytes(value):
    if (value['format']!='ONEPLUS13_SYNTHETIC_AVC_BARS_V1' or
        value['width']!=320 or value['height']!=240 or value['frames']!=1 or
        value['capture_input'] is not False or value['annex_b'] is not True):
        raise ValueError('Wrong synthetic video fixture')
    raw=bytes.fromhex(value['hex'])
    if len(raw)!=905 or value['bytes']!=905 or value['sha256']!=FIXTURE_SHA or hashlib.sha256(raw).hexdigest()!=FIXTURE_SHA:
        raise ValueError('Changed synthetic AVC bytes')
    return raw


def parse_frame(text):
    rows=re.findall(r'^IRIS_FRAME_RESULT (\{.*\})\s*$',text,re.M)
    if len(rows)!=1:raise ValueError('Missing unique decoded-frame evidence')
    result=json.loads(rows[0])
    if (result.get('frame_verified') is not True or result.get('streams_stopped') is not True or
        result.get('software_fallback') is not False or result.get('frame_width')!=320 or
        result.get('frame_height')!=240 or 'IRIS_FIRMWARE_SESSION_OPENED' not in text):
        raise ValueError('Hardware frame or stream teardown failed')
    return result


def decoder_nodes():
    nodes=[]
    for path in Path('/sys/class/video4linux').glob('video*'):
        device=path/'device'
        if (path/'name').read_text().strip()=='qcom-iris-decoder' and (device/'of_node').exists() and str((device/'of_node').resolve()).endswith(TARGET):
            node=Path('/dev')/path.name
            major,minor=map(int,(path/'dev').read_text().strip().split(':'))
            metadata=node.stat()
            if not stat.S_ISCHR(metadata.st_mode) or metadata.st_rdev!=os.makedev(major,minor):
                raise ValueError('Video node/device mismatch')
            if (device/'driver').resolve().name!='qcom-iris':raise ValueError('Wrong video driver')
            nodes.append((node,device))
    if len(nodes)>1:raise ValueError('Ambiguous phone decoder')
    return nodes


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings-sha256',required=True)
    parser.add_argument('--gstreamer',action='store_true',help='Also require an explicit V4L2 GStreamer frame')
    args=parser.parse_args()
    if os.geteuid()!=0 or sha(BINDINGS)!=args.bindings_sha256:raise ValueError('Wrong root/video bindings')
    bindings=json.loads(BINDINGS.read_text())
    if bindings['kernel_release']!=os.uname().release:raise ValueError('Wrong video kernel')
    if not Path('/run/oneplus13/ram-session').is_file():raise ValueError('Protected RAM session required')
    if not re.search(r'^overlay / overlay ',Path('/proc/mounts').read_text(),re.M):raise ValueError('RAM overlay required')
    if float(Path('/proc/uptime').read_text().split()[0])>=440:raise ValueError('Insufficient absolute recovery budget')
    for path in Path('/sys/class/block').iterdir():
        if not path.name.startswith(('ram','zram','loop')) and (path/'ro').read_text().strip()!='1':
            raise ValueError('Writable physical storage')
    flags=Path('/proc/cmdline').read_text().split()
    for name in ('audio_probe','camera_probe','video_probe'):
        values=[value for value in flags if value.startswith('oneplus13.'+name+'=')]
        if not 1<=len(values)<=4 or set(values)!={'oneplus13.'+name+'=1'}:raise ValueError('Wrong manual video alternative')
    spec=importlib.util.spec_from_file_location('video_audio_guard','/usr/libexec/oneplus13-ram-audio-check.py')
    audio=importlib.util.module_from_spec(spec);spec.loader.exec_module(audio)
    for unit in ('oneplus13-ram-guard.service','oneplus13-ram-deadline.timer'):
        audio.run(['systemctl','is-active','--quiet',unit])
    for unit in ('oneplus13-sensor-fileserver.service','oneplus13-sensor-proxy.service'):
        if audio.run(['systemctl','show',unit,'-p','ActiveState','--value']).strip() not in ('inactive','failed'):
            raise ValueError('Sensor service active during video test')
    for name in ('qcom_iris','s5kjn5','i2c_qcom_cci','ath12k_wifi7'):
        if Path('/sys/module',name).exists():raise ValueError('Peripheral active before manual video test: '+name)
    audio.run(['systemctl','is-active','--quiet','oneplus13-awake-video-guard.service'])
    guard=Path('/run/oneplus13/watchdog-recovery/result.json')
    node=guard.lstat()
    if not stat.S_ISREG(node.st_mode) or node.st_uid!=0 or node.st_size>16384:
        raise ValueError('Wrong awake video guard receipt')
    guard_receipt=json.loads(guard.read_text())
    validate_awake_guard(guard_receipt,time.monotonic())
    if args.gstreamer and guard_receipt['guard_deadline_monotonic']-time.monotonic()<=65:
        raise ValueError('Insufficient fixed guard window for native plus GStreamer frames')
    watchdogs=[p for p in Path('/sys/class/watchdog').glob('watchdog[0-9]*')
               if (p/'identity').read_text().strip()=='Gunyah Watchdog']
    if len(watchdogs)!=1 or (watchdogs[0]/'state').read_text().strip()!='active':
        raise ValueError('Awake video watchdog is not active')
    if decoder_nodes():raise ValueError('Decoder already present')
    if sha(POLICY)!=bindings['module_policy_sha256'] or 'install qcom_iris /bin/false\n' not in POLICY.read_text():
        raise ValueError('Wrong manual video policy')
    base=Path('/proc/device-tree')
    for path,props in bindings['live_nodes'].items():
        for key,value in props.items():
            if (base/path.lstrip('/')/key).read_bytes()!=bytes.fromhex(value):raise ValueError('Wrong live video resource: '+key)
    for name,digest in bindings['files'].items():
        if sha(Path('/')/name)!=digest:raise ValueError('Changed video runtime payload')
    modules=Path('/usr/lib/modules')/bindings['kernel_release']
    for name,digest in bindings['module_files'].items():
        if sha(modules/name)!=digest:raise ValueError('Changed video module/dependency')
    firmware=Path('/usr/lib/firmware/qcom/sm8750/oneplus/dodge/vpu35_4v.mbn')
    if sha(firmware)!='e1b2cb6de867893537fe6a1ffba312c2a2949402ca9892d393e2f40de255690a':
        raise ValueError('Wrong product-signed Iris firmware')
    raw=fixture_bytes(json.loads(Path('/usr/lib/oneplus13-video/synthetic-avc-bars.json').read_text()))
    STATE.mkdir(mode=0o700,exist_ok=False)
    fixture=STATE/'generated-frame.h264';fixture.write_bytes(raw);fixture.chmod(0o600)
    report={'scope':'BOUNDED_MANUAL_IRIS_AVC_FRAME','hardware_decode_qualified':False,
            'gstreamer_requested':args.gstreamer,'gstreamer_frame_qualified':False,
            'software_fallback':False,'capture_input':False,'firmware_session_opened':False,
            'runtime_suspended':False,'module_unloaded':False,'video_nodes_removed':False}
    attempted=False;device=None;suspend_waited=False
    try:
        ordered=audio.module_order((modules/'modules.dep').read_text(),['videocc_sm8750','qcom_iris'])
        attempted=True
        for name in ordered:audio.run(['modprobe','--ignore-install',name],8)
        until=time.monotonic()+5
        while time.monotonic()<until:
            nodes=decoder_nodes()
            if nodes:break
            time.sleep(.1)
        else:raise ValueError('Iris decoder did not bind')
        node,device=nodes[0]
        environment=dict(os.environ,ONEPLUS13_GUARDED_VIDEO_TEST='1')
        result=subprocess.run(['/usr/libexec/oneplus13-decode-one-frame','--decode',str(node),str(fixture)],
                              capture_output=True,text=True,timeout=25,env=environment)
        # Native diagnostics contain only stage/errno and the synthetic-frame result.
        print(result.stdout,flush=True);print(result.stderr,flush=True)
        report['firmware_session_opened']='IRIS_FIRMWARE_SESSION_OPENED' in result.stdout
        if result.returncode:raise ValueError('Iris native test failed')
        report['decoded_frame']=parse_frame(result.stdout)
        if args.gstreamer:
            # Fresh RAM registry is created after the guarded module binds, so
            # its stateful decoder is probed even if the desktop cached no VPU.
            framework_env=dict(environment,GST_REGISTRY_1_0=str(STATE/'gst-registry.bin'))
            result=subprocess.run(['python3','/usr/libexec/oneplus13-ram-gstreamer-frame.py',
                                   '--device',str(node),'--fixture',str(fixture)],
                                  capture_output=True,text=True,timeout=18,env=framework_env)
            print(result.stdout,flush=True);print(result.stderr,flush=True)
            statuses=re.findall(r'^GSTREAMER_PIPELINE_STATUS (\{.*\})$',result.stdout,re.M)
            if len(statuses)==1:
                status=json.loads(statuses[0])
                if (status.get('scope')=='GSTREAMER_EXPLICIT_V4L2_FRAME' and
                    status.get('software_fallback') is False and
                    type(status.get('pipeline_stopped')) is bool):
                    report['gstreamer_pipeline_stopped']=status['pipeline_stopped']
            if result.returncode:raise ValueError('GStreamer V4L2 frame failed')
            rows=re.findall(r'^GSTREAMER_FRAME_RESULT (\{.*\})$',result.stdout,re.M)
            if len(rows)!=1:raise ValueError('Missing unique GStreamer result')
            frame=json.loads(rows[0])
            if (frame.get('scope')!='GSTREAMER_EXPLICIT_V4L2_FRAME' or
                frame.get('frame_verified') is not True or frame.get('pipeline_stopped') is not True or
                frame.get('software_fallback') is not False or
                not re.fullmatch(r'v4l2(?:video[0-9]+)?h264dec',str(frame.get('hardware_decoder_factory'))) or
                frame.get('width')!=320 or frame.get('height')!=240 or frame.get('pts_ns')!=12345000000):
                raise ValueError('GStreamer pixels/decoder/cleanup not qualified')
            report['gstreamer_frame']=frame;report['gstreamer_frame_qualified']=True
        suspend_waited=True
        until=time.monotonic()+8
        while time.monotonic()<until:
            if (device/'power/runtime_status').read_text().strip()=='suspended':
                report['runtime_suspended']=True;break
            time.sleep(.2)
        if not report['runtime_suspended']:raise ValueError('Iris did not autosuspend')
        report['hardware_decode_qualified']=True
    finally:
        cleanup_error=False
        # Observe autosuspend even after framework failure. This does not turn
        # a failed decode into acceptance; hardware_decode_qualified stays false.
        if device is not None and not suspend_waited:
            until=time.monotonic()+8
            try:
                while time.monotonic()<until:
                    if (device/'power/runtime_status').read_text().strip()=='suspended':
                        report['runtime_suspended']=True;break
                    time.sleep(.2)
            except OSError:
                report['runtime_suspend_observation_failed']=True
        if attempted and Path('/sys/module/qcom_iris').exists():
            try:audio.run(['modprobe','--remove','qcom_iris'],8)
            except Exception:cleanup_error=True
        report['module_unloaded']=not Path('/sys/module/qcom_iris').exists()
        report['video_nodes_removed']=not decoder_nodes()
        fixture.unlink(missing_ok=True)
        report['cleanup_pass']=not cleanup_error and report['module_unloaded'] and report['video_nodes_removed']
        if not report['cleanup_pass']:report['hardware_decode_qualified']=False
        (STATE/'result.json').write_text(json.dumps(report,sort_keys=True)+'\n')
        print('VIDEO_CHECK_RESULT '+json.dumps(report,sort_keys=True),flush=True)
        if not report['cleanup_pass']:raise ValueError('Iris cleanup incomplete')


if __name__=='__main__':main()
