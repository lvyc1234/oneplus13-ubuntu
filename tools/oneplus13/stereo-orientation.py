#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
"""Remap only the phone speaker filter, from Mutter's actual internal output.

The matrix is a pure permutation (0/1), never gain/amp/firmware control. No
SensorProxy claim or ScreenSaver/authentication change is made here.
"""
from pathlib import Path
import json,os,subprocess,time

def matrix(transform):
    if type(transform) is not int or transform not in range(8):
        raise ValueError('Invalid Mutter transform')
    # On the phone, transform 3 retains the native speaker order. Transform 1
    # swaps it. The portrait and mirror policy is otherwise unchanged.
    swap=(transform & 3)==1
    return {'params':['outL:Gain 1',float(not swap),'outL:Gain 2',float(swap),
                      'outR:Gain 1',float(swap),'outR:Gain 2',float(not swap)]}

def phone_transform(logical_monitors):
    values=[]
    for row in logical_monitors:
        if len(row)!=7:raise ValueError('Unexpected Mutter monitor tuple')
        if any(spec[0].startswith('DSI-') for spec in row[5]):values.append(row[3])
    if len(values)!=1:raise ValueError('Expected exactly one internal logical monitor')
    matrix(values[0])
    return values[0]

def call(argv):
    p=subprocess.run(argv,capture_output=True,text=True,timeout=4)
    if p.returncode:raise RuntimeError('Speaker matrix command failed')
    if len(p.stdout)>4*1024*1024:raise RuntimeError('Oversized PipeWire state')
    return p.stdout

def main():
    from gi.repository import Gio
    if os.geteuid()<1000 or not Path('/run/oneplus13/ram-session').is_file():
        raise RuntimeError('Ordinary user RAM session required')
    bus=Gio.bus_get_sync(Gio.BusType.SESSION,None);last=None
    state=Path(os.environ['XDG_RUNTIME_DIR'])/'oneplus13-stereo.json'
    while True:
        try:
            reply=bus.call_sync('org.gnome.Mutter.DisplayConfig','/org/gnome/Mutter/DisplayConfig',
                'org.gnome.Mutter.DisplayConfig','GetCurrentState',None,None,
                Gio.DBusCallFlags.NONE,3000,None).unpack()
            transform=phone_transform(reply[2])
            nodes=[x for x in json.loads(call(['pw-dump'])) if x.get('type')=='PipeWire:Interface:Node'
                and x.get('info',{}).get('props',{}).get('node.name')=='oneplus13_stereo']
            if len(nodes)!=1:raise RuntimeError('Unique phone stereo filter unavailable')
            node=nodes[0];identity=(node['id'],node['info']['props'].get('object.serial'),transform)
            if identity!=last:
                call(['pw-cli','set-param',str(node['id']),'Props',json.dumps(matrix(transform))])
                temp=state.with_suffix('.tmp');temp.write_text(json.dumps(dict(transform=transform,
                    swap=(transform & 3)==1,filter_id=node['id'],unity_permutation=True)))
                temp.replace(state);last=identity
        except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired) as error:
            # Keep the last valid matrix on transient session/monitor loss;
            # do not guess from a different display or destroy the real sink.
            last=None
            print(type(error).__name__+': speaker orientation unavailable',flush=True)
        except Exception as error:
            # D-Bus NameOwner/closed errors are retried with a fresh connection.
            bus=Gio.bus_get_sync(Gio.BusType.SESSION,None);last=None
            print(type(error).__name__+': Mutter state unavailable',flush=True)
        time.sleep(1)

if __name__=='__main__':main()
