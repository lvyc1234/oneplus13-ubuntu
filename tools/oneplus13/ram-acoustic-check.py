#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded native tone/capture correlation, after a successful microphone test."""
import argparse,hashlib,importlib.util,json,math,os,re,struct,subprocess,time
from pathlib import Path
STATE=Path('/run/oneplus13/acoustic-check')
CARD='OnePlus13-Audio-Probe'
RATE=48000
VOLUME=640
SIGNAL_PEAK_DBFS=-6
FREQUENCIES=(1000,1500)
PHASES=(('muted',(0,0)),('amp0_only',(VOLUME,0)),('amp1_only',(0,VOLUME)),('both',(VOLUME,VOLUME)))
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def signal():
 peak=round(32767*10**(SIGNAL_PEAK_DBFS/20));result=bytearray()
 for i in range(RATE*2):
  t=i%RATE;level=min(1.,t/(RATE*.02),(RATE-1-t)/(RATE*.02))
  value=round(peak*level*math.sin(2*math.pi*FREQUENCIES[i//RATE]*t/RATE))
  # Duplicate the fixture to both I2S channels so amplifier isolation does not
  # depend on an unqualified frontend channel-to-slot mapping.
  result.extend(struct.pack('<hh',value,value))
 return bytes(result)
def analyze_capture(raw):
 if len(raw)!=RATE*2*2*4:raise ValueError('Incomplete two-second stereo S24 capture')
 channels=[[],[]]
 for n,(v,) in enumerate(struct.iter_unpack('<i',raw)):
  v&=0xffffff
  if v&0x800000:v-=0x1000000
  channels[n%2].append(v/8388608)
 n=RATE//2
 basis={freq:([math.cos(2*math.pi*freq*i/RATE) for i in range(n)],
              [math.sin(2*math.pi*freq*i/RATE) for i in range(n)]) for freq in FREQUENCIES}
 def amplitude(values,start,freq):
  if not 0<=start<=len(values)-n:raise ValueError('Tone window outside capture')
  data=values[start:start+n]
  c=sum(v*w for v,w in zip(data,basis[freq][0]))
  s=sum(v*w for v,w in zip(data,basis[freq][1]))
  return 2*math.hypot(c,s)/n
 return {(channel,freq):amplitude(values,int((.25 if freq==FREQUENCIES[0] else 1.25)*RATE),freq)
         for channel,values in enumerate(channels) for freq in FREQUENCIES},channels

def analyze(captures):
 names={phase for phase,_ in PHASES}
 if set(captures)!=names:raise ValueError('Missing acoustic phase capture')
 measured={name:analyze_capture(raw) for name,raw in captures.items()}
 quiet=measured['muted'][0];profile=[];isolated=[]
 for phase_name,volumes in PHASES:
  for freq in FREQUENCIES:
   observations=[]
   for channel in (0,1):
    base=quiet[(channel,freq)];live=measured[phase_name][0][(channel,freq)]
    ratio=live/max(base,1e-9)
    observations.append({'microphone_channel':channel,'muted_amplitude':base,'live_amplitude':live,
                         'live_to_muted_ratio':ratio,'pass':live>3e-6 and ratio>=8})
   profile.append({'phase':phase_name,'volumes':volumes,'frequency_hz':freq,'observations':observations,
                   'response_pass':any(v['pass'] for v in observations)})
 for amp,freq,phase_name in ((0,FREQUENCIES[0],'amp0_only'),(1,FREQUENCIES[1],'amp1_only')):
  row=next(v for v in profile if v['phase']==phase_name and v['frequency_hz']==freq)
  isolated.append({'amplifier':amp,'frequency_hz':freq,'observations':row['observations'],'pass':row['response_pass']})
 clipping=[sum(abs(v)>=.999999 for values in (measured[name][1][channel] for name in names) for v in values)
           for channel in (0,1)]
 return {'amp_isolation':isolated,'clipped_samples':clipping,'phase_spectrum':profile,
         'correlated_two_speaker_response_pass':all(v['pass'] for v in isolated) and not any(clipping) and
             all(v['response_pass'] for v in profile if v['phase']=='both'),
         'human_listening_performed':False,'dynamic_protection_qualified':False}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--release',required=True);p.add_argument('--policy-sha256',required=True);a=p.parse_args()
 if os.geteuid()!=0 or os.uname().release!=a.release:raise ValueError('Wrong acoustic root/kernel')
 if not Path('/run/oneplus13/ram-session').is_file() or not re.search(r'^overlay / overlay ',Path('/proc/mounts').read_text(),re.M):raise ValueError('Protected RAM root required')
 for d in Path('/sys/class/block').iterdir():
  if not d.name.startswith(('ram','zram','loop')) and (d/'ro').read_text().strip()!='1':raise ValueError('Writable physical storage')
 if Path('/proc/device-tree/sound/model').read_bytes().rstrip(b'\0').decode()!=CARD:raise ValueError('Microphone alternative required')
 policy=Path('/etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf')
 audio=load('bounded_speaker','/usr/libexec/oneplus13-ram-audio-check.py')
 audio.validate_policy(policy.read_bytes(),a.policy_sha256)
 mic=load('bounded_microphone','/usr/libexec/oneplus13-ram-microphone-check.py')
 for unit in ('oneplus13-ram-guard.service','oneplus13-ram-deadline.timer'):audio.run(['systemctl','is-active','--quiet',unit])
 proof=json.loads((mic.STATE/'result.json').read_text())
 if not all(proof.get(k) is True for k in ('bounded_nonzero_capture_pass','controls_restored','raw_audio_deleted')):raise ValueError('Successful independent microphone precheck required')
 STATE.mkdir(mode=0o700,exist_ok=False)
 cards=Path('/proc/asound/cards').read_text();found=re.findall(r'^\s*(\d+) \[.*\]: .* - '+re.escape(CARD)+r'$',cards,re.M)
 if len(found)!=1:raise ValueError('Unique native audio card required')
 card=found[0];paths=sorted(Path('/sys/bus/i2c/devices').glob('*/phone_state'))
 if len(paths)!=2:raise ValueError('Both amplifier state nodes required')
 before=[audio.amp_state(p.read_text()) for p in paths]
 if {s['channel'] for s in before}!={0,1} or any(s['powered'] for s in before):raise ValueError('Amplifiers must start muted/off')
 saved=[]
 for name,target in mic.ROUTES+[('Secondary MI2S Audio Mixer MultiMedia1','on,on')]:
  block=audio.run(['amixer','-c',card,'cget','name='+name]);value=re.search(r'^\s*: values=([^\n]+)$',block,re.M)
  if not value or not re.fullmatch(r'[0-9,]+|(?:on|off)(?:,(?:on|off))*',value[1]):raise ValueError('Unknown acoustic mixer schema')
  options=re.findall(r"Item #\d+ '([^']+)'",block)
  if options and target not in options:raise ValueError('Acoustic enum unavailable')
  if name.startswith('Secondary') and value[1] not in ('off','off,off'):raise ValueError('Playback route already active')
  if name.startswith('Secondary') and value[1]=='off':target='on'
  saved.append((name,value[1],target))
 controls=[f'Amp{channel} Playback Volume' for channel in (0,1)]
 for name in controls:
  if not re.search(r'^\s*: values=0$',audio.run(['amixer','-c',card,'cget','name='+name]),re.M):raise ValueError('Initial amplifier volume changed')
 raw=STATE/'capture.s24le';tone=STATE/'signal.s16le';wave=signal();tone.write_bytes(wave);tone.chmod(0o600)
 capture_log=(STATE/'arecord.private.log').open('xb');recorder=player=None;changed=[];amp_samples=[];captures={};answer=None;cleanup_errors=[]
 try:
  for name,old,target in saved:
   changed.append((name,old));audio.run(['amixer','-q','-c',card,'cset','name='+name,target])
  for phase_name,volumes in PHASES:
   for name,volume in zip(controls,volumes):
    audio.run(['amixer','-q','-c',card,'cset','name='+name,str(volume)])
   raw.unlink(missing_ok=True)
   recorder=subprocess.Popen(['arecord','-q','-D','hw:'+card+',2','-t','raw','-f','S24_LE',
       '-r','48000','-c','2','--period-size','1024','--buffer-size','4096','-d','2',str(raw)],
       stdout=subprocess.DEVNULL,stderr=capture_log)
   time.sleep(.2)
   player=subprocess.Popen(['aplay','-q','-D','hw:'+card+',0','-t','raw','-f','S16_LE','-r','48000','-c','2','-d','2',str(tone)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
   for _ in range(2):
    time.sleep(.75)
    states=[audio.amp_state(p.read_text(),ffi_volume) for p,ffi_volume in zip(paths,volumes)]
    if not all(audio.active_state(s) for s in states):raise ValueError('Acoustic amplifier state failed')
    amp_samples.append({'phase':phase_name,'volumes':volumes,'states':states})
   _,err=player.communicate(timeout=3)
   if player.returncode:raise ValueError('Native tone playback failed')
   player=None
   _,err=recorder.communicate(timeout=4)
   if recorder.returncode:raise ValueError('Acoustic capture failed')
   recorder=None
   captures[phase_name]=raw.read_bytes()
   if len(captures[phase_name])>4*1024*1024:raise ValueError('Acoustic RAM capture bound exceeded')
   raw.unlink()
   time.sleep(.1)
  answer=analyze(captures)
 finally:
  for proc in (player,recorder):
   if proc is not None:
    if proc.poll() is None:proc.terminate()
    try:proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:proc.kill();proc.communicate(timeout=2)
  capture_log.close()
  for name in controls:
   try:audio.run(['amixer','-q','-c',card,'cset','name='+name,'0'])
   except Exception:cleanup_errors.append(name)
  for name,old in reversed(changed):
   try:audio.run(['amixer','-q','-c',card,'cset','name='+name,old])
   except Exception:cleanup_errors.append(name)
  raw.unlink(missing_ok=True);tone.unlink(missing_ok=True)
  audio.wait_off(paths)
  if cleanup_errors:raise ValueError('Acoustic control restoration failed')
 after=audio.wait_off(paths)
 report={'scope':'BOUNDED_NATIVE_TWO_SPEAKER_MICROPHONE_CORRELATION',
    'test_volume':VOLUME,'signal_peak_dbfs':SIGNAL_PEAK_DBFS,'signal_sha256':hashlib.sha256(wave).hexdigest(),
    'muted_then_live_same_pcm':True,'amp_state_samples':amp_samples,
    'raw_audio_deleted':True,'controls_restored':True,'amplifiers_stopped':not any(s['powered'] for s in after),
    'normal_desktop_playback_qualified':False,**answer}
 (STATE/'result.json').write_text(json.dumps(report,sort_keys=True)+'\n')
 print('RAM_ACOUSTIC_RESULT '+json.dumps(report,sort_keys=True),flush=True)
if __name__=='__main__':main()
