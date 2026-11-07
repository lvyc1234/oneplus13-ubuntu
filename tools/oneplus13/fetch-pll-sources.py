#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Fetch immutable public PLL/PHY sources, checking every Git blob identity."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',required=True,type=Path)
OUT=parser.parse_args().output
OUT.mkdir(parents=True,exist_ok=True)
PIN='7a81d184e3098624d4342d8f4eff5e23674bcc9f'
PATHS=['drivers/gpu/drm/msm/dsi/phy/dsi_phy_7nm.c',
       'drivers/gpu/drm/msm/dsi/phy/dsi_phy.c','drivers/gpu/drm/msm/dsi/phy/dsi_phy.h']
def fetch(path):
    req=urllib.request.Request('https://api.github.com/repos/yzddmr6/linux-sm8750-erhai/contents/'+path+'?ref='+PIN,
                              headers={'User-Agent':'Codex-public-source-review'})
    with urllib.request.urlopen(req,timeout=45) as response:r=json.load(response)
    b=base64.b64decode(r['content'])
    assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==r['sha']
    (OUT/Path(path).name).write_bytes(b)
    return {'path':path,'git_blob':r['sha'],'sha256':hashlib.sha256(b).hexdigest()}
with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(fetch,PATHS))
(OUT/'manifest.json').write_text(json.dumps({'repository':'yzddmr6/linux-sm8750-erhai','commit':PIN,'files':rows},indent=2))
print('Fetched and Git-blob verified three pinned PHY sources')

PIN='d50b305f7da9e14715a25120a4ac7b1a4b8b97c3'
def vendor(path):
    req=urllib.request.Request('https://api.github.com/repos/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750/contents/'+path+'?ref='+PIN,
                              headers={'User-Agent':'Codex-public-source-review'})
    with urllib.request.urlopen(req,timeout=45) as response:r=json.load(response)
    b=base64.b64decode(r['content'])
    assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==r['sha']
    (OUT/Path(path).name).write_bytes(b)
    return {'path':path,'git_blob':r['sha'],'sha256':hashlib.sha256(b).hexdigest()}
vendor_paths=['vendor/qcom/opensource/display-drivers/msm/dsi/'+name for name in
              ['dsi_pll_3nm.c','dsi_pll_3nm.h','dsi_phy_timing_v4_0.c','dsi_panel.c','dsi_display.c']]
vendor_paths+=['vendor/qcom/opensource/display-devicetree/oplus/panel/dsi-panel-AA569-P-3-A0019-dsc-cmd.dtsi']
with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(vendor,vendor_paths))
(OUT/'vendor-manifest.json').write_text(json.dumps({'repository':'OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750','commit':PIN,'files':rows},indent=2))
print('Fetched and Git-blob verified six pinned vendor PHY/panel sources')
