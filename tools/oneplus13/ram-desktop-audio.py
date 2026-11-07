#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Explicit, reversible RAM speaker integration with the desktop volume mixer."""
import argparse,hashlib,json,os,pwd,re,shutil,subprocess,time
from pathlib import Path
ROOT=Path('/usr/lib/oneplus13-audio')
BINDINGS=ROOT/'desktop-bindings.json'
RULE=Path('/etc/wireplumber/wireplumber.conf.d/95-oneplus13-desktop-audio.conf')
CARD='OnePlus13-Audio-Probe'
UNITS=['pipewire.service','pipewire-pulse.service','wireplumber.service']
STATE=Path('/run/oneplus13/desktop-audio')
MODULE=STATE/'pause-module-id'
NODE=STATE/'pipewire-node-id'
SESSION=STATE/'user-bus-inode'


def desktop_user():
    # First-user creation is owned by GDM. Never create a legacy test account.
    user=pwd.getpwuid(1000)
    if not 1000<=user.pw_uid<60000:raise ValueError('Ordinary first user required')
    return user


def user_bus():
    return Path('/run/user')/str(desktop_user().pw_uid)/'bus'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def run(args,timeout=8,user=False):
    if user:
        owner=desktop_user();uid=owner.pw_uid
        args=['runuser','-u',owner.pw_name,'--','env','XDG_RUNTIME_DIR=/run/user/'+str(uid),
              'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus',
              'PULSE_SERVER=unix:/run/user/'+str(uid)+'/pulse/native',*args]
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise ValueError('Desktop audio command failed: '+Path(args[0]).name+' rc='+str(p.returncode))
    if len(p.stdout)>2*1024*1024:raise ValueError('Oversized desktop audio output')
    return p.stdout

def validate_policy(actual,expected):
    if hashlib.sha256(actual).hexdigest()==expected:return
    original=actual
    for name in ('cec','ubwc_config'):
        rule=('install '+name+' /bin/true\n').encode()
        if original.count(rule)!=1:raise ValueError('Unknown post-display policy')
        original=original.replace(rule,('install '+name+' /bin/false\n').encode(),1)
    if hashlib.sha256(original).hexdigest()!=expected:raise ValueError('Peripheral denial changed')

def guard(require_card=True):
    doc=json.loads(BINDINGS.read_text())
    if os.geteuid()!=0 or doc['kernel_release']!=os.uname().release:raise ValueError('Wrong root/kernel')
    if not Path('/run/oneplus13/ram-session').is_file() or not any(r.split()[1:3]==['/','overlay'] for r in Path('/proc/mounts').read_text().splitlines()):
        raise ValueError('Protected RAM root required')
    for p in Path('/sys/class/block').iterdir():
        if not p.name.startswith(('ram','loop','zram')) and (p/'ro').read_text().strip()!='1':raise ValueError('Writable physical storage')
    for unit in ('oneplus13-ram-guard.service','oneplus13-ram-deadline.timer'):run(['systemctl','is-active','--quiet',unit])
    for name,digest in doc['files'].items():
        if sha(Path('/')/name)!=digest:raise ValueError('Desktop audio source changed: '+name)
    module=Path('/usr/lib/modules')/doc['kernel_release']/'kernel/sound/soc/codecs/snd-soc-aw88461.ko'
    if sha(module)!=doc['amplifier_module_sha256']:raise ValueError('Wrong amplifier module')
    validate_policy(Path('/etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf').read_bytes(),doc['original_policy_sha256'])
    if Path('/proc/device-tree/sound/model').read_bytes()!=CARD.encode()+b'\0':raise ValueError('Wrong audio boot alternative')
    if not require_card:return doc,None
    cards=re.findall(r'^\s*(\d+) \[.*\]: .* - '+re.escape(CARD)+r'$',Path('/proc/asound/cards').read_text(),re.M)
    if len(cards)!=1:raise ValueError('Unique phone card required')
    return doc,cards[0]

