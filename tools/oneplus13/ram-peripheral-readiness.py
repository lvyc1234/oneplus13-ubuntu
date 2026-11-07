#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded read-only power/USB/video/modem readiness; never infer support."""
import json,re
from pathlib import Path


def read(path,limit=256):
    try:
        with path.open() as stream:value=stream.read(limit+1)
        return value.strip() if len(value)<=limit else 'OVERSIZED'
    except (OSError,UnicodeError):return None


def collect(root=Path('/')):
    def inventory(relative,pattern,fields,maximum=16):
        paths=sorted((root/relative).glob(pattern))
        if len(paths)>maximum:return {'count':len(paths),'inventory_bound_exceeded':True}
        return {'count':len(paths),'entries':[{field:read(path/field) for field in fields} for path in paths]}
    result={'scope':'READ_ONLY_PERIPHERAL_SOFTWARE_READINESS',
            'hardware_support_inferred':False,'peripheral_activated':False,
            'watchdog_device_opened':False,'rtc_alarm_changed':False,'sysfs_writes':0,
            'power_states':read(root/'sys/power/state'),
            'memory_sleep_modes':read(root/'sys/power/mem_sleep'),
            'rtc':inventory('sys/class/rtc','rtc[0-9]*',
                            ('name','hctosys','wakealarm','device/power/wakeup'),4),
            'watchdog':inventory('sys/class/watchdog','watchdog[0-9]*',
                ('identity','state','status','timeout','timeleft','nowayout','bootstatus'),4),
            'usb_roles':inventory('sys/class/usb_role','*',('role',),8),
            'typec':inventory('sys/class/typec','port[0-9]',
                ('data_role','power_role','power_operation_mode','preferred_role'),8),
            'video_nodes':inventory('sys/class/video4linux','video[0-9]*',('name',),32),
            'remote_processors':inventory('sys/class/remoteproc','remoteproc[0-9]*',
                                          ('name','state','firmware'),8),
            'power_supplies':inventory('sys/class/power_supply','*',
                ('type','status','capacity','temp','current_now','voltage_now'),12),
            'device_interface_counts':{kind:len(list((root/'dev').glob(pattern)))
                for kind,pattern in [('gnss','gnss[0-9]*'),('nfc','nfc*'),('infrared','lirc[0-9]*')]}}
    thermal=[]
    paths=sorted((root/'sys/class/thermal').glob('thermal_zone[0-9]*'))
    if len(paths)<=128:
        for path in paths:
            name=read(path/'type');value=read(path/'temp')
            if value and re.fullmatch(r'-?[0-9]{1,7}',value):thermal.append({'type':name,'temperature_raw':int(value)})
    result['thermal']=thermal
    return result


if __name__=='__main__':
    if not Path('/run/oneplus13/ram-session').is_file():raise SystemExit('Protected phone RAM session required')
    print('PERIPHERAL_READINESS_RESULT '+json.dumps(collect(),sort_keys=True),flush=True)
