# SPDX-License-Identifier: GPL-2.0-only
"""Parse the manual watchdog receipt without depending on JSON key order."""
import json

def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate watchdog JSON key')
        result[key]=value
    return result

def parse(text):
    if len(text)>65536:raise ValueError('Oversized watchdog transcript')
    lines=[line.strip() for line in text.splitlines() if line.strip().startswith('{')]
    if len(lines)!=1:raise ValueError('Missing unique watchdog receipt')
    result=json.loads(lines[0],object_pairs_hook=unique_object)
    if not isinstance(result,dict):raise ValueError('Wrong watchdog receipt type')
    return result

def validate_armed(result,scope):
    if scope not in ('ONE_MANUAL_RAM_GUNYAH_WATCHDOG_RESET','ONE_MANUAL_RAM_GUNYAH_AWAKE_RESET',
                     'ONE_MANUAL_RAM_GUNYAH_AWAKE_VIDEO_GUARD'):
        raise ValueError('Unknown watchdog reset scope')
    if result.get('scope')!=scope:raise ValueError('Wrong watchdog reset scope')
    for name in ('armed','countdown_verified','keepalive_verified'):
        if result.get(name) is not True:raise ValueError('Unqualified watchdog arm gate: '+name)
    for name in ('suspend_requested','reset_observed'):
        if result.get(name) is not False:raise ValueError('Unexpected watchdog receipt flag: '+name)
    values={}
    for name in ('partition_writes','first_timeleft','second_timeleft','third_timeleft',
                 'set_timeout_readback','get_timeout_readback','timeout_seconds','support_options'):
        value=result.get(name)
        if type(value) is not int:raise ValueError('Invalid watchdog integer: '+name)
        values[name]=value
    if (values['partition_writes']!=0 or values['set_timeout_readback']!=12 or
        values['get_timeout_readback']!=12 or values['timeout_seconds']!=12 or
        values['support_options']&0x8180!=0x8180):
        raise ValueError('Watchdog timeout/capability mismatch')
    if not (0<values['second_timeleft']<values['first_timeleft']<=12 and
            values['second_timeleft']<values['third_timeleft']<=12):
        raise ValueError('Watchdog countdown/keepalive mismatch')
    if scope in ('ONE_MANUAL_RAM_GUNYAH_AWAKE_RESET','ONE_MANUAL_RAM_GUNYAH_AWAKE_VIDEO_GUARD'):
        if (result.get('active_wait_required') is not True or
            result.get('idle_or_suspend_recovery_qualified') is not False or
            type(result.get('awake_cpu')) is not int or not 0<=result['awake_cpu']<=255):
            raise ValueError('Missing awake-only qualification boundary')
    if scope=='ONE_MANUAL_RAM_GUNYAH_AWAKE_VIDEO_GUARD':
        if result.get('guard_running') is not True or result.get('guard_seconds')!=90:
            raise ValueError('Missing fixed video guard window')
    return result
