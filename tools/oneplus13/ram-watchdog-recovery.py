#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""One manual RAM-only Gunyah watchdog reset; no suspend or storage writes."""
import argparse,array,fcntl,json,os,re,stat,subprocess,time
from pathlib import Path

STATE=Path('/run/oneplus13/watchdog-recovery')
WDIOC_GETSUPPORT=0x80285700
WDIOC_SETTIMEOUT=0xc0045706
WDIOC_GETTIMEOUT=0x80045707
WDIOC_KEEPALIVE=0x80045705
WDIOC_GETTIMELEFT=0x8004570a


def validate_inventory(base):
    fields={name:(base/name).read_text().strip() for name in
            ('identity','state','timeout','nowayout','dev')}
    if fields['identity']!='Gunyah Watchdog':raise ValueError('Wrong watchdog identity')
    if fields['state']!='inactive' or fields['nowayout']!='0':
        raise ValueError('Watchdog already owned or cannot be disarmed')
    if fields['timeout']!='32':raise ValueError('Unexpected Gunyah timeout range')
    numbers=fields['dev'].split(':')
    if len(numbers)!=2 or not all(x.isdecimal() for x in numbers):raise ValueError('Invalid watchdog dev_t')
    return tuple(map(int,numbers))


def ioctl_int(fd,request,value=0):
    data=array.array('i',[value]);fcntl.ioctl(fd,request,data,True)
    return data[0]


def status_sample(base,label,required=False):
    """Read the driver's STATUS-only diagnostic, without petting the timer."""
    path=base/'device/hypervisor_status'
    if not path.is_file():
        if required:raise ValueError('Required hypervisor status diagnostic is absent')
        return {'label':label,'available':False}
    with path.open() as stream:text=stream.read(257)
    if len(text)>256:raise ValueError('Oversized hypervisor status diagnostic')
    pairs=[item.split('=') for item in text.split()]
    if (len(pairs)!=4 or any(len(p)!=2 for p in pairs) or
        {p[0] for p in pairs}!={'result','enabled','expired','elapsed_raw'}):
        raise ValueError('Malformed hypervisor status diagnostic')
    values={key:int(value) for key,value in pairs}
    if (values['result']!=0 or values['enabled'] not in (0,1) or
        values['expired'] not in (0,1) or not 0<=values['elapsed_raw']<=0xffffffff):
        raise ValueError('Invalid hypervisor status diagnostic values')
    return {'label':label,'available':True,'monotonic_seconds':time.monotonic(),**values}


def record(doc):
    temporary=STATE/'result.tmp'
    temporary.write_text(json.dumps(doc,sort_keys=True)+'\n')
    temporary.replace(STATE/'result.json')
    print('WATCHDOG_RECOVERY_RESULT '+json.dumps(doc,sort_keys=True),flush=True)


def busy_wait(seconds):
    """Keep this ordinary-priority process runnable without any keepalive."""
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:pass


def video_stop_requested(token,marker=None,result=None,module=None):
    """Only disarm after the source-bound video helper removed its hardware."""
    marker=STATE/'stop-video-guard' if marker is None else marker
    result=Path('/run/oneplus13/video/result.json') if result is None else result
    module=Path('/sys/module/qcom_iris') if module is None else module
    try:
        node=marker.lstat()
        if (not stat.S_ISREG(node.st_mode) or node.st_uid!=0 or
            node.st_mode&0o077 or node.st_nlink!=1 or node.st_size!=33):return False
        if marker.read_text(encoding='ascii')!=token+'\n':return False
        node=result.lstat()
        if not stat.S_ISREG(node.st_mode) or node.st_uid!=0 or node.st_size>16384:return False
        receipt=json.loads(result.read_text())
        if not isinstance(receipt,dict):return False
        return (all(receipt.get(key) is True for key in
                    ('cleanup_pass','module_unloaded','video_nodes_removed')) and
                not module.exists())
    except (OSError,ValueError,UnicodeError):return False


def video_guard_window(stop_check,ping,report):
    """Keep CPU runnable; a fixed 90s window can never be renewed by a ping."""
    started=time.monotonic();deadline=started+90;next_ping=started+3;next_check=started
    report.update(stage='video-guard-running',guard_running=True,guard_seconds=90,
                  guard_deadline_monotonic=deadline,guard_keepalives=0,
                  remaining_no_ping_wait_seconds=45)
    record(report)
    try:
        while (now:=time.monotonic())<deadline:
            if now>=next_check:
                if stop_check():
                    report.update(stage='video-guard-clean-stop',guard_running=False,
                                  guard_completed=True)
                    return
                next_check=now+0.05
            if now>=next_ping:
                ping();report['guard_keepalives']+=1;next_ping=now+3
    except Exception as error:
        report.update(stage='video-guard-error-no-ping',guard_running=False,
                      guard_failure_category=type(error).__name__)
        record(report)
        busy_wait(45)
        report['reset_failed']=True
        raise RuntimeError('Awake video guard error did not reset within expiry window') from error
    report.update(stage='video-guard-expired-no-ping',guard_running=False,
                  guard_deadline_expired=True)
    record(report)
    busy_wait(45)
    report['reset_failed']=True
    raise RuntimeError('Awake video guard did not reset within bounded expiry window')