def mixer(card,name,value):run(['amixer','-q','-c',card,'cset','name='+name,value])

def card_property_match(props,card):
    values={str(props.get(key,'')) for key in
            ('api.alsa.card','api.alsa.pcm.card','api.alsa.card.name',
             'api.alsa.card.longname','api.alsa.path','device.description','node.nick')}
    return (card in values or CARD in values or
            f'hw:{card},' in ' '.join(values) or
            'oneplus13' in ' '.join(values).lower())


def desktop_sinks(objects,card):
    devices={obj.get('id') for obj in objects
             if obj.get('type')=='PipeWire:Interface:Device'
             and card_property_match(obj.get('info',{}).get('props',{}),card)}
    sinks=[]
    for obj in objects:
        if obj.get('type')!='PipeWire:Interface:Node':
            continue
        props=obj.get('info',{}).get('props',{})
        if props.get('media.class')!='Audio/Sink':
            continue
        if props.get('device.id') in devices or card_property_match(props,card):
            sinks.append({'id':obj['id'],'name':props.get('node.name'),'properties':props})
    return sinks


def prepare_explicit_route(card):
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    # UCM remains the route/control source when WirePlumber declines to expose
    # an unqualified ALSA device. The fallback sink still traverses that PCM.
    run(['alsaucm','-c','hw:'+card,'set','_verb','HiFi'],8)
    # Apply the exact reviewed UCM EnableSequence. Some WirePlumber versions
    # reject the card before the UCM device verb can be exposed.
    mixer(card,'Amp0 Playback Volume','1023')
    mixer(card,'Amp1 Playback Volume','1023')
    mixer(card,'Secondary MI2S Audio Mixer MultiMedia1','on,on')


def load_native_sink(card):
    prepare_explicit_route(card)
    run(['pw-cli','create-node','spa-node-factory',
         'factory.name=api.alsa.pcm.sink',
         'api.alsa.path="hw:'+card+',0"','node.name=oneplus13_speakers',
         'node.description="OnePlus 13 speakers"','media.class=Audio/Sink',
         'object.linger=true'],8,user=True)
    time.sleep(15)
    sinks=desktop_sinks(json.loads(run(['pw-dump'],user=True)),card)
    if len(sinks)!=1:raise ValueError('Native ALSA sink did not appear')
    with NODE.open('x') as stream:stream.write(str(sinks[0]['id'])+'\n')
    NODE.chmod(0o600)
    return int(sinks[0]['id'])


def load_pulse_sink(card):
    prepare_explicit_route(card)
    owner=desktop_user();uid=owner.pw_uid
    command=['runuser','-u',owner.pw_name,'--','env',
             'XDG_RUNTIME_DIR=/run/user/'+str(uid),
             'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus',
             'PULSE_SERVER=unix:/run/user/'+str(uid)+'/pulse/native',
             'pactl','load-module','module-alsa-sink',
             'device=hw:'+card+',0','sink_name=oneplus13_speakers',
             'rate=48000','channels=2','format=s16le']
    completed=subprocess.run(command,capture_output=True,text=True,timeout=8)
    combined=(completed.stdout+'\n'+completed.stderr)[:8192]
    (STATE/'pulse-load.private.txt').write_text(combined)
    (STATE/'pulse-load.private.txt').chmod(0o600)
    if completed.returncode:raise ValueError('PulseAudio sink module failed')
    until=time.monotonic()+5
    while True:
        sinks=desktop_sinks(json.loads(run(['pw-dump'],user=True)),card)
        if len(sinks)==1:break
        if time.monotonic()>until:
            (STATE/'pulse-inventory.private.json').write_text(json.dumps({
                'objects':json.loads(run(['pw-dump'],user=True)),
                'matching_sinks':sinks},indent=2)+'\n')
            raise ValueError('Pulse ALSA sink did not appear')
        time.sleep(.3)
    with NODE.open('x') as stream:stream.write(str(sinks[0]['id'])+'\n')
    NODE.chmod(0o600)
    return int(sinks[0]['id'])


