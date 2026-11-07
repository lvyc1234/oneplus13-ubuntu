#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded read-only platform inventory. No controls, streams or suspend."""
import argparse
import json
from pathlib import Path
import re

def text(path):
    try:
        with path.open('r') as stream:return stream.read(256).strip()
    except OSError:return None

def number(path):
    value=text(path)
    try:return int(value) if value is not None else None
    except ValueError:return None

def children(path,pattern='*',limit=64):
    try:return sorted(path.glob(pattern))[:limit]
    except OSError:return []

def bindings(root,bus,pattern):
    """Read existing driver links only; never bind, probe or request firmware."""
    result=[]
    base=root/'sys/bus'/bus/'devices'
    try: selected=sorted(p for p in base.iterdir() if re.search(pattern,p.name))[:64]
    except OSError: return result
    for device in selected:
        try: driver=(device/'driver').readlink().name
        except OSError: driver=None
        result.append({'device':device.name,'driver':driver,
                       'modalias':text(device/'modalias')})
    return result

def collect(root):
    thermal=[]
    for zone in children(root/'sys/class/thermal','thermal_zone*'):
        value=number(zone/'temp')
        thermal.append({'type':text(zone/'type'),'millidegrees_c':value,
                        'plausible_range':value is not None and -50000<=value<=200000})
    cooling=[{'type':text(p/'type'),'current_state':number(p/'cur_state'),
              'maximum_state':number(p/'max_state')} for p in children(root/'sys/class/thermal','cooling_device*')]
    rtc=[{'name':text(p/'name'),'epoch_read_only':number(p/'since_epoch'),'hctosys':number(p/'hctosys')}
         for p in children(root/'sys/class/rtc','rtc*',4)]
    cpus=[{'governor':text(p/'scaling_governor'),'driver':text(p/'scaling_driver'),
           'minimum_khz':number(p/'scaling_min_freq'),'maximum_khz':number(p/'scaling_max_freq')}
          for p in children(root/'sys/devices/system/cpu/cpufreq','policy*',16)]
    keys=[]
    for p in children(root/'sys/class/input','event*'):
        mask=text(p/'device/capabilities/key')
        try:bits=int(''.join(word.zfill(16) for word in mask.split()),16) if mask else 0
        except ValueError:bits=0
        found={name:bool(bits&(1<<code)) for name,code in [('power',116),('volume_up',115),('volume_down',114)]}
        if any(found.values()):keys.append({'capabilities':found})
    supplies=[]
    for p in children(root/'sys/class/power_supply',limit=16):
        supplies.append({field:text(p/field) for field in ['type','status','online','usb_type','voltage_max','current_max',
            'capacity','voltage_now','current_now','temp']})
    remoteprocs=[{'name':text(p/'name'),'state':text(p/'state')}
        for p in children(root/'sys/class/remoteproc','remoteproc*',16)]
    roles=[text(p/'role') for p in children(root/'sys/class/usb_role',limit=8)]
    ports=[{field:text(p/field) for field in ['data_role','power_role','power_operation_mode']}
           for p in children(root/'sys/class/typec','port[0-9]',8)]
    block=[number(p/'ro') for p in children(root/'sys/class/block')
           if not p.name.startswith(('ram','zram','loop'))]
    return {'scope':'PASSIVE_SYSFS_INVENTORY_ONLY','no_control_writes':True,
            'hardware_functional_acceptance':False,'thermal_zones':thermal,'cooling_devices':cooling,
            'rtc':rtc,'cpu_frequency_policies':cpus,'advertised_key_devices':keys,
            'power_supplies':supplies,'remote_processors':remoteprocs,'usb_roles':roles,'typec_ports':ports,
            'hardware_block_devices_observed':len(block),'all_observed_block_devices_read_only':bool(block) and all(v==1 for v in block),
            'writable_block_devices_observed':sum(v==0 for v in block),
            'available_sleep_states':text(root/'sys/power/state'),
            'existing_driver_bindings':{
                'scmi':bindings(root,'scmi_protocol',r'.'),
                'platform':bindings(root,'platform',r'(?i)scmi|cpucp|16430000|17830000|pmic.glink|ucsi|usbss'),
                'auxiliary':bindings(root,'auxiliary',r'(?i)pmic|glink|ucsi'),
                'rpmsg':bindings(root,'rpmsg',r'(?i)pmic|glink')},
            'suspend_executed':False,'rtc_write_requested':False,'audio_or_sensor_stream_started':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/'));a=p.parse_args()
    print('RAM_PLATFORM_RESULT '+json.dumps(collect(a.root),sort_keys=True))

if __name__=='__main__':main()
