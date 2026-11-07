#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bind the filter to the verified native sink, entirely in its user session."""
from pathlib import Path
import json,os,subprocess,sys,tempfile

TEMPLATE=Path('/usr/share/oneplus13/stereo-filter.conf')

def nodes():
    p=subprocess.run(['pw-dump'],check=True,capture_output=True,text=True,timeout=4)
    if len(p.stdout)>4*1024*1024:raise ValueError('Oversized PipeWire state')
    return [o for o in json.loads(p.stdout) if o.get('type')=='PipeWire:Interface:Node']

def identity(node):
    props=node.get('info',{}).get('props',{})
    if props.get('media.class')!='Audio/Sink' or props.get('object.serial') is None:
        raise ValueError('Native sink identity unavailable')
    name=props.get('node.name')
    if not isinstance(name,str) or not name or name=='oneplus13_stereo':
        raise ValueError('Invalid native sink name')
    return dict(id=node['id'],name=name,serial=props['object.serial'])

def select_native(objects,node_id,name):
    matches=[o for o in objects if o.get('id')==node_id and
             o.get('info',{}).get('props',{}).get('node.name')==name]
    if len(matches)!=1:raise ValueError('Verified native sink disappeared or changed')
    return identity(matches[0])

def render_config(template,name):
    marker='"target.object": "oneplus13_speakers"'
    if template.count(marker)!=1:raise ValueError('Unexpected phone filter template')
    return template.replace(marker,'"target.object": '+json.dumps(name))

def atomic_write(path,text):
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:stream.write(text)
        os.replace(tmp,path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def main():
    if os.geteuid()<1000 or not Path('/run/oneplus13/ram-session').is_file():
        raise ValueError('Ordinary user RAM session required')
    runtime=Path(os.environ['XDG_RUNTIME_DIR'])
    if not runtime.is_absolute() or runtime.stat().st_uid!=os.geteuid():
        raise ValueError('Own user runtime required')
    state=runtime/'oneplus13-stereo-target.json'
    if len(sys.argv)==4 and sys.argv[1]=='prepare':
        target=select_native(nodes(),int(sys.argv[2]),sys.argv[3])
        atomic_write(runtime/'oneplus13-stereo-filter.conf',render_config(TEMPLATE.read_text(),target['name']))
        atomic_write(state,json.dumps(target)+'\n')
    elif sys.argv[1:]==['cap']:
        if not state.is_file():return
        target=json.loads(state.read_text())
        # Numeric IDs may be reused; only cap the same name AND object serial.
        matches=[o for o in nodes() if o.get('id')==target['id']]
        if len(matches)==1 and identity(matches[0])==target:
            subprocess.run(['wpctl','set-volume','--limit','1',str(target['id']),'0.5'],
                           check=True,timeout=4)
    else:raise ValueError('Expected prepare ID NAME or cap')

if __name__=='__main__':main()
