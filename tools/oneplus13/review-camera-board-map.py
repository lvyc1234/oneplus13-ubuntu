#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Validate the phone camera board graph from a saved stock FDT."""
import argparse, hashlib, importlib.util, json
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('oneplus13_stock',HERE/'stock.py')
stock=importlib.util.module_from_spec(spec);spec.loader.exec_module(stock)
EXPECTED={0:{'phy':5,'bus':'cci0'},1:{'phy':1,'bus':'cci0'},2:{'phy':3,'bus':'cci1'},3:{'phy':4,'bus':'i2c'}}
REQUIRED={'cell-index','csiphy-sd-index','clocks','gpios','pinctrl-0','pinctrl-1','cam_clk-supply','cam_vana-supply','cam_vdig-supply','cam_vio-supply','eeprom-src','sensor-position-yaw','sensor-position-pitch','sensor-position-roll','status'}

def ultrawide_selection(probe_intent):
    """Apply the public vendor's OR operation to reviewed C.93 template IDs.

    PHY fields are inferred from the retained module schema; they are not
    capture acceptance, and L/S supplier selection remains unresolved.
    """
    if type(probe_intent) is not int or probe_intent!=0x30e0:
        raise ValueError('Ultrawide probe_intent differs from saved phone graph')
    templates=[{'template':name,'module_id':module_id,
                'effective_id':module_id|probe_intent,'phy_mode_inferred':mode,
                'lanes_or_trios':lanes,'matches_observed_chip_id':(module_id|probe_intent)==0x38e5}
               for name,module_id,mode,lanes in
               [('D',0x30e0,'DPHY',4),('L',0x805,'CPHY',3),('S',0x805,'CPHY',3)]]
    return {'probe_intent':probe_intent,'physical_chip_id':0x38e5,
            'match_operation':'module_id OR probe_intent','templates':templates,
            'matching_templates':['L','S'],'supplier_selected':False,
            'mode_schema_authoritative':False,'camera_capture_qualified':False}

def one_cell(value,name):
    cells=stock.cells(value)
    if len(cells)!=1:raise ValueError(f'{name} is not one cell')
    return cells[0]

def validate_records(records):
    if len(records)!=4:raise ValueError('Expected exactly four populated camera graph records')
    by_index={}
    for row in records:
        idx=row['cell_index']
        if idx in by_index or idx not in EXPECTED:raise ValueError('Camera cell-index is duplicate or outside 0..3')
        if row['status']!='ok':raise ValueError('Camera sensor is not enabled in stock graph')
        if row['phy']!=EXPECTED[idx]['phy'] or row['bus']!=EXPECTED[idx]['bus']:raise ValueError('Camera PHY/CCI mapping differs from saved phone graph')
        missing=REQUIRED-set(row['properties'])
        if missing:raise ValueError('Camera graph misses required power/clock property')
        if not row['eeprom_known']:raise ValueError('Camera EEPROM phandle is unresolved')
        if not row['position_complete']:raise ValueError('Camera orientation tuple is incomplete')
        if idx==3 and not row['front_reg_present']:raise ValueError('Front camera I2C address is absent')
        if idx!=3 and row['front_reg_present']:raise ValueError('CCI camera unexpectedly uses front I2C address form')
        by_index[idx]=row
    if set(by_index)!=set(EXPECTED):raise ValueError('Camera indices are incomplete')
    selection=ultrawide_selection(by_index[1].get('probe_intent'))
    return {'populated_sensors':4,'cell_indices':[0,1,2,3],'phy_indices':[by_index[i]['phy'] for i in range(4)],'cci_buses':['cci0','cci0','cci1','i2c'],'power_clock_gpio_graph_complete':True,'eeprom_links_resolved':True,'orientation_tuples_present':True,'sensor_serials_published':False,'ultrawide_selection':selection,'custom_ultrawide_phy_still_required':True,'camera_capture_qualified':False}

def extract(tree):
    records=[]
    for path,props in tree.nodes.items():
        if 'compatible' not in props:continue
        compatible=tree.text(path,'compatible')
        if compatible not in (['qcom,cam-sensor'],['qcom,cam-i2c-sensor']):continue
        idx=one_cell(props['cell-index'],'cell-index')
        if '/qcom,cci0@' in path:bus='cci0'
        elif '/qcom,cci1@' in path:bus='cci1'
        elif '/i2c@' in path:bus='i2c'
        else:raise ValueError('Camera sensor parent bus is unknown')
        position_complete=all(k in props and len(stock.cells(props[k]))==1 for k in ('sensor-position-yaw','sensor-position-pitch','sensor-position-roll'))
        records.append({'cell_index':idx,'phy':one_cell(props['csiphy-sd-index'],'csiphy-sd-index'),'bus':bus,'status':tree.text(path,'status')[0],'properties':set(props),'eeprom_known':one_cell(props['eeprom-src'],'eeprom-src') in tree.phandles,'position_complete':position_complete,'front_reg_present':'reg' in props})
        if idx==1 and 'probe_intent' in props:
            records[-1]['probe_intent']=one_cell(props['probe_intent'],'probe_intent')
    return records

def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();blob=a.runtime.read_bytes();summary=validate_records(extract(stock.Fdt(blob)))
    result={'scope':'PRIVATE_STOCK_FDT_CAMERA_BOARD_MAP_REVIEW','runtime_sha256':hashlib.sha256(blob).hexdigest(),'phone_operations':0,**summary}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='runtime_sha256'},sort_keys=True))

if __name__=='__main__':main()
