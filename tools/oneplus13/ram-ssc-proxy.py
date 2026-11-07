#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded original SensorProxy API candidate; explicit RAM-only invocation."""
import hashlib,json,os,re,subprocess,time
from pathlib import Path
PREFIX=Path('/usr/lib/erhai/sensors')
STATE=Path('/run/oneplus13/ssc-proxy')
UNIT='oneplus13-ssc-proxy-qualification.service'
RULE=Path('/run/udev/rules.d/79-oneplus13-ssc-manual.rules')
POLICY=Path('/etc/dbus-1/system.d/oneplus13-ssc-manual.conf')
AUTH=Path('/usr/share/polkit-1/actions/oneplus13-ssc-manual.policy')
AUTHRULE=Path('/etc/polkit-1/rules.d/49-oneplus13-ssc-manual.rules')
NODE='/sys/class/misc/fastrpc-adsp-secure'

def run(args,timeout=5):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise ValueError('Proxy command failed: '+Path(args[0]).name+' rc='+str(p.returncode))
    if len(p.stdout)>65536:raise ValueError('Oversized proxy command output')
    return p.stdout
def rule(value):
    return 'ACTION!="remove", SUBSYSTEM=="misc", KERNEL=="fastrpc-adsp-secure", ENV{IIO_SENSOR_PROXY_TYPE}="'+value+'"\n'
def refresh():
    run(['udevadm','control','--reload-rules'])
    run(['udevadm','trigger','--action=change','--subsystem-match=misc','--sysname-match=fastrpc-adsp-secure'])
    run(['udevadm','settle','--timeout=3'])
def reload_bus():
    run(['busctl','call','org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','ReloadConfig'])
def interpret(output):
    rows=re.findall(r'^PHONE_SENSOR_BUS_RESULT complete=(yes|no) gyro_updates=(\d+) accel_updates=(\d+) compass_updates=(\d+) claims=([01]{3}) releases=([01]{3}) physical_axes_accepted=no$',output,re.M)
    if len(rows)!=1:raise ValueError('Missing unique D-Bus result')
    complete,updates,accel,compass,claims,releases=rows[0]
    if complete!='yes' or not 3<=int(updates)<=4096 or not all(1<=int(n)<=4096 for n in (accel,compass)) or claims!='111' or releases!='111':raise ValueError('D-Bus data or claim/release failed')
    return {'same_connection_three_claims_and_releases':True,'gyro_property_updates':int(updates),
            'accelerometer_posture_updates':int(accel),'compass_heading_updates':int(compass),
            'accelerometer_posture_available':True,'compass_heading_finite':True,
            'physical_axes_or_calibration_accepted':False,'continuous_autostart_accepted':False}
def collect_posture(seconds,claim,properties,release,clock=time.monotonic,sleep=time.sleep):
    """Observe a short owner-assisted window; never accept physical calibration."""
    if type(seconds) is not int or seconds!=12:
        raise ValueError('Only the twelve-second owner posture window is reviewed')
    orientations={'undefined','normal','bottom-up','left-up','right-up'}
    tilts={'undefined','vertical','tilted-up','tilted-down','face-up','face-down'}
    rows=[];samples=0
    claim()
    try:
        start=clock()
        while clock()-start<seconds:
            values=properties();orientation=values.get('AccelerometerOrientation');tilt=values.get('AccelerometerTilt')
            if orientation not in orientations or tilt not in tilts:
                raise ValueError('Unrecognized SensorProxy posture properties')
            samples+=1
            if samples>64:raise ValueError('Posture sample bound exceeded')
            row={'seconds':round(clock()-start,3),'orientation':orientation,'tilt':tilt}
            if not rows or (orientation,tilt)!=(rows[-1]['orientation'],rows[-1]['tilt']):rows.append(row)
            sleep(.2)
    finally:release()
    if samples<3:raise ValueError('Incomplete owner posture window')
    return {'scope':'OWNER_ASSISTED_POSTURE_OBSERVATION_ONLY','same_connection_claim_release':True,
            'seconds':seconds,'samples':samples,'transitions':rows,'physical_axis_calibration_accepted':False}

def observe_posture():
    # This optional diagnostic stays within the existing30s proxy service cap.
    from gi.repository import Gio,GLib
    bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
    destination='net.hadess.SensorProxy';path='/net/hadess/SensorProxy'
    def call(interface,method,args=None):
        return bus.call_sync(destination,path,interface,method,args,None,Gio.DBusCallFlags.NONE,2000,None)
    return collect_posture(12,lambda:call(destination,'ClaimAccelerometer'),
        lambda:call('org.freedesktop.DBus.Properties','GetAll',GLib.Variant('(s)',(destination,))).unpack()[0],
        lambda:call(destination,'ReleaseAccelerometer'))

