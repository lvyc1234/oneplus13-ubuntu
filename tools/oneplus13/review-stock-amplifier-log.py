#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Review saved stock PID-specific callbacks without accessing any device."""
import argparse,hashlib,json,re
from pathlib import Path

def body(text,name):
    match=re.search(r'(?m)^(?:static\s+)?(?:int|void|unsigned int)\s+'+re.escape(name)+r'\s*\([^;{}]*\)\s*\{',text)
    if not match:raise ValueError('missing actual source function')
    pos=match.end();level=1;start=pos-1
    while level and pos<len(text):
        if text[pos]=='{':level+=1
        elif text[pos]=='}':level-=1
        pos+=1
    if level:raise ValueError('unclosed source function')
    return text[start:pos]

def review(log,init,header):
    if len(log)>262144 or len(init)>1048576 or len(header)>1048576:
        raise ValueError('bounded saved inputs required')
    if not re.search(r'\bPID_2308_ID\s*=\s*0x2308\b',header):
        raise ValueError('vendor PID mapping differs')
    dispatch=body(init,'aw882xx_init')
    if not re.search(r'case PID_2308_ID:\s*return aw_pid_2308_dev_init\(aw882xx\);',dispatch):
        raise ValueError('PID-specific dispatch differs')
    setup=body(init,'aw_pid_2308_dev_init')
    if 'aw_pa->ops.aw_get_voltage_offset = aw_pid_2308_get_voltage_offset;' not in setup:
        raise ValueError('PID-specific callback assignment differs')
    body(init,'aw_pid_2308_get_voltage_offset')
    found=set(re.findall(r'\[Awinic\]\[(\d+)-00(34|35)\]aw_pid_2308_get_voltage_offset:\s*get vol offset val -?\d+\b',log))
    if len(found)!=2 or {x[1] for x in found}!={'34','35'} or len({x[0] for x in found})!=1:
        raise ValueError('both amplifiers on one saved stock I2C bus required')
    return {'scope':'SAVED_STOCK_PID_SPECIFIC_CALLBACK_EVIDENCE','phone_operations':0,
        'two_stock_amplifier_pid2308_callbacks_observed':True,'vendor_dispatch_checked':True,
        'pid_family':'0x2308','raw_chip_id_register_readback_present':False,
        'factory_calibration_verified':False,'dynamic_speaker_protection_verified':False,
        'driver_loading_or_stream_eligible':False,
        'limitation':'Indirect stock callback evidence; no new register read, control operation or stream.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--log',type=Path,required=True)
    p.add_argument('--vendor-init',type=Path,required=True);p.add_argument('--vendor-header',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    result=review(a.log.read_text(),a.vendor_init.read_text(),a.vendor_header.read_text())
    result['vendor_init_sha256']=hashlib.sha256(a.vendor_init.read_bytes()).hexdigest()
    result['vendor_header_sha256']=hashlib.sha256(a.vendor_header.read_bytes()).hexdigest()
    a.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()

