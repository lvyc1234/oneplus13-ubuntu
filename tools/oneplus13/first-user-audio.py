#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Connect the existing guarded RAM audio path to GDM's actual first user.

The bounded RAM profile retains real microphone/acoustic prechecks. It neither
creates users nor fabricates their result files. No captured audio is retained.
Persistent audio policy and dynamic speaker protection remain separate work.
"""
import importlib.util,json,os,subprocess,sys,time
from pathlib import Path

LIB=Path('/usr/libexec')
EVIDENCE=Path('/run/evidence/first-user-audio.json')


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def run(args,seconds=8):
    result=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=seconds)
    if result.returncode:
        raise ValueError('First-user audio stage failed: '+Path(str(args[0])).name+
                         ' rc='+str(result.returncode)+' '+result.stderr[-1200:])
    return result.stdout


def ordinary_session(uid):
    for line in run(['loginctl','list-sessions','--no-legend','--no-pager']).splitlines():
        fields=line.split()
        if len(fields)<2 or fields[1]!=str(uid):continue
        result=run(['loginctl','show-session',fields[0],'-p','Type','-p','Class','-p','Remote','-p','Active'])
        values=dict(row.split('=',1) for row in result.splitlines() if '=' in row)
        if all(values.get(k)==v for k,v in [('Type','wayland'),('Class','user'),('Remote','no'),('Active','yes')]):
            return True
    return False


def stop(desktop,module_path=Path('/sys/module/snd_soc_aw88461'),
         i2c_root=Path('/sys/bus/i2c/devices')):
    route_enabled=any(path.exists() for path in (desktop.RULE,desktop.NODE,desktop.MODULE))
    if route_enabled:
        _,card=desktop.guard();desktop.disable(card)
    if not module_path.exists():
        return dict(cleanup_complete=True,amplifier_module_not_loaded=True)
    audio=load('phone_audio_health',LIB/'oneplus13-ram-audio-check.py')
    until=time.monotonic()+7
    while True:
        paths=sorted(i2c_root.glob('*/phone_state'))
        states=[audio.amp_state(path.read_text(),0) for path in paths]
        if len(states)==2 and {s['channel'] for s in states}=={0,1} and all(not s['powered'] for s in states):
            return dict(cleanup_complete=True,amplifiers_off=True,desktop_route_was_enabled=route_enabled)
        if time.monotonic()>until:raise ValueError('Desktop amplifier stop not verified')
        time.sleep(.2)


def main():
    if sys.argv[1:] not in [['start'],['stop']]:raise ValueError('Expected start or stop')
    if os.geteuid() or not Path('/run/oneplus13/ram-session').is_file():raise ValueError('Root RAM profile required')
    desktop=load('phone_desktop_audio',LIB/'oneplus13-ram-desktop-audio.py')
    desktop.guard(require_card=False)
    if sys.argv[1]=='stop':
        print('FIRST_USER_AUDIO_STOP '+json.dumps(stop(desktop)),flush=True);return
    owner=desktop.desktop_user()
    until=time.monotonic()+90
    while not (desktop.user_bus().exists() and ordinary_session(owner.pw_uid)):
        if time.monotonic()>until:raise ValueError('Active local first-user Wayland session required')
        time.sleep(1)
    native=json.loads(Path('/usr/lib/oneplus13-desktop/runtime-bindings.private.json').read_text())
    results={}
    for label,filename,seconds,folder,required in [
        ('microphone','oneplus13-ram-microphone-check.py',100,'microphone-check',
         ['bounded_nonzero_capture_pass','controls_restored','raw_audio_deleted']),
        ('acoustic','oneplus13-ram-acoustic-check.py',55,'acoustic-check',
         ['correlated_two_speaker_response_pass','controls_restored','raw_audio_deleted','amplifiers_stopped']),
    ]:
        proof=Path('/run/oneplus13')/folder/'result.json'
        if not proof.exists():
            run(['python3',LIB/filename,'--release',os.uname().release,
                 '--policy-sha256',native['MODULE_POLICY_SHA256']],seconds)
        results[label]=json.loads(proof.read_text())
        if not all(results[label].get(key) is True for key in required):
            raise ValueError('Real native precheck failed: '+label)
    _,card=desktop.guard()
    try:results['desktop']=desktop.enable(card)
    except BaseException:
        stop(desktop);raise
    result=dict(scope='BOUNDED_RAM_FIRST_USER_AUDIO_WITH_NATIVE_PRECHECKS',pass_=True,
                ordinary_user=True,account_created=False,raw_audio_retained=False,**results)
    result['pass']=result.pop('pass_')
    EVIDENCE.parent.mkdir(exist_ok=True);EVIDENCE.write_text(json.dumps(result)+'\n')
    print('FIRST_USER_AUDIO_RESULT '+json.dumps(result),flush=True)


if __name__=='__main__':main()
