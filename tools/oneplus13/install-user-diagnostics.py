#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install the phone-only offline diagnostic candidate into a fresh RAM root."""
from pathlib import Path
import hashlib,json,subprocess,sys
REPO=Path(__file__).resolve().parents[2]

def filter_module():
    graph={'nodes':[{'type':'builtin','name':'left','label':'copy'},
                    {'type':'builtin','name':'right','label':'copy'},
                    {'type':'builtin','name':'outL','label':'mixer','control':{'Gain 1':1.0,'Gain 2':0.0}},
                    {'type':'builtin','name':'outR','label':'mixer','control':{'Gain 1':0.0,'Gain 2':1.0}}],
        'links':[{'output':c+':Out','input':o+':In '+str(i)} for o in ['outL','outR'] for i,c in [(1,'left'),(2,'right')]],
        'inputs':['left:In','right:In'],'outputs':['outL:Out','outR:Out']}
    return {'name':'libpipewire-module-filter-chain','args':{'node.description':'OnePlus 13 speakers',
        'audio.rate':48000,'filter.graph':graph,'capture.props':{'node.name':'oneplus13_stereo',
        'media.class':'Audio/Sink','audio.channels':2,'audio.position':['FL','FR'],'priority.session':-10,
        'node.autoconnect':False},
        'playback.props':{'node.name':'oneplus13_stereo_out','audio.channels':2,
        'audio.position':['FL','FR'],'target.object':'oneplus13_speakers','node.autoconnect':True,
        'node.dont-fallback':True,'node.passive':True}}}

def install(root):
    root=root.resolve()
    if not root.is_relative_to((REPO/'out').resolve()) or not (root/'etc/os-release').is_file():
        raise ValueError('Fresh phone staging root required')
    files={}
    def write(name,data,mode=0o644):
        p=root/name
        if not p.resolve().is_relative_to(root):raise ValueError('Path escapes root')
        p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(mode)
        files[name]=hashlib.sha256(data).hexdigest()
    for source,name in [('devices/oneplus13/display-power/display-power.py','usr/libexec/oneplus13-display-power.py'),
        ('devices/oneplus13/display-power/power-key.py','usr/libexec/oneplus13-power-key.py'),
        ('tools/oneplus13/stereo-orientation.py','usr/libexec/oneplus13-stereo-orientation.py'),
        ('tools/oneplus13/stereo-session.py','usr/libexec/oneplus13-stereo-session.py')]:
        write(name,(REPO/source).read_bytes(),0o755)
    for name in ['oneplus13-display-power.service','oneplus13-display-power.socket','oneplus13-power-key@.service']:
        write('usr/lib/systemd/system/'+name,(REPO/'devices/oneplus13/display-power'/name).read_bytes())
    write('usr/lib/udev/rules.d/82-oneplus13-power-key.rules',(REPO/'devices/oneplus13/display-power/82-oneplus13-power-key.rules').read_bytes())
    write('usr/lib/systemd/user/org.gnome.SettingsDaemon.Power.service.d/80-oneplus13-backlight.conf',
        (REPO/'devices/oneplus13/display-power/80-oneplus13-backlight.conf').read_bytes())
    write('etc/systemd/logind.conf.d/80-oneplus13-power-key.conf',b'[Login]\nHandlePowerKey=ignore\n')
    lib=root/'usr/lib/aarch64-linux-gnu/oneplus13/gsd-backlight-compat.so';lib.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(['gcc','-shared','-fPIC','-O2','-Wall','-Wextra','-Werror',
        str(REPO/'devices/oneplus13/display-power/gsd-backlight-compat.c'),'-o',str(lib)],check=True)
    files[str(lib.relative_to(root))]=hashlib.sha256(lib.read_bytes()).hexdigest()
    template=(root/'usr/share/pipewire/filter-chain.conf').read_text();end=template.rfind(']')
    if end<0:raise ValueError('PipeWire filter template missing module list')
    write('usr/share/oneplus13/stereo-filter.conf',(template[:end]+json.dumps(filter_module())+'\n]\n').encode())
    links={'etc/systemd/system/sockets.target.wants/oneplus13-display-power.socket':'/usr/lib/systemd/system/oneplus13-display-power.socket'}
    for unit in ['oneplus13-stereo-filter.service','oneplus13-stereo-orientation.service']:
        write('usr/lib/systemd/user/'+unit,(REPO/'devices/oneplus13/audio'/unit).read_bytes())
        # Root first-user audio starts these only after the native target has
        # been verified; never autostart a filter aimed at an absent PCM.
    for name,target in links.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists() or p.is_symlink():raise ValueError('Existing diagnostic unit link')
        p.symlink_to(target)
    write('usr/lib/oneplus13-desktop/diagnostic-runtime.json',json.dumps(dict(files=files,
        hardware_tested=False,native_dpms_qualified=False,system_suspend_enabled=False,
        display_backend='AA569 binary backlight compatibility blank',stereo_matrix='unity permutation from internal Mutter transform'),indent=2).encode())
    return files

if __name__=='__main__':install(Path(sys.argv[1]))
