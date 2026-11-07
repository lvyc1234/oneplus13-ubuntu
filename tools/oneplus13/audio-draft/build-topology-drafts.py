#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile/decode separate phone graph drafts without installing or streaming."""
import argparse,hashlib,json,os,re,subprocess,tarfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]

def verify_i2s(text):
    # alsatplg emits a token declaration and one data tuple. Require the
    # actual data tuple value, not a macro name or the token declaration.
    matches=re.findall(r"'device18\.i2s_rx1:tuple0'\s*\{(.*?)\n\t\}",text,re.S)
    if len(matches)!=1:raise ValueError('Missing unique Secondary I2S tuple')
    values=dict(re.findall(r'^\s*token(\d+)\s+(\d+)\s*$',matches[0],re.M))
    if any(values.get(k)!=v for k,v in {'250':'1','251':'0','256':'2'}.items()):
        raise ValueError('Secondary I2S selector differs from phone OTA SD1')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--m4',default='m4');p.add_argument('--alsatplg',default='alsatplg')
    p.add_argument('--tool-library-path',type=Path)
    a=p.parse_args();out=a.output.resolve()
    if out.exists():raise ValueError('Preserve prior topology output')
    out.mkdir(parents=True)
    lock=json.loads((REPO/'packaging/audio/source.json').read_text())
    archive=REPO/'packaging/audio'/lock['archive']
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    if sha(archive)!='58e0fa0ab9702c5237b4bea1f0486bcc8af7470fafd2d4ad8d508754fd269b17':raise ValueError('Original macro source changed')
    with tarfile.open(archive) as tf:
        for m in tf:
            path=Path(m.name)
            if path.is_absolute() or '..' in path.parts or not(m.isfile() or m.isdir()):raise ValueError('Unsafe macro source')
        tf.extractall(out/'source',filter='data')
    macros=out/'source'/lock['directory']
    env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR') if k in os.environ}
    env.update(LANG='C.UTF-8',LC_ALL='C.UTF-8')
    if a.tool_library_path:env['LD_LIBRARY_PATH']=str(a.tool_library_path.resolve())
    def run(command):
        r=subprocess.run([str(x) for x in command],capture_output=True,text=True,env=env,cwd=macros,timeout=30)
        with (out/'build.log').open('a') as f:f.write(json.dumps([str(x) for x in command])+'\n'+r.stdout+r.stderr+'\n')
        if r.returncode:raise ValueError('Topology compiler failed; see build.log')
        return r.stdout
    results=[]
    for name,required,forbidden,sample_format in [
        ('capture-only','TX_CODEC_DMA_TX_3 Capture',['Secondary MI2S','DISPLAY_PORT','WSA_CODEC'],'S24_LE'),
        ('speaker-mi2s-only','Secondary MI2S Playback',['TX_CODEC_DMA_TX_3','DISPLAY_PORT','WSA_CODEC'],'S24_LE'),
        ('speaker-s16-probe','Secondary MI2S Playback',['TX_CODEC_DMA_TX_3','DISPLAY_PORT','WSA_CODEC'],'S16_LE')]:
        config=out/(name+'.conf');binary=out/(name+'.tplg');decoded=out/(name+'.decoded.conf')
        config.write_text(run([a.m4,'-I',macros,HERE/(name+'.m4')]))
        expanded=config.read_text()
        if required not in expanded or any(x in expanded for x in forbidden+['TDM','S32_LE']):raise ValueError('Wrong graph endpoint or copied Pad route')
        run([a.alsatplg,'-c',config,'-o',binary]);run([a.alsatplg,'-d',binary,'-o',decoded])
        text=decoded.read_text()
        if name.startswith('speaker-'):verify_i2s(text)
        if required not in text:raise ValueError('Decoded graph lost required endpoint')
        # Check actual PCM capabilities, not numbers occurring elsewhere in
        # opaque vendor tuples. Reject broadened formats/rates/channels.
        capabilities=re.findall(r"SectionPCMCapabilities\.[^\{]+\{([^}]+)\}",text)
        if len(capabilities)!=1:raise ValueError('Expected one isolated PCM capability')
        expected={'formats':"'"+sample_format+"'",'rate_min':'48000','rate_max':'48000','channels_min':'2','channels_max':'2'}
        fields=dict(re.findall(r'^\s*(formats|rate_min|rate_max|channels_min|channels_max)\s+([^\n]+)',capabilities[0],re.M))
        if {k:v.strip() for k,v in fields.items()}!=expected:raise ValueError('Decoded PCM capabilities differ from phone contract')
        if any(x in text for x in forbidden+['TDM','S32_LE']):raise ValueError('Decoded graph contains another backend')
        if binary.read_bytes()[:4]!=b'CoSA':raise ValueError('Invalid ALSA topology magic')
        results.append({'name':name,'endpoint':required,'format':sample_format,'binary_sha256':sha(binary),
                        'source_sha256':sha(HERE/(name+'.m4')),'compile_and_decode_pass':True})
    # This alternative leaves all three isolated graph outputs unchanged.
    name='speaker-microphone-probe'
    config=out/(name+'.conf');binary=out/(name+'.tplg');decoded=out/(name+'.decoded.conf')
    config.write_text(run([a.m4,'-I',macros,HERE/(name+'.m4')]))
    run([a.alsatplg,'-c',config,'-o',binary]);run([a.alsatplg,'-d',binary,'-o',decoded])
    text=decoded.read_text();verify_i2s(text)
    cap=re.search(r'^SectionPCMCapabilities\s*\{(.*?)^\}',text,re.M|re.S)
    if not cap:raise ValueError('Missing combined PCM capabilities')
    caps=re.findall(r"'([^']+)'\s*\{([^}]+)\}",cap[1])
    if {n for n,v in caps}!={'MultiMedia1 Playback','MultiMedia3 Capture'} or len(caps)!=2:
        raise ValueError('Wrong combined frontend capabilities')
    for pcm,body in caps:
        fields=dict(re.findall(r'^\s*(formats|rate_min|rate_max|channels_min|channels_max)\s+([^\n]+)',body,re.M))
        expected={'formats':"'S16_LE'" if 'Playback' in pcm else "'S24_LE'",
                  'rate_min':'48000','rate_max':'48000','channels_min':'2','channels_max':'2'}
        if {k:v.strip() for k,v in fields.items()}!=expected:raise ValueError('Combined format/rate/channel changed')
    matches=re.findall(r"'device120\.codec_dma_tx1:tuple0'\s*\{(.*?)\n\t\}",text,re.S)
    if len(matches)!=1:raise ValueError('Missing unique TX3 DMA tuple')
    values=dict(re.findall(r'^\s*token(\d+)\s+(\d+)\s*$',matches[0],re.M))
    if any(values.get(k)!=v for k,v in {'250':'4','251':'1','253':'1'}.items()):
        raise ValueError('Wrong phone TX3 DMA selectors')
    if any(x in text for x in ['TDM','S32_LE','DISPLAY_PORT','WSA_CODEC']):raise ValueError('Unexpected combined endpoint')
    if binary.read_bytes()[:4]!=b'CoSA':raise ValueError('Invalid combined topology magic')
    results.append({'name':name,'binary_sha256':sha(binary),'source_sha256':sha(HERE/(name+'.m4')),
                    'compile_and_decode_pass':True,'separate_manual_boot_only':True})
    result={'scope':'OFFLINE_PHONE_AUDIO_GRAPH_DRAFTS_ONLY','phone_operations':0,
       'original_macro_source_sha256':sha(archive),'graphs':results,
       'rate_hz':48000,'channels':2,'pad_tdm_or_wsa_route_included':False,
       'board_mixer_clock_or_protection_qualified':False,'service_or_runtime_install_created':False,
       'hardware_stream_eligible':False}
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
