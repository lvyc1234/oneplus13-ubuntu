#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check actual guarded AA569 PHY selection and vendor V7.2 timing writes offline."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--sanitize',action='store_true')
    a=p.parse_args()
    repo=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('display_check',Path(__file__).with_name('check-display.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);extract=m.extract_function
    pin=json.loads((repo/'devices/oneplus13/source.json').read_text())['kernel']['commit']
    assert subprocess.check_output(['git','-C',str(a.kernel_source),'rev-parse','HEAD'],text=True).strip()==pin
    def original(path):return subprocess.check_output(['git','-C',str(a.kernel_source),'show',pin+':'+path]).decode()
    phy=(a.kernel_source/'drivers/gpu/drm/msm/dsi/phy/dsi_phy_7nm.c').read_text()
    enable=extract(phy,'static int dsi_7nm_phy_enable(')
    assert enable.index('dsi_aa569_fixed_timing(phy, clk_req)')<enable.index('dsi_phy_hw_v4_0_is_pll_on(phy)')
    # Actual pre-existing timing register writers are kept byte-for-byte.
    old=original('drivers/gpu/drm/msm/dsi/phy/dsi_phy_7nm.c')
    start='\t} else {\n\t\twritel(0x00, base + REG_DSI_7nm_PHY_CMN_TIMING_CTRL_0);'
    offset=enable.index(start)
    block=enable[offset+len('\t} else {\n'):enable.index('\n\t}\n',offset)]
    assert block in old
    calc=extract(original('drivers/gpu/drm/msm/dsi/phy/dsi_phy.c'),'int msm_dsi_dphy_timing_calc_v4(')
    interp=extract(original('drivers/gpu/drm/msm/dsi/phy/dsi_phy.c'),'static inline s32 linear_inter(')
    helper=extract(phy,'static int dsi_aa569_fixed_timing(')
    header=original('drivers/gpu/drm/msm/dsi/phy/dsi_phy.h')
    types=extract(header,'struct msm_dsi_dphy_timing {')+';'
    source=(repo/'tests/fixed_phy_test.c').read_text()
    register_names=['DSI_7nm_PHY_CMN_TIMING_CTRL_'+str(i) for i in range(14)]
    source=source.replace('/* REGISTERS */','\n'.join('#define REG_'+name+' '+str(i) for i,name in enumerate(register_names)))
    source=source.replace('/* TYPES */',types).replace('/* FUNCTIONS */',interp+'\n'+calc+'\n'+helper)
    source=source.replace('/* WRITE_TIMINGS */',block)
    # Preserve LP64 unsigned long semantics on Windows.
    source=source.replace('unsigned long','uint64_t')
    with tempfile.TemporaryDirectory(prefix='oneplus13-fixed-phy-') as tmp:
        c,exe=Path(tmp)/'phy.c',Path(tmp)/'phy.exe';c.write_text(source,encoding='utf-8')
        cmd=['gcc','-std=gnu11','-Wall','-Wextra','-Werror']
        if a.sanitize:cmd+=['-fsanitize=address,undefined','-fno-omit-frame-pointer']
        subprocess.run(cmd+[str(c),'-o',str(exe)],check=True)
        output=subprocess.check_output([str(exe)],text=True).strip()
    assert output=='OK fixed AA569 timing, register writes, rejection and legacy controls'
    result={'scope':'HOST_C_FIXED_AA569_PHY_ONLY','hardware_tested':False,'sanitizers':a.sanitize,
        'kernel_commit':pin,'production_timing_bytes':[0,44,12,12,29,26,12,12,11,2,4,0,36,17],
        'real_mmio_writers_match_public_profile':True,'rejected_profiles_before_mmio':7,
        'legacy_profiles_unchanged':33,'shared_host_timing_updated':True,
        'all_rejection_outputs_unchanged':True,'selector_called_before_any_phy_mmio':True,
        'phy_source_sha256':hashlib.sha256(phy.encode()).hexdigest()}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(output)


if __name__=='__main__':main()