def disable(card):
    errors=[]
    # A vanished/replaced user bus means its Pulse objects no longer belong to
    # this session. Do not send stale numeric IDs into a later login's server.
    bus=user_bus()
    same_bus=bus.exists() and (not SESSION.exists() or
        SESSION.read_text().strip()==str(bus.stat().st_ino))
    try:
        if same_bus and Path('/usr/lib/systemd/user/oneplus13-stereo-filter.service').exists():
            run(['systemctl','--user','stop','oneplus13-stereo-orientation.service',
                 'oneplus13-stereo-filter.service'],15,user=True)
        if MODULE.exists():
            module=MODULE.read_text().strip()
            if not re.fullmatch(r'[0-9]{1,8}',module):raise ValueError('Unknown desktop audio module')
            if same_bus:run(['pactl','unload-module',module],8,user=True)
            MODULE.unlink()
        if NODE.exists():
            node=NODE.read_text().strip()
            if not re.fullmatch(r'[0-9]{1,8}',node):raise ValueError('Unknown desktop audio node')
            if same_bus:
                objects=json.loads(run(['pw-dump'],user=True))
                if any(str(obj.get('id'))==node for obj in objects):
                    run(['pw-cli','destroy',node],8,user=True)
            NODE.unlink()
        if RULE.exists():
            if RULE.is_symlink() or RULE.read_bytes()!=(ROOT/RULE.name).read_bytes():raise ValueError('Unknown desktop rule')
            RULE.unlink()
        if same_bus:run(['systemctl','--user','restart',*UNITS],timeout=15,user=True)
        SESSION.unlink(missing_ok=True)
    except Exception as e:errors.append(str(e))
    for name,value in [('Amp0 Playback Volume','0'),('Amp1 Playback Volume','0'),('Secondary MI2S Audio Mixer MultiMedia1','off,off')]:
        try:mixer(card,name,value)
        except Exception as e:errors.append(str(e))
    if errors:raise ValueError('Desktop audio cleanup failed: '+'; '.join(errors))

def stereo_sink(native):
    """Move the real speaker streams behind a unity permutation, muted first.

    GNOME's volume remains50% on the new sink, with the physical sink at unity;
    amp controls and software ceilings are unchanged. Never reroute Bluetooth
    or other hardware sinks. Stop cleanup returns the native software cap.
    """
    # UCM and the Pulse fallback can use different node names. Resolve the
    # actual verified target in the user session, never a guessed default.
    run(['/usr/libexec/oneplus13-stereo-session.py','prepare',str(native['id']),native['name']],user=True)
    run(['wpctl','set-mute',str(native['id']),'1'],user=True)
    run(['systemctl','--user','start','oneplus13-stereo-filter.service',
         'oneplus13-stereo-orientation.service'],15,user=True)
    end=time.monotonic()+8
    while True:
        nodes=[o for o in json.loads(run(['pw-dump'],user=True))
            if o.get('type')=='PipeWire:Interface:Node']
        inputs=[o for o in nodes
            if o.get('info',{}).get('props',{}).get('node.name')=='oneplus13_stereo']
        outputs=[o for o in nodes
            if o.get('info',{}).get('props',{}).get('node.name')=='oneplus13_stereo_out']
        if len(inputs)==len(outputs)==1:break
        if time.monotonic()>end:raise ValueError('Phone stereo filter unavailable')
        time.sleep(.2)
    sink={'id':inputs[0]['id'],'name':'oneplus13_stereo'}
    output_serial=outputs[0]['info']['props'].get('object.serial')
    if type(output_serial) is not int or output_serial<0:
        raise ValueError('Phone stereo output identity unavailable')
    run(['wpctl','set-volume','--limit','1',str(sink['id']),'0.5'],user=True)
    run(['wpctl','set-mute',str(sink['id']),'0'],user=True)
    run(['wpctl','set-default',str(sink['id'])],user=True)
    run(['pactl','set-default-sink',sink['name']],user=True)
    pulse=[o for o in json.loads(run(['pactl','--format=json','list','sinks'],user=True))
        if o.get('name')==native['name']]
    if len(pulse)!=1:raise ValueError('Unique native Pulse sink required')
    for stream in json.loads(run(['pactl','--format=json','list','sink-inputs'],user=True)):
        # PipeWire Pulse exposes the filter output as a sink input too.
        # Moving it into the filter's capture sink creates a feedback loop.
        if (stream.get('index')==output_serial
                or stream.get('properties',{}).get('node.name')=='oneplus13_stereo_out'):
            continue
        if stream.get('sink')==pulse[0]['index']:
            run(['pactl','move-sink-input',str(stream['index']),sink['name']],user=True)
    run(['wpctl','set-volume','--limit','1',str(native['id']),'1'],user=True)
    run(['wpctl','set-mute',str(native['id']),'0'],user=True)
    return sink