def clock_probe(sample,report,keepalive):
    """Collect idle/busy STATUS evidence; never infer units or qualify recovery."""
    affinity=os.sched_getaffinity(0)
    if not affinity:raise ValueError('Empty CPU affinity')
    cpu=min(affinity)
    report.update(clock_probe_cpu=cpu,clock_intervals=[])
    try:
        os.sched_setaffinity(0,{cpu})
        sample('clock-probe-start')
        for mode in ('idle','busy','idle'):
            wall=time.monotonic();process=time.process_time()
            if mode=='idle':time.sleep(2)
            else:busy_wait(2)
            report['clock_intervals'].append({
                'mode':mode,'wall_seconds':time.monotonic()-wall,
                'process_seconds':time.process_time()-process})
            sample('clock-probe-'+mode+'-'+str(len(report['clock_intervals'])))
        keepalive()
        sample('clock-probe-after-keepalive')
    finally:
        os.sched_setaffinity(0,affinity)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--arm-one-reset',action='store_true')
    mode.add_argument('--diagnose-clock',action='store_true',
                      help='Six-second STATUS sampling, then disarm; no reset qualification')
    mode.add_argument('--guard-one-video',action='store_true',
                      help='One fixed awake 90s video window; stop only after verified teardown')
    parser.add_argument('--stop-token')
    parser.add_argument('--hold-cpu-awake',action='store_true',
                        help='Pin this reset test to one CPU and busy-wait; excludes idle/suspend qualification')
    parser.add_argument('--require-hypervisor-status',action='store_true')
    args=parser.parse_args()
    if args.guard_one_video:
        if not args.stop_token or not re.fullmatch('[0-9a-f]{32}',args.stop_token):
            parser.error('--guard-one-video requires a 32-hex stop token')
        args.hold_cpu_awake=True
    elif args.stop_token:parser.error('--stop-token requires --guard-one-video')
    if args.hold_cpu_awake and not (args.arm_one_reset or args.guard_one_video):
        parser.error('--hold-cpu-awake requires --arm-one-reset')
    if os.geteuid()!=0 or os.uname().release!=args.release:raise ValueError('Wrong root/kernel')
    if not Path('/run/oneplus13/ram-session').is_file():raise ValueError('Protected RAM session required')
    if not any(x.startswith('overlay / overlay ') for x in Path('/proc/mounts').read_text().splitlines()):
        raise ValueError('RAM overlay required')
    for p in Path('/sys/class/block').iterdir():
        if not p.name.startswith(('ram','zram','loop')) and (p/'ro').read_text().strip()!='1':
            raise ValueError('Writable physical storage')
    for unit in ('oneplus13-ram-guard.service','oneplus13-ram-deadline.timer'):
        subprocess.run(['systemctl','is-active','--quiet',unit],check=True,timeout=3)
    uptime=float(Path('/proc/uptime').read_text().split()[0])
    if not 60<uptime<510:raise ValueError('Insufficient bounded recovery window')
    if args.guard_one_video:
        flags=[x for x in Path('/proc/cmdline').read_text().split()
               if x.startswith('oneplus13.video_probe=')]
        if not flags or set(flags)!={'oneplus13.video_probe=1'}:
            raise ValueError('Manual video alternative required')
        if uptime>=300 or Path('/sys/module/qcom_iris').exists():
            raise ValueError('Video already active or insufficient guard budget')
    candidates=[p for p in Path('/sys/class/watchdog').glob('watchdog[0-9]*')
                if (p/'identity').read_text().strip()=='Gunyah Watchdog']
    if len(candidates)!=1:raise ValueError('Ambiguous Gunyah watchdog')
    base=candidates[0];device=Path('/dev')/base.name
    major,minor=validate_inventory(base)
    node=device.stat()
    if not stat.S_ISCHR(node.st_mode) or (os.major(node.st_rdev),os.minor(node.st_rdev))!=(major,minor):
        raise ValueError('Wrong watchdog character device')
    STATE.mkdir(mode=0o700,exist_ok=False)
    report={'scope':('ONE_MANUAL_RAM_GUNYAH_CLOCK_DIAGNOSTIC' if args.diagnose_clock else
                     'ONE_MANUAL_RAM_GUNYAH_AWAKE_VIDEO_GUARD' if args.guard_one_video else
                     'ONE_MANUAL_RAM_GUNYAH_AWAKE_RESET' if args.hold_cpu_awake else
                     'ONE_MANUAL_RAM_GUNYAH_WATCHDOG_RESET'),'suspend_requested':False,
            'active_wait_required':args.hold_cpu_awake,'idle_or_suspend_recovery_qualified':False,
            'partition_writes':0,'armed':False,'countdown_verified':False,
            'keepalive_verified':False,'reset_observed':False,'hardware_samples':[]}
    def sample(label):
        report['hardware_samples'].append(status_sample(
            base,label,args.require_hypervisor_status or args.diagnose_clock or args.hold_cpu_awake))
    fd=None;affinity=None
    try:
        if args.hold_cpu_awake:
            report['stage']='cpu-affinity'
            affinity=os.sched_getaffinity(0)
            if not affinity:raise ValueError('Empty CPU affinity')
            report['awake_cpu']=min(affinity)
            os.sched_setaffinity(0,{report['awake_cpu']})
        report['stage']='inactive-status'
        sample('inactive-before-open')
        report['stage']='device-open'
        fd=os.open(device,os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        sample('after-open')
        report['stage']='support'
        support=bytearray(40);fcntl.ioctl(fd,WDIOC_GETSUPPORT,support,True)
        if bytes(support[8:]).split(b'\0')[0]!=b'Gunyah Watchdog':raise ValueError('Wrong ioctl identity')
        options=int.from_bytes(support[:4],'little')
        report['support_options']=options
        if options&0x8180!=0x8180:raise ValueError('Missing timeout/keepalive/magic-close support')
        report['stage']='timeout'
        requested_timeout=32 if args.diagnose_clock else 12
        report['set_timeout_readback']=ioctl_int(fd,WDIOC_SETTIMEOUT,requested_timeout)
        report['get_timeout_readback']=ioctl_int(fd,WDIOC_GETTIMEOUT)
        if (report['set_timeout_readback']!=requested_timeout or
            report['get_timeout_readback']!=requested_timeout):
            raise ValueError('Watchdog timeout readback mismatch')
        report['stage']='first-keepalive'
        ioctl_int(fd,WDIOC_KEEPALIVE)
        sample('first-keepalive')
        if args.diagnose_clock:
            # The existing reset/countdown gate below remains unchanged.
            # No keepalive occurs during the six-second comparison window.
            report['stage']='clock-probe'
            clock_probe(sample,report,lambda:ioctl_int(fd,WDIOC_KEEPALIVE))
            report.update(stage='clock-probe-complete',clock_probe_completed=True)
            return
        report['stage']='countdown'
        first=ioctl_int(fd,WDIOC_GETTIMELEFT)
        report['first_timeleft']=first
        sample('first-timeleft')
        if args.hold_cpu_awake:busy_wait(2)
        else:time.sleep(2)
        second=ioctl_int(fd,WDIOC_GETTIMELEFT)
        report['second_timeleft']=second
        sample('second-timeleft')
        if not 0<second<first<=12:raise ValueError('Watchdog countdown not observed')
        report['countdown_verified']=True
        report['stage']='second-keepalive'
        ioctl_int(fd,WDIOC_KEEPALIVE)
        sample('second-keepalive')
        third=ioctl_int(fd,WDIOC_GETTIMELEFT)
        report['third_timeleft']=third
        if not second<third<=12:raise ValueError('Watchdog keepalive not observed')
        if (base/'state').read_text().strip()!='active':raise ValueError('Watchdog not active')
        report.update(armed=True,keepalive_verified=True,timeout_seconds=12,
                      last_timeleft=third,remaining_no_ping_wait_seconds=20,stage='no-ping-wait')
        if args.guard_one_video:
            video_guard_window(lambda:video_stop_requested(args.stop_token),
                               lambda:ioctl_int(fd,WDIOC_KEEPALIVE),report)
            return
        record(report)
        # Keep the open descriptor alive without pings; do not ask PID1 to reboot.
        # If the hypervisor fails to reset, the finally block disarms after20s.
        if args.hold_cpu_awake:busy_wait(20)
        else:time.sleep(20)
        report['reset_failed']=True
        raise RuntimeError('Watchdog failed to reset within bounded no-ping wait')
    except (OSError,ValueError,RuntimeError) as error:
        report['failure_category']=type(error).__name__
        report['failure_reason']=str(error)
        if isinstance(error,OSError):report['errno']=error.errno
        raise
    finally:
        try:
            if fd is not None:
                try:os.write(fd,b'V')
                finally:os.close(fd)
                report['disarmed_after_return']=(base/'state').read_text().strip()=='inactive'
        finally:
            try:
                if affinity is not None:os.sched_setaffinity(0,affinity)
            finally:record(report)


if __name__=='__main__':main()
