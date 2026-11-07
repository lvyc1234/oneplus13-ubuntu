#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bind an offline ACF to a saved owner hash; never infer amplifier identity."""
import argparse
import hashlib
import json
from pathlib import Path
import re

def review(acf,metadata):
    rows=re.findall(r'(?m)^FILE (\d+) /odm/firmware/aw882xx_acf\.bin\r?\n([0-9a-f]{64})  -\r?$',metadata)
    if len(rows)!=1:raise ValueError('Exactly one saved owner ACF measurement required')
    size,digest=rows[0]
    if len(acf)!=int(size) or hashlib.sha256(acf).hexdigest()!=digest:
        raise ValueError('ACF differs from saved owner measurement')
    return {'scope':'SAVED_OWNER_ACF_HASH_BINDING_ONLY','phone_operations':0,
            'saved_owner_acf_content_matched':True,'acf_sha256':digest,'acf_bytes':len(acf),
            'physical_chip_id_verified':False,'factory_calibration_verified':False,
            'dynamic_speaker_protection_verified':False,'stream_eligible':False,
            'contains_register_payload':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--acf',type=Path,required=True)
    p.add_argument('--owner-metadata',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();result=review(a.acf.read_bytes(),a.owner_metadata.read_text(encoding='utf-8'))
    a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
