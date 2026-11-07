#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute pinned platform/vendor PLL arithmetic and platform D-PHY timing offline."""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import subprocess
import tempfile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--sanitize',action='store_true')
    a=p.parse_args()
    repo=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('display_check',Path(__file__).with_name('check-display.py'))
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    extract=mod.extract_function
    manifests=[json.loads((a.sources/name).read_text()) for name in ['manifest.json','vendor-manifest.json']]
    assert manifests[0]['commit']=='7a81d184e3098624d4342d8f4eff5e23674bcc9f'
    assert manifests[1]['commit']=='d50b305f7da9e14715a25120a4ac7b1a4b8b97c3'
    for manifest in manifests:
        for entry in manifest['files']:
            b=(a.sources/Path(entry['path']).name).read_bytes()
            assert hashlib.sha256(b).hexdigest()==entry['sha256']
            assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==entry['git_blob']
    pll=(a.sources/'dsi_phy_7nm.c').read_text()
    phy=(a.sources/'dsi_phy.c').read_text()
    vendor=(a.sources/'dsi_pll_3nm.c').read_text()
    # Pin the actual SM8750 route and divider registration semantics too.
    cfg=extract(pll,'const struct msm_dsi_phy_cfg dsi_phy_3nm_8750_cfgs =')
    assert '.quirks = DSI_PHY_7NM_QUIRK_V7_2' in cfg
    assert '.min_pll_rate = 600000000UL' in cfg and '.max_pll_rate = 5000000000UL' in cfg
    assert 'ret = msm_dsi_dphy_timing_calc_v4(timing, clk_req);' in pll
    assert '0, 2, CLK_DIVIDER_POWER_OF_TWO' in pll
    assert '0, 4, CLK_DIVIDER_ONE_BASED' in pll
    platform_calc=extract(pll,'static void dsi_pll_calc_dec_frac(')
    vendor_calc=extract(vendor,'static void dsi_pll_calc_dec_frac(').replace('dsi_pll_calc_dec_frac','vendor_dec_frac').replace('struct dsi_pll_config','struct vendor_config')
    functions='\n'.join([platform_calc,vendor_calc,
        extract(pll,'static unsigned long dsi_pll_7nm_vco_recalc_rate('),
        extract(pll,'static int dsi_pll_7nm_clk_determine_rate('),
        extract(phy,'static inline s32 linear_inter('),
        extract(phy,'int msm_dsi_dphy_timing_calc_v4(')])
    thresholds=[163000000,175000000,325000000,350000000,650000000,700000000,
                1300000000,2500000000,4000000000]
    rates=sorted(set([0,600000000,1363200000,2726400000,4089600000,5000000000,5000000001]+
                     [v+d for v in thresholds for d in [-1,0,1]]+
                     [1363200000+d for d in range(-16,17)]))
    cases=[]
    for rate in rates:
        fixed=rate*(1<<18)//38400000
        achieved=fixed*38400000//(1<<18)
        inv=0xa0 if rate<163000000 else 0x20 if rate<175000000 else 0xa0 if rate<325000000 else 0x20 if rate<350000000 else 0xa0 if rate<650000000 else 0x20 if rate<700000000 else 0xa0 if rate<1300000000 else 0x20 if rate<2500000000 else 0 if rate<4000000000 else 0x40
        cases.append(f'{{{rate}ULL,{fixed>>18},{fixed&((1<<18)-1)},{achieved}ULL,{inv}}}')
    vectors='static const struct {u64 rate;u32 dec,frac;u64 actual;u32 inv;} cases[] = {'+','.join(cases)+'};\n'
    template=(repo/'tests/pll_timing_test.c').read_text()
    template=template.replace('/* PLL_CONFIG */',extract(pll,'struct dsi_pll_config {')+';')
    template=template.replace('/* TIMING_STRUCT */',extract((a.sources/'dsi_phy.h').read_text(),'struct msm_dsi_dphy_timing {')+';')
    source=template.replace('/* REAL_FUNCTIONS */',functions).replace('/* VECTORS */',vectors)
    # Preserve ARM64 LP64 unsigned-long width on Windows LLP64 hosts.
    source=source.replace('unsigned long','uint64_t')
    with tempfile.TemporaryDirectory(prefix='oneplus13-pll-') as tmp:
        c,exe=Path(tmp)/'pll.c',Path(tmp)/'pll.exe'
        c.write_text(source,encoding='utf-8')
        cmd=['gcc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter']
        if a.sanitize:cmd+=['-fsanitize=address,undefined','-fno-omit-frame-pointer']
        subprocess.run(cmd+[str(c),'-o',str(exe)],check=True)
        output=subprocess.check_output([str(exe)],text=True)
    lines=output.splitlines()
    assert lines[-1]=='OK real platform/vendor PLL and D-PHY timing'
    timings=json.loads(next(line[7:] for line in lines if line.startswith('TIMING ')))
    # Verify the normalized output order against the actual register writers.
    fields=['clk_zero','clk_prepare','clk_trail','hs_exit','hs_zero','hs_prepare','hs_trail','hs_rqst']
    for i,field in enumerate(fields,1):
        assert f'writel(timing->{field}, base + REG_DSI_7nm_PHY_CMN_TIMING_CTRL_{i});' in pll
    for i,field in [(12,'clk_pre'),(13,'clk_post')]:
        assert re.search(r'writel\(timing->shared_timings\.'+field+r',\s*base \+ REG_DSI_7nm_PHY_CMN_TIMING_CTRL_'+str(i)+r'\);',pll)
    mapping=(a.sources/'dsi_phy_timing_v4_0.c').read_text()
    for i,field in list(enumerate(fields,1))+[(12,'clk_pre'),(13,'clk_post')]:
        assert f'timing->lane_v4[{i}] = desc->{field}.reg_value;' in mapping
    panel=(a.sources/'dsi-panel-AA569-P-3-A0019-dsc-cmd.dtsi').read_text()
    mode='&dsi_panel_AA569_P_3_A0019_dsc_cmd {\n\tqcom,ulps-enabled;'
    assert panel.count(mode)==1
    stock_block=extract(panel[panel.index(mode):],'timing@wqhd_sdc_120 {')
    match=re.search(r'qcom,mdss-dsi-panel-phy-timings\s*=\s*\[([^]]+)\]',stock_block)
    assert match
    stock=list(bytes.fromhex(match[1]))
    assert len(stock)==14
    parser=(a.sources/'dsi_panel.c').read_text()
    assert 'priv_info->phy_timing_val[i] = data[i];' in extract(parser,'static int dsi_panel_parse_phy_timing(')
    assert 'mode->priv_info->phy_timing_val' in (a.sources/'dsi_display.c').read_text()
    result={'scope':'HOST_C_PLL_QUANTIZATION_AND_PHY_TIMING_ONLY','hardware_tested':False,
        'phone_operations':0,'sanitizers':a.sanitize,'source_manifests':manifests,
        'platform_vendor_pll_cases':len(rates),'nominal_lane_rate':1363200000,
        'nominal_fractional_vco_rates_exact':[1363200000,2726400000,4089600000],
        'nominal_quantization_error_hz':0,'vco_clamp_bounds_checked':True,
        'v7_2_inverter_thresholds_checked':9,'nominal_dphy_timing_bytes':timings,
        'vendor_public_timing_bytes':stock,
        'timing_differences':[{'index':i,'platform':v,'vendor':stock[i]} for i,v in enumerate(timings) if v!=stock[i]],
        'clock_tree_runtime_selection':'UNVERIFIED','physical_lock_and_frequency':'UNVERIFIED',
        'te_mdp_scheduling':'UNVERIFIED','source_sha256':hashlib.sha256(source.encode()).hexdigest()}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['platform_vendor_pll_cases','nominal_quantization_error_hz','nominal_dphy_timing_bytes','timing_differences']}))


if __name__=='__main__':main()
