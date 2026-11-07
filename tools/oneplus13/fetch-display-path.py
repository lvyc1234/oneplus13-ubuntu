#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Fetch immutable display/CCF references; no device access."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',required=True,type=Path)
OUT=parser.parse_args().output
OUT.mkdir(parents=True,exist_ok=True)
groups=[('OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750',
         'd50b305f7da9e14715a25120a4ac7b1a4b8b97c3','vendor',
         ['vendor/qcom/opensource/display-drivers/msm/dsi/'+x for x in
          ['dsi_phy.c','dsi_phy.h','dsi_phy_hw.h','dsi_phy_hw_v7_2.c','dsi_phy_hw_v4_0.c']]),
        ('yzddmr6/linux-sm8750-erhai','7a81d184e3098624d4342d8f4eff5e23674bcc9f','platform',
         ['drivers/clk/qcom/dispcc-sm8750.c','drivers/clk/qcom/clk-rcg2.c',
          'drivers/clk/clk-divider.c','drivers/clk/clk-fixed-factor.c',
          'drivers/gpu/drm/msm/disp/dpu1/dpu_encoder_phys_cmd.c',
          'drivers/gpu/drm/msm/dsi/phy/dsi_phy_7nm.c'])]
def fetch(item):
    repository,pin,prefix,path=item
    p=OUT/prefix/Path(path).name;p.parent.mkdir(exist_ok=True)
    req=urllib.request.Request('https://api.github.com/repos/'+repository+'/contents/'+path+'?ref='+pin,
                              headers={'User-Agent':'Codex-source-review'})
    with urllib.request.urlopen(req,timeout=45) as response:r=json.load(response)
    b=base64.b64decode(r['content'])
    assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==r['sha']
    p.write_bytes(b)
    return {'repository':repository,'commit':pin,'path':path,'local':str(p.relative_to(OUT)),
            'git_blob':r['sha'],'sha256':hashlib.sha256(b).hexdigest()}
with ThreadPoolExecutor(max_workers=4) as pool:
    rows=list(pool.map(fetch,[(repo,pin,prefix,path) for repo,pin,prefix,paths in groups for path in paths]))
(OUT/'manifest.json').write_text(json.dumps(rows,indent=2)+'\n')
print('Fetched and Git-blob verified '+str(len(rows))+' public display path sources')
