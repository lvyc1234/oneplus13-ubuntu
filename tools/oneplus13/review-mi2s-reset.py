#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Review saved phone MI2S references and amplifier reset semantics, host only."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from stock import Fdt,cells,strings

spec=importlib.util.spec_from_file_location('audio_review',Path(__file__).with_name('review-audio-topology.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

def function_body(text,name):
    matches=list(re.finditer(r'(?m)^(?:static\s+)?(?:int|void)\s+'+re.escape(name)+r'\s*\([^;{}]*\)\s*\{',text))
    if len(matches)!=1:raise ValueError('one actual source function definition required')
    start=matches[0].end()-1;level=1;pos=start+1
    while level and pos<len(text):
        if text[pos]=='{':level+=1
        elif text[pos]=='}':level-=1
        pos+=1
    if level:raise ValueError('unclosed source function')
    return text[start:pos]

def review(blob,vendor,driver):
    tree=Fdt(blob)
    cards=[p for p,v in tree.nodes.items() if 'qcom,sec-mi2s-gpios' in v and a.enabled(tree.nodes,p)]
    if len(cards)!=1:raise ValueError('one enabled saved secondary-MI2S sound card required')
    card=tree.nodes[cards[0]];reference=cells(card['qcom,sec-mi2s-gpios'])
    if len(reference)!=1 or reference[0] not in tree.phandles:raise ValueError('one resolved secondary pinctrl reference required')
    controller=tree.nodes[tree.phandles[reference[0]]]
    names=strings(controller['pinctrl-names'])
    if names.count('aud_active')!=1:raise ValueError('unique active pinctrl state required')
    refs=cells(controller['pinctrl-'+str(names.index('aud_active'))]);pins=[]
    for phandle in refs:
        root=tree.phandles[phandle]
        if not a.enabled(tree.nodes,root):raise ValueError('referenced pin group has a disabled ancestor')
        for path,props in tree.nodes.items():
            if (path==root or path.startswith(root+'/')) and 'pins' in props and 'function' in props:
                pins.append({'pins':strings(props['pins']),'function':strings(props['function'])})
    if len(pins)!=4:raise ValueError('four named active MI2S groups expected')
    amps=[]
    for path,props in tree.nodes.items():
        if 'compatible' not in props or 'awinic,aw882xx_smartpa' not in strings(props['compatible']):continue
        if not a.enabled(tree.nodes,path):continue
        reset=cells(props['reset-gpio'])
        if len(reset)!=3 or reset[0] not in tree.phandles:raise ValueError('resolved three-cell amplifier reset required')
        amps.append({'i2c_address':cells(props['reg'])[0],'channel':cells(props['sound-channel'])[0],
                     'downstream_reset_pin':reset[1],'downstream_gpio_flags':reset[2]})
    if len(amps)!=2 or {x['channel'] for x in amps}!={0,1}:raise ValueError('two independently enabled amplifier channels expected')
    body=function_body(vendor,'aw882xx_hw_reset')
    levels=re.findall(r'gpio_set_value_cansleep\(aw882xx->reset_gpio,\s*([01])\)',body)
    if levels!=['0','1']:raise ValueError('vendor physical reset sequence changed')
    if 'gpiod_set_value_cansleep(amp->reset, 1)' not in function_body(driver,'aw88461_reset'):
        raise ValueError('draft logical reset semantics changed')
    if not re.search(r'gpiod_set_value_cansleep\(amp->reset,\s*0\)',function_body(driver,'aw88461_probe')):
        raise ValueError('draft logical release semantics changed')
    return {'scope':'HOST_SAVED_FDT_AND_PINNED_DRIVER_REVIEW','phone_operations':0,
            'stock_sha256':hashlib.sha256(blob).hexdigest(),'secondary_mi2s_pinctrl_reference_resolved':True,
            'active_pin_functions':pins,'enabled_aw_channels':sorted(amps,key=lambda x:x['channel']),
            'vendor_reset_physical_assert':0,'vendor_reset_physical_release':1,
            'draft_reset_logical_assert':1,'draft_reset_logical_release':0,
            'active_low_descriptor_required_to_preserve_vendor_levels':True,
            'copying_downstream_flags_without_translation_is_invalid':True,
            'chip_id_proven':False,'phone_dt_generated':False,'stream_eligible':False,
            'remaining_gates':['actual amplifier chip identity and owner-matched protection/tuning',
                               'verified backend port/clock/data-line/topology and machine-driver setup',
                               'explicit reset polarity translation and independent recovery review']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--stock',type=Path,required=True)
    p.add_argument('--vendor-driver',type=Path,required=True);p.add_argument('--draft-driver',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    r=review(args.stock.read_bytes(),args.vendor_driver.read_text(),args.draft_driver.read_text())
    r['vendor_driver_sha256']=hashlib.sha256(args.vendor_driver.read_bytes()).hexdigest()
    r['draft_driver_sha256']=hashlib.sha256(args.draft_driver.read_bytes()).hexdigest()
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r,indent=2))

if __name__=='__main__':main()