def run_proxy(manifest):
    owner_window=os.environ.get('ONEPLUS13_OWNER_POSTURE_SECONDS','0')
    if owner_window not in ('0','12'):raise ValueError('Unreviewed owner posture duration')
    if os.geteuid()!=0 or not Path('/run/oneplus13/ram-session').is_file():raise ValueError('Protected root RAM session required')
    if not any(row.split()[1:3]==['/','overlay'] for row in Path('/proc/mounts').read_text().splitlines()):raise ValueError('RAM overlay required')
    for path in Path('/sys/class/block').iterdir():
        if not path.name.startswith(('ram','loop','zram')) and (path/'ro').read_text().strip()!='1':raise ValueError('Physical storage is writable')
    for unit in ('oneplus13-ram-guard.service','oneplus13-ram-deadline.timer'):run(['systemctl','is-active','--quiet',unit])
    if manifest.get('scope')!='PUBLIC_REVIEWED_PHONE_SENSOR_RUNTIME_INPUTS' or manifest.get('native_elf_count')!=10:raise ValueError('Unreviewed proxy runtime')
    for name,item in manifest['files'].items():
        if not name.startswith(str(PREFIX).lstrip('/')+'/'):continue
        path=Path('/')/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Proxy native runtime changed')
    if any(p.exists() for p in (RULE,POLICY,AUTH,AUTHRULE)):raise ValueError('Proxy integration already present')
    before=run(['udevadm','info','--query=property','--path='+NODE])
    if re.search(r'^IIO_SENSOR_PROXY_TYPE=',before,re.M):raise ValueError('Sensor udev type already assigned')
    STATE.mkdir(mode=0o700,exist_ok=False)
    if run([str(PREFIX/'bin/phone-sensor-bus-check'),'--self-test']).strip()!='PHONE_SENSOR_BUS_VARIANT_CHECKS_PASS_NO_BUS_NO_QRTR':raise ValueError('Native client self-test failed')
    installed_rule=installed_policy=installed_auth=installed_authrule=False;started=False;answer=None;errors=[]
    try:
        RULE.parent.mkdir(parents=True,exist_ok=True)
        with RULE.open('x') as f:f.write(rule('ssc-accel ssc-compass ssc-gyro'))
        installed_rule=True;RULE.chmod(0o644);refresh()
        POLICY.parent.mkdir(parents=True,exist_ok=True)
        with POLICY.open('x') as f:f.write('''<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><policy user="root"><allow own="net.hadess.SensorProxy"/></policy>
<policy context="default"><allow send_destination="net.hadess.SensorProxy"/></policy></busconfig>
''')
        installed_policy=True;POLICY.chmod(0o644);reload_bus()
        AUTH.parent.mkdir(parents=True,exist_ok=True)
        with AUTH.open('x') as f:f.write('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN" "http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">
<policyconfig><action id="net.hadess.SensorProxy.claim-sensor">
<description>Claim a sensor for the bounded OnePlus 13 test</description>
<message>Privileges are required to claim a sensor.</message>
<defaults><allow_any>no</allow_any><allow_inactive>no</allow_inactive><allow_active>yes</allow_active></defaults>
</action></policyconfig>
''')
        installed_auth=True;AUTH.chmod(0o644)
        AUTHRULE.parent.mkdir(parents=True,exist_ok=True)
        with AUTHRULE.open('x') as f:f.write('polkit.addRule(function(action, subject) { if (action.id === "net.hadess.SensorProxy.claim-sensor" && subject.user === "root") return polkit.Result.YES; });\n')
        installed_authrule=True;AUTHRULE.chmod(0o644)
        run(['systemctl','start','polkit.service'],8)
        props=['Type=dbus','BusName=net.hadess.SensorProxy','RuntimeMaxSec=30s','TimeoutStartSec=10s','TimeoutStopSec=5s',
          'KillMode=control-group','Restart=no','NoNewPrivileges=yes','CapabilityBoundingSet=',
          'ProtectSystem=strict','ProtectHome=yes','ProtectKernelTunables=yes','ProtectKernelModules=yes',
          'ProtectControlGroups=yes','PrivateTmp=yes','PrivateDevices=yes','MemoryMax=192M','TasksMax=24',
          'RestrictAddressFamilies=AF_UNIX AF_QIPCRTR AF_NETLINK','RestrictNamespaces=yes',
          'StandardOutput=append:'+str(STATE/'proxy.log'),'StandardError=append:'+str(STATE/'proxy.log')]
        command=['systemd-run','--quiet','--no-block','--unit='+UNIT,
          '--setenv=LD_LIBRARY_PATH='+str(PREFIX/'lib'),'--setenv=SSC_SUSPEND_MODE=nowakeup']
        command+=['--property='+x for x in props]+[str(PREFIX/'libexec/iio-sensor-proxy')]
        started=True;run(command)
        env={**os.environ,'LD_LIBRARY_PATH':str(PREFIX/'lib')}
        result=subprocess.run([str(PREFIX/'bin/phone-sensor-bus-check')],capture_output=True,text=True,timeout=23,env=env)
        (STATE/'client.stdout').write_text(result.stdout);(STATE/'client.stderr').write_text(result.stderr)
        if result.returncode:raise ValueError('D-Bus client failed rc='+str(result.returncode))
        answer=interpret(result.stdout)
        if owner_window=='12':answer['owner_posture_observation']=observe_posture()
    finally:
        if started:
            try:
                run(['systemctl','stop',UNIT],7)
                status=run(['systemctl','show',UNIT,'-p','ActiveState','-p','MainPID','-p','Result'])
                v=dict(x.split('=',1) for x in status.splitlines() if '=' in x)
                if v.get('MainPID')!='0' or v.get('ActiveState')!='inactive' or v.get('Result')!='success':raise ValueError('Proxy did not stop cleanly')
            except Exception as e:errors.append(str(e))
        if installed_rule:
            try:
                RULE.write_text(rule(''));refresh();RULE.unlink();run(['udevadm','control','--reload-rules'])
            except Exception as e:errors.append(str(e))
        if installed_policy:
            try:POLICY.unlink();reload_bus()
            except Exception as e:errors.append(str(e))
        for created,path in ((installed_authrule,AUTHRULE),(installed_auth,AUTH)):
            if created:
                try:path.unlink()
                except Exception as e:errors.append(str(e))
        if errors:raise ValueError('Proxy cleanup failed: '+'; '.join(errors))
    return {'scope':'BOUNDED_THREE_SENSOR_DBUS_CANDIDATE','unit_inactive':True,
            'temporary_udev_and_bus_policy_removed':True,**answer}
