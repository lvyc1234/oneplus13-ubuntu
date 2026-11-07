#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Measure bounded desktop volume steps through the phone microphones in RAM."""
import importlib.util,json,os,re,subprocess,time,wave
from pathlib import Path


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def evaluate(measurements):
    required={'muted','20','50','100'}
    if set(measurements)!=required:
        raise ValueError('Incomplete desktop volume measurement')
    # Compare the same physical microphone channel and tone at every step.
    best=max(range(2),key=lambda c:sum(measurements['100']['amplitudes'][c]))
    rows=[]
    for index,freq in enumerate((1000,1500)):
        levels={name:data['amplitudes'][best][index] for name,data in measurements.items()}
        rows.append({'frequency_hz':freq,'microphone_channel':best,'amplitudes':levels,
                     'increases_with_volume':levels['20']<levels['50']<levels['100'],
                     'live_to_mute_ratio':levels['100']/max(levels['muted'],1e-9)})
    clipping=sum(item['clipped_samples'] for item in measurements.values())
    return {'frequency_responses':rows,'clipped_samples':clipping,
            'acoustic_system_volume_pass':not clipping and all(
                row['increases_with_volume'] and row['live_to_mute_ratio']>=8
                for row in rows),'absolute_loudness_or_dynamic_protection_qualified':False}


def main():
    desktop=load('desktop_audio','/usr/libexec/oneplus13-ram-desktop-audio.py')
    audio=load('speaker','/usr/libexec/oneplus13-ram-audio-check.py')
    mic=load('microphone','/usr/libexec/oneplus13-ram-microphone-check.py')
    acoustic=load('acoustic','/usr/libexec/oneplus13-ram-acoustic-check.py')
    _,card=desktop.guard()
    state=Path('/run/oneplus13/desktop-volume-check');state.mkdir(mode=0o700,exist_ok=False)
    node=(desktop.STATE/'pipewire-node-id').read_text().strip()
    if not re.fullmatch(r'[0-9]{1,8}',node):raise ValueError('Missing explicit speaker node')
    def user(args):return desktop.run(args,timeout=6,user=True)
    saved=[]
    for name,target in mic.ROUTES:
        block=audio.run(['amixer','-c',card,'cget','name='+name])
        value=re.search(r'^\s*: values=([^\n]+)$',block,re.M)
        if not value or not re.fullmatch(r'[0-9,]+|(?:on|off)(?:,(?:on|off))*',value[1]):
            raise ValueError('Unknown microphone control')
        options=re.findall(r"Item #\d+ '([^']+)'",block)
        if options and target not in options:raise ValueError('Unknown capture enum')
        saved.append((name,value[1],target))
    uid=1000
    fixture=Path('/run/user/1000/oneplus13-volume-check.wav')
    if fixture.exists():raise ValueError('Volume fixture already exists')
    raw=state/'capture.raw';changed=[];measurements={};recorder=None;errors=[]
    try:
        with wave.open(str(fixture),'wb') as stream:
            stream.setparams((2,2,48000,0,'NONE','not compressed'))
            stream.writeframes(acoustic.signal())
        os.chown(fixture,uid,uid);fixture.chmod(0o600)
        for name,old,target in saved:
            changed.append((name,old));audio.run(['amixer','-q','-c',card,'cset','name='+name,target])
        for name,volume in [('muted',.2),('20',.2),('50',.5),('100',1.0)]:
            user(['wpctl','set-volume','--limit','1',node,str(volume)])
            user(['wpctl','set-mute',node,'1' if name=='muted' else '0'])
            readback=user(['wpctl','get-volume',node]).strip()
            raw.unlink(missing_ok=True)
            with (state/'capture.private.log').open('ab') as log:
                recorder=subprocess.Popen(['arecord','-q','-D','hw:'+card+',2','-t','raw',
                    '-f','S24_LE','-r','48000','-c','2','-d','2',str(raw)],stderr=log)
                time.sleep(.2)
                user(['pw-play','--target','oneplus13_speakers',str(fixture)])
                if recorder.wait(timeout=4):raise ValueError('Volume capture failed')
                recorder=None
            if raw.stat().st_size!=768000:raise ValueError('Incomplete bounded capture')
            amplitudes,channels=acoustic.analyze_capture(raw.read_bytes())
            measurements[name]={'volume_readback':readback,
                'amplitudes':[[amplitudes[c,f] for f in (1000,1500)] for c in range(2)],
                'clipped_samples':sum(abs(v)>=.999999 for ch in channels for v in ch)}
            raw.unlink()
            print('DESKTOP_VOLUME_STEP '+name+' '+readback,flush=True)
    finally:
        if recorder is not None:
            recorder.kill();recorder.wait(timeout=3)
        for args in (['wpctl','set-mute',node,'1'],['wpctl','set-volume',node,'0']):
            try:user(args)
            except Exception:errors.append('software mute')
        for name,old in reversed(changed):
            try:audio.run(['amixer','-q','-c',card,'cset','name='+name,old])
            except Exception:errors.append(name)
        raw.unlink(missing_ok=True);fixture.unlink(missing_ok=True)
    if errors:raise ValueError('Desktop volume cleanup failed')
    result={'scope':'RAM_DESKTOP_SYSTEM_VOLUME_ACOUSTIC_MEASUREMENT',
            'hardware_level':1023,'measurements':measurements,'raw_audio_deleted':True,
            'capture_controls_restored':True,'software_muted_at_exit':True,**evaluate(measurements)}
    (state/'result.json').write_text(json.dumps(result,sort_keys=True)+'\n')
    print('DESKTOP_VOLUME_ACOUSTIC_RESULT '+json.dumps(result,sort_keys=True),flush=True)
    return 0 if result['acoustic_system_volume_pass'] else 1


if __name__=='__main__':raise SystemExit(main())
