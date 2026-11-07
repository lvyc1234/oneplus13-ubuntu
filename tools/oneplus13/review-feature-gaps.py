#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Aggregate saved FDT software nodes; never infer physical hardware support."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from stock import Fdt,strings

spec=importlib.util.spec_from_file_location('audio_review',Path(__file__).with_name('review-audio-topology.py'))
audio=importlib.util.module_from_spec(spec);spec.loader.exec_module(audio)

PATTERNS={
    'wsa_amplifier_templates':r'^(qcom,wsa88|sdw20217020400)',
    'aw_amplifier_nodes':r'^awinic,aw',
    'modem_remoteproc':r'^qcom,.*(?:mpss|modem).*pas$',
    'camera_pipeline_templates':r'^qcom,(?:cam-|cci$|.*camss)',
    'camera_sensor_templates':r'^camera_',
    'nfc_software_nodes':r'^(oplus-nfc|nxp,.*(?:nfc|nci)|st,st21)',
    'fingerprint_software_nodes':r'^(oplus,fp|goodix,.*finger|fpc,)',
    'adsp_remoteproc':r'^qcom,.*adsp.*pas$',
    'fastrpc_controllers':r'^qcom,fastrpc$',
    'thermal_sensor_controllers':r'^qcom,.*tsens',
    'pmic_glink_controllers':r'^qcom,(?:qti-)?pmic-glink$',
    'rtc_nodes':r'^qcom,.*rtc$',
    'power_key_nodes':r'^qcom,.*pwrkey$',
    'gpio_key_nodes':r'^gpio-keys$',
    'usb_controllers':r'^(snps,dwc3|qcom,.*dwc3)$',
    'ufs_controllers':r'^(jedec,ufs|qcom,.*ufshc)',
}

def inventory(tree):
    result={}
    for feature,pattern in PATTERNS.items():
        nodes=[p for p,v in tree.nodes.items() if 'compatible' in v
               and any(re.search(pattern,c) for c in strings(v['compatible']))]
        result[feature]={'present':len(nodes),'effectively_enabled':sum(audio.enabled(tree.nodes,p) for p in nodes)}
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--stock',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();stock=a.stock.read_bytes();candidate=a.candidate.read_bytes()
    report={'scope':'SAVED_FDT_SOFTWARE_GAP_REVIEW_ONLY','phone_operations':0,
            'hardware_support_inferred':False,'physical_chip_identity_inferred':False,
            'stock_sha256':hashlib.sha256(stock).hexdigest(),'candidate_sha256':hashlib.sha256(candidate).hexdigest(),
            'stock':inventory(Fdt(stock)),'candidate':inventory(Fdt(candidate)),
            'limitations':['No match does not establish hardware absence, especially DSP/modem peripherals.',
                          'Enabled templates do not identify populated camera/amplifier chips.',
                          'Counts do not establish driver binding, firmware compatibility, safety or functionality.']}
    a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