def enable(card):
    for folder,keys in [('microphone-check',('bounded_nonzero_capture_pass','controls_restored','raw_audio_deleted')),
                        ('acoustic-check',('correlated_two_speaker_response_pass','controls_restored','raw_audio_deleted','amplifiers_stopped'))]:
        result=json.loads((Path('/run/oneplus13')/folder/'result.json').read_text())
        if not all(result.get(k) is True for k in keys):raise ValueError('Independent native audio precheck required')
    if RULE.exists():raise ValueError('Desktop audio already attempted')
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    SESSION.write_text(str(user_bus().stat().st_ino)+'\n');SESSION.chmod(0o600)
    if 'HiFi' not in run(['alsaucm','-c','hw:'+card,'list','_verbs']):raise ValueError('Phone UCM profile unavailable')
    # Keep GNOME from exposing amplification beyond the software-unity ceiling.
    run(['gsettings','set','org.gnome.desktop.sound','allow-volume-above-100-percent','false'],user=True)
    shutil.copyfile(ROOT/RULE.name,RULE)
    run(['systemctl','--user','restart',*UNITS],timeout=15,user=True)
    until=time.monotonic()+8
    sink_method='wireplumber'
    while True:
        objects=json.loads(run(['pw-dump'],user=True))
        sinks=desktop_sinks(objects,card)
        if len(sinks)==1:break
        if time.monotonic()>until:
            load_pulse_sink(card);sink_method='pulse-module'
            until=time.monotonic()+8
            objects=json.loads(run(['pw-dump'],user=True))
            sinks=desktop_sinks(objects,card)
            if len(sinks)==1:break
            (STATE/'inventory.private.json').write_text(json.dumps({
                'objects':objects,'matching_sinks':sinks},indent=2)+'\n')
            raise ValueError('Unique desktop speaker sink did not appear')
        time.sleep(.3)
    sink=sinks[0]
    if Path('/usr/lib/systemd/user/oneplus13-stereo-filter.service').exists():
        sink=stereo_sink(sink)
    run(['wpctl','set-default',str(sink['id'])],user=True)
    try:
        run(['pactl','set-default-sink',sink['name']],user=True)
    except ValueError:
        pass
    run(['wpctl','set-volume','--limit','1',str(sink['id']),'0.5'],user=True)
    run(['wpctl','set-mute',str(sink['id']),'0'],user=True)
    return {'scope':'EXPLICIT_RAM_PIPEWIRE_SPEAKER_CANDIDATE','sink_id':sink['id'],'sink_name':sink['name'],
            'sink_method':sink_method,'ucm_speakers':True,'desktop_software_volume':True,'initial_volume':0.5,
            'hardware_test_level':1023,'extra_profile_attenuation_db':0,
            'dynamic_protection_qualified':False,'normal_desktop_playback_qualified':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['enable','disable']);args=p.parse_args()
    doc,card=guard()
    if args.mode=='disable':disable(card);print('RAM_DESKTOP_AUDIO_DISABLED');return
    try:result=enable(card)
    except Exception:
        disable(card)
        raise
    print('RAM_DESKTOP_AUDIO_RESULT '+json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
