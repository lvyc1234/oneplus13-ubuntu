#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""One bounded native capture; raw audio stays in RAM and is deleted."""
import argparse,hashlib,importlib.util,json,math,os,re,struct,subprocess,time
from pathlib import Path
CARD='OnePlus13-Audio-Probe'
STATE=Path('/run/oneplus13/microphone-check')
POLICY=Path('/etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf')
SOURCE_COMPAT=b'oneplus,dodge\0qcom,sm8750\0'
SOURCE_MODEL=b'OnePlus 13 (AA569 native display experimental candidate)\0'
ABL_COMPAT=b'qcom,sun-mtp\0qcom,sun\0qcom,sunp-mtp\0qcom,sunp\0qcom,mtp\0'
ABL_MODEL=b'Qualcomm Technologies, Inc. Sun MTP,dodge\0'

def validate_board(root=Path('/proc/device-tree')):
    def read(path):return (root/path).read_bytes()
    def cells(path):
        raw=read(path)
        if len(raw)%4:raise ValueError('Malformed live audio property')
        return list(struct.unpack('>'+'I'*(len(raw)//4),raw))
    identity=(read('compatible'),read('model'))
    if identity==(SOURCE_COMPAT,SOURCE_MODEL):kind='SOURCE_ONEPLUS13'
    elif identity==(ABL_COMPAT,ABL_MODEL):kind='C93_ABL_SUN_DODGE'
    else:raise ValueError('Wrong board identity pair')
    supply='soc@0/rsc@16500000/regulators-4/ldo2/'
    if cells('wcd939x/vdd-px-supply')!=cells(supply+'phandle'):raise ValueError('Wrong live WCD PX provider')
    for key in ('regulator-min-microvolt','regulator-max-microvolt'):
        if cells(supply+key)!=[1200000]:raise ValueError('Wrong live WCD PX voltage')
    for n in range(1,5):
        if cells(f'wcd939x/qcom,micbias{n}-microvolt')!=[2700000]:raise ValueError('Wrong live microphone bias')
    reset=cells('wcd939x/reset-gpios')
    if reset!=cells('soc@0/pinctrl@f100000/phandle')+[101,1]:raise ValueError('Wrong live WCD reset')
    if cells('sound/wcd-capture-dai-link/cpu/sound-dai')[1:]!=[120]:raise ValueError('Wrong live TX3 port')
    return kind
ROUTES=[('ADC1 MUX','CH1_AMIC1'),('ADC4 MUX','CH4_AMIC5'),
    ('TX0 MODE','ADC_HIFI'),('TX3 MODE','ADC_HIFI'),
    ('ADC1 Volume','6'),('ADC4 Volume','6'),('ADC1 Switch','1'),('ADC4 Switch','1'),
    ('ADC1_MIXER Switch','1'),('ADC4_MIXER Switch','1'),
    ('TX DEC0 MUX','SWR_MIC'),('TX DEC1 MUX','SWR_MIC'),
    ('TX SMIC MUX0','SWR_MIC0'),('TX SMIC MUX1','SWR_MIC3'),
    ('DEC0 MODE','ADC_DEFAULT'),('DEC1 MODE','ADC_DEFAULT'),
    ('TX_DEC0 Volume','84'),('TX_DEC1 Volume','84'),
    ('TX_AIF1_CAP Mixer DEC0','1'),('TX_AIF1_CAP Mixer DEC1','1'),
    ('MultiMedia3 Mixer TX_CODEC_DMA_TX_3','1')]
def summarize(raw):
    if len(raw)!=48000*2*2*4:raise ValueError('Incomplete 48kHz two-second stereo S24_LE capture')
    channels=[[],[]]
    for n,(value,) in enumerate(struct.iter_unpack('<i',raw)):
        # ALSA S24_LE occupies a 32-bit word with 24 significant low bits.
        value&=0xffffff
        if value&0x800000:value-=0x1000000
        channels[n%2].append(value)
    result=[]
    for values in channels:
        distinct=len(set(values));peak=max(abs(v) for v in values)/8388608
        rms=math.sqrt(sum(v*v for v in values)/len(values))/8388608
        clip=sum(abs(v)>=8388607 for v in values)
        result.append({'frames':len(values),'distinct_values':distinct,'peak':peak,'rms':rms,'clipped_samples':clip})
    valid=all(s['distinct_values']>=16 and 1e-7<s['rms']<.25 and s['clipped_samples']==0 for s in result)
    return {'channels':result,'bounded_nonzero_capture_pass':valid,'physical_microphone_or_acoustic_response_accepted':False}
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',required=True);p.add_argument('--policy-sha256',required=True)
    a=p.parse_args()
    if os.geteuid()!=0 or os.uname().release!=a.release:raise ValueError('Wrong microphone root/kernel')
    if not Path('/run/oneplus13/ram-session').is_file():raise ValueError('Protected RAM session required')
    if not re.search(r'^overlay / overlay ',Path('/proc/mounts').read_text(),re.M):raise ValueError('RAM root required')
    for d in Path('/sys/class/block').iterdir():
        if not d.name.startswith(('ram','zram','loop')) and (d/'ro').read_text().strip()!='1':raise ValueError('Writable physical storage')
    s=importlib.util.spec_from_file_location('speaker_guard','/usr/libexec/oneplus13-ram-audio-check.py')
    audio=importlib.util.module_from_spec(s);s.loader.exec_module(audio)
    for unit in ['oneplus13-ram-guard.service','oneplus13-ram-deadline.timer']:
        audio.run(['systemctl','is-active','--quiet',unit])
    audio.validate_policy(POLICY.read_bytes(),a.policy_sha256)
    board_identity=validate_board()
    if Path('/proc/device-tree/sound/model').read_bytes().rstrip(b'\0').decode()!=CARD:raise ValueError('Separate microphone boot required')
    STATE.mkdir(mode=0o700,exist_ok=False)
    roots=['snd_soc_lpass_va_macro','snd_soc_lpass_rx_macro','snd_soc_lpass_tx_macro',
        'soundwire_qcom','snd_soc_wcd939x_sdw','snd_soc_wcd939x','snd_soc_aw88461',
        'snd_q6apm','q6apm_dai','q6apm_lpass_dais','q6prm','q6prm_clocks','snd_soc_sc8280xp']
    dep=Path('/usr/lib/modules')/a.release/'modules.dep'
    for name in audio.module_order(dep.read_text(),roots):audio.run(['modprobe','--ignore-install',name],8)
    until=time.monotonic()+15
    while True:
        cards=Path('/proc/asound/cards').read_text()
        found=re.findall(r'^\s*(\d+) \[.*\]: .* - '+re.escape(CARD)+r'$',cards,re.M)
        if len(found)==1:break
        if time.monotonic()>=until:raise ValueError('Native microphone card did not register')
        time.sleep(.2)
    card=found[0]
    dump=audio.run(['amixer','-c',card,'contents'])
    (STATE/'controls.private.txt').write_text(dump)
    saved=[]
    # Preflight the complete control schema before the first mixer change.
    for name,target in ROUTES:
        block=audio.run(['amixer','-c',card,'cget','name='+name])
        value=re.search(r'^\s*: values=([^\n]+)$',block,re.M)
        if not value or not re.fullmatch(r'[0-9,]+|(?:on|off)(?:,(?:on|off))*',value[1]):raise ValueError('Unknown capture control: '+name)
        options=re.findall(r"Item #\d+ '([^']+)'",block)
        if options and target not in options:raise ValueError('Capture enum unavailable: '+name)
        saved.append((name,value[1],target))
    raw=STATE/'capture.s24le';changed=[];answer=None
    try:
        for name,old,target in saved:
            changed.append((name,old))
            audio.run(['amixer','-q','-c',card,'cset','name='+name,target])
        proc=subprocess.run(['arecord','-q','-D','hw:'+card+',2','-t','raw','-f','S24_LE',
            '-r','48000','-c','2','-d','2',str(raw)],capture_output=True,text=True,timeout=8)
        (STATE/'arecord.private.log').write_text(proc.stdout+proc.stderr)
        if proc.returncode:raise ValueError('Native capture failed rc='+str(proc.returncode))
        if raw.stat().st_size>1024*1024:raise ValueError('Capture exceeds RAM bound')
        answer=summarize(raw.read_bytes())
    finally:
        errors=[]
        for name,old in reversed(changed):
            try:audio.run(['amixer','-q','-c',card,'cset','name='+name,old])
            except Exception:errors.append(name)
        raw.unlink(missing_ok=True)
        if errors:raise ValueError('Capture control restoration failed: '+','.join(errors))
    report={'scope':'BOUNDED_NATIVE_MICROPHONE_CAPTURE_CANDIDATE',
        'raw_audio_deleted':True,'controls_restored':True,'format':'S24_LE','rate_hz':48000,
        'seconds':2,'adc_gain':6,'tx_dec_gain':84,'board_identity':board_identity,**answer}
    (STATE/'result.json').write_text(json.dumps(report,sort_keys=True)+'\n')
    print('RAM_MICROPHONE_RESULT '+json.dumps(report,sort_keys=True),flush=True)
if __name__=='__main__':main()
