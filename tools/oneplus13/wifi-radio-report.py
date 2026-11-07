#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read channel/regulatory capabilities without scanning or changing RF policy."""
import argparse
import json
import re
import subprocess


def channel_facts(text):
    radios=[]
    for block in re.split(r'^Wiphy ', text, flags=re.M)[1:]:
        bands={'2.4GHz': [], '5GHz': [], '6GHz': []}
        for match in re.finditer(r'^\s*\* ([0-9]{4})(?:\.0+)? MHz \[[0-9]+\]([^\n]*)$', block, re.M):
            frequency=int(match[1]); flags=match[2].lower()
            band=('2.4GHz' if 2400 <= frequency <= 2500 else
                  '5GHz' if 4900 <= frequency < 5925 else
                  '6GHz' if 5925 <= frequency <= 7125 else None)
            if band:
                bands[band].append({'frequency_mhz':frequency,
                                   'disabled':'disabled' in flags,
                                   'no_ir':'no ir' in flags or 'passive scan' in flags,
                                   'radar':'radar' in flags})
        radios.append({'bands':bands})
    return {'radio_count':len(radios),'radios':radios}


def regulatory_facts(text):
    # No raw lines: iw output can contain interface names and addresses.
    return {'domains': [{'alpha2':m[1], 'dfs_region':m[2]}
                        for m in re.finditer(r'^country ([A-Z0-9]{2}): (DFS-[A-Z]+|UNSET)\s*$',text,re.M)],
            'self_managed_radio_count':len(re.findall(r'^phy#[0-9]+ \(self-managed\)',text,re.M))}


def collect(execute=subprocess.run):
    result={'scope':'READ_ONLY_RADIO_CAPABILITIES','scan_requested':False,
            'country_or_sar_set':False}
    for key,command,parser in [('channels',['iw','phy'],channel_facts),
                               ('regulatory',['iw','reg','get'],regulatory_facts)]:
        try:
            output=execute(command,capture_output=True,text=True,timeout=5)
            result[key+'_rc']=output.returncode
            if output.returncode==0 and len(output.stdout)<=262144:
                result[key]=parser(output.stdout)
        except (OSError,subprocess.TimeoutExpired):
            result[key+'_rc']=124
    return result


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print('WIFI_RADIO_REPORT '+json.dumps(collect(),sort_keys=True))
