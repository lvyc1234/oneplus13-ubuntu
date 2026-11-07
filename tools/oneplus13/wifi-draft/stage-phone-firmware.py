#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare inert, exact-phone WLAN files on the host; never activate a radio."""
import argparse,hashlib,importlib.util,json,shutil
from pathlib import Path
HERE=Path(__file__).resolve().parent
MAPPING={'wlan/modem/amss20.bin':'amss.bin',
         'wlan/modem/phy_ucode20.elf':'m3.bin',
         'wlan/modem/aux_ucode20.elf':'aux_ucode.bin',
         'wlan/odm/bdwlan.elf':'board.bin',
         'wlan/odm/regdb.bin':'regdb.bin'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stock',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    stock=a.stock.resolve();out=a.output.resolve()
    if out.exists():raise ValueError('Preserve previous/private staging evidence')
    spec=importlib.util.spec_from_file_location('phone_firmware',HERE.parent/'firmware.py');module=importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0,str(HERE.parent));spec.loader.exec_module(module)
    pin=module.verify(stock);entries={i['output']:i for i in pin['files']}
    if not all(n in entries for n in MAPPING):raise ValueError('Missing exact phone files')
    out.mkdir(parents=True);files={}
    for source,name in MAPPING.items():
        data=(stock/source).read_bytes()
        if name in ['amss.bin','m3.bin','aux_ucode.bin','board.bin'] and data[:4]!=b'\x7fELF':raise ValueError('Expected unmodified ELF input')
        if name=='board.bin' and len(data)>256*1024:raise ValueError('Board exceeds pinned WCN7860 capacity')
        target=out/'inactive/ath12k/WCN7860/hw2.0'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(stock/source,target)
        if target.read_bytes()!=data:raise ValueError('Staging mutation')
        files[name]={'source':source,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    proof={'scope':'HOST_ONLY_INACTIVE_EXACT_PHONE_WLAN_STAGING','phone_operations':0,
           'source_manifest_sha256':hashlib.sha256((stock/'manifest.json').read_bytes()).hexdigest(),
           'unmodified_files':files,'reference_radio_inputs_included':False,
           'board_api':1,'board2_or_firmware2_override_included':False,
           'separate_calibration_included':False,'qdss_debug_configuration_included':False,
           'candidate_or_active_firmware_path_modified':False,'wlan_policy_or_rf_qualified':False}
    (out/'verification.json').write_text(json.dumps(proof,indent=2)+'\n',newline='\n')
    print(json.dumps(proof,indent=2))

if __name__=='__main__':main()
