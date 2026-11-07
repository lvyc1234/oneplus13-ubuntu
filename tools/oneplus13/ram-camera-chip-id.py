#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""One guarded manual CCI/S5KJN5 probe and teardown, never a capture request."""
import argparse,hashlib,importlib.util,json,os,re,subprocess,time
from pathlib import Path
STATE=Path('/run/oneplus13/camera-chip-id')
POLICY=Path('/etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf')
TARGET='/soc@0/cci@ac7b000/i2c-bus@0/camera@2d'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',required=True)
    parser.add_argument('--policy-sha256',required=True)
    parser.add_argument('--sensor-sha256',required=True)
    parser.add_argument('--cci-sha256',required=True)
    args=parser.parse_args()
    if os.geteuid()!=0 or os.uname().release!=args.release:raise ValueError('Wrong camera root/kernel')
    if not Path('/run/oneplus13/ram-session').is_file():raise ValueError('Protected RAM session required')
    if not re.search(r'^overlay / overlay ',Path('/proc/mounts').read_text(),re.M):raise ValueError('RAM overlay required')
    for path in Path('/sys/class/block').iterdir():
        if not path.name.startswith(('ram','zram','loop')) and (path/'ro').read_text().strip()!='1':
            raise ValueError('Writable physical storage')
    flags=Path('/proc/cmdline').read_text().split()
    for flag in ('oneplus13.audio_probe=1','oneplus13.camera_probe=1'):
        values=[value for value in flags if value.startswith(flag.split('=')[0]+'=')]
        if not 1<=len(values)<=4 or set(values)!={flag}:raise ValueError('Wrong camera alternative boot')
    audio=load('camera_audio_guard','/usr/libexec/oneplus13-ram-audio-check.py')
    mic=load('camera_board_guard','/usr/libexec/oneplus13-ram-microphone-check.py')
    mic.validate_board()
    for unit in ('oneplus13-ram-guard.service','oneplus13-ram-deadline.timer'):
        audio.run(['systemctl','is-active','--quiet',unit])
    for unit in ('oneplus13-sensor-fileserver.service','oneplus13-sensor-proxy.service'):
        status=audio.run(['systemctl','show',unit,'-p','ActiveState','--value']).strip()
        if status not in ('inactive','failed'):raise ValueError('Sensor service active during camera probe')
    if Path('/sys/module/ath12k_wifi7').exists():raise ValueError('Radio active during camera probe')
    if sha(POLICY)!=args.policy_sha256:raise ValueError('Changed camera module policy')
    policy=POLICY.read_text()
    for name in ('s5kjn5','i2c_qcom_cci'):
        if Path('/sys/module',name).exists():raise ValueError('Camera module already loaded')
        if 'install '+name+' /bin/false\n' not in policy:raise ValueError('Missing manual camera policy')
    dt=Path('/proc/device-tree');sensor=dt/TARGET.lstrip('/')
    def expect(path,raw):
        if path.read_bytes()!=raw:raise ValueError('Wrong live camera graph: '+path.name)
    import struct
    def u32(*values):return struct.pack('>'+'I'*len(values),*values)
    expect(sensor/'compatible',b'samsung,s5kjn5\0');expect(sensor/'reg',u32(0x2d))
    expect(sensor/'assigned-clock-rates',u32(19200000))
    def ph(path):return (dt/path/'phandle').read_bytes()
    expect(sensor/'reset-gpios',ph('soc@0/pinctrl@f100000')+u32(178,1))
    expect(sensor/'clocks',ph('soc@0/clock-controller@1760000')+u32(0))
    expect(sensor/'assigned-clocks',ph('soc@0/clock-controller@1760000')+u32(0))
    cc=ph('soc@0/clock-controller@ade0000')
    expect(dt/'soc@0/cci@ac7b000/clocks',cc+u32(0)+cc+u32(19))
    expect(dt/'soc@0/cci@ac7b000/power-domains',cc+u32(0))
    expect(dt/'soc@0/rsc@16500000/regulators-camera/compatible',b'qcom,pm8010-rpmh-regulators\0')
    expect(dt/'soc@0/rsc@16500000/regulators-camera/qcom,pmic-id',b'm\0')
    for name,rail in [('vddio',5),('vdda',4),('afvdd',7)]:
        expect(sensor/(name+'-supply'),ph('soc@0/rsc@16500000/regulators-camera/ldo'+str(rail)))
    for name in ('vddd','vddm'):
        expect(sensor/(name+'-supply'),ph('soc@0/rsc@16500000/regulators-4/ldo1'))
    expect(dt/'soc@0/cci@ac7b000/interrupts',u32(0,426,1))
    for number,voltage in ((4,2200000),(5,1800000),(7,2800000)):
        base=dt/('soc@0/rsc@16500000/regulators-camera/ldo'+str(number))
        for key in ('regulator-min-microvolt','regulator-max-microvolt'):expect(base/key,u32(voltage))
    for key in ('regulator-min-microvolt','regulator-max-microvolt'):
        expect(dt/('soc@0/rsc@16500000/regulators-4/ldo1/'+key),u32(1000000))
    modules=Path('/usr/lib/modules')/args.release
    for path,digest in ((modules/'kernel/drivers/media/i2c/s5kjn5.ko',args.sensor_sha256),
                        (modules/'kernel/drivers/i2c/busses/i2c-qcom-cci.ko',args.cci_sha256)):
        if sha(path)!=digest:raise ValueError('Wrong camera module bytes')
    STATE.mkdir(mode=0o700,exist_ok=False)
    report={'scope':'BOUNDED_MANUAL_CAMERA_CHIP_ID_PROBE','capture_requested':False,
            'camera_capture_qualified':False,'expected_chip_id':0x38e5,'probe_bound':False,'runtime_suspended':False}
    attempted=False;cleanup=[]
    def targets():
        result=[]
        for path in Path('/sys/bus/i2c/devices').glob('*-002d'):
            node=path/'of_node'
            if node.exists() and str(node.resolve()).endswith(TARGET):result.append(path)
        if len(result)>1:raise ValueError('Ambiguous camera probe target')
        return result
    try:
        roots=['camcc_sm8750','cambistmclkcc_sm8750','i2c_qcom_cci','s5kjn5']
        ordered=audio.module_order((modules/'modules.dep').read_text(),roots)
        attempted=True
        for name in ordered:audio.run(['modprobe','--ignore-install',name],8)
        until=time.monotonic()+6
        while time.monotonic()<until:
            paths=targets()
            if paths and (paths[0]/'driver').exists():break
            time.sleep(.2)
        else:raise ValueError('S5KJN5 did not bind after its hardware chip-ID check')
        path=paths[0]
        if (path/'driver').resolve().name!='s5kjn5':raise ValueError('Wrong bound sensor driver')
        report['probe_bound']=True
        until=time.monotonic()+5
        while time.monotonic()<until:
            if (path/'power/runtime_status').read_text().strip()=='suspended':
                report['runtime_suspended']=True;break
            time.sleep(.2)
        if not report['runtime_suspended']:raise ValueError('Sensor failed to autosuspend after probe')
    finally:
        if attempted:
            for name in ('s5kjn5','i2c_qcom_cci'):
                try:
                    if Path('/sys/module',name).exists():audio.run(['modprobe','--remove',name],8)
                except Exception:cleanup.append(name)
        report['camera_modules_unloaded']=not any(Path('/sys/module',name).exists() for name in ('s5kjn5','i2c_qcom_cci'))
        report['camera_i2c_target_removed']=not targets()
        report['cleanup_pass']=not cleanup and report['camera_modules_unloaded'] and report['camera_i2c_target_removed']
        (STATE/'result.json').write_text(json.dumps(report,sort_keys=True)+'\n')
        print('CAMERA_CHIP_ID_RESULT '+json.dumps(report,sort_keys=True),flush=True)
        if not report['cleanup_pass']:raise ValueError('Camera teardown incomplete')
    if not report['probe_bound']:raise ValueError('Camera probe incomplete')


if __name__=='__main__':main()
