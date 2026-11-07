#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute real CCF divider/pixel callbacks against the pinned SM8750 topology."""
import argparse
import hashlib
import importlib.util
import json
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
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);extract=m.extract_function
    manifest=json.loads((a.sources/'manifest.json').read_text())
    for e in manifest:
        b=(a.sources/e['local']).read_bytes()
        assert hashlib.sha256(b).hexdigest()==e['sha256']
        assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==e['git_blob']
    div=(a.sources/'platform/clk-divider.c').read_text()
    rcg=(a.sources/'platform/clk-rcg2.c').read_text()
    cc=(a.sources/'platform/dispcc-sm8750.c').read_text()
    cfg=extract(cc,'static struct clk_rcg2 disp_cc_mdss_pclk0_clk_src =')
    assert '.ops = &clk_pixel_ops' in cfg and '.mnd_width = 8' in cfg
    pll=(a.sources/'platform/dsi_phy_7nm.c').read_text()
    assert 'phy_pll_out_dsi_parent, 0,' in pll
    assert '4, 4, CLK_DIVIDER_ONE_BASED' in pll
    assert 'pll_bit, 0, 1, 2' in pll
    functions='\n'.join(extract(div,sig) for sig in [
        'static unsigned int _get_table_maxdiv(', 'static unsigned int _get_table_mindiv(',
        'static unsigned int _get_maxdiv(', 'static int _round_up_table(',
        'static int _round_down_table(', 'static int _div_round_up(',
        'static int _div_round_closest(', 'static int _div_round(',
        'static bool _is_best_div(', 'static int _next_div(',
        'static int clk_divider_bestdiv(', 'int divider_determine_rate('])
    functions+='\n'+extract(rcg,'static const struct frac_entry frac_table_pixel[] =')+';\n'
    functions+='\n'.join(extract(rcg,sig) for sig in ['static int clk_pixel_determine_rate(', 'static int clk_pixel_set_rate('])
    te=(a.sources/'platform/dpu_encoder_phys_cmd.c').read_text()
    functions+='\n'+extract(te,'static void dpu_encoder_phys_cmd_tearcheck_config(')
    template=(repo/'tests/clock_tree_test.c').read_text()
    source=template.replace('/* REAL_FUNCTIONS */',functions).replace('unsigned long','uint64_t')
    source=source.replace('ULONG_MAX','UINT64_MAX')
    with tempfile.TemporaryDirectory(prefix='oneplus13-clocktree-') as tmp:
        c,exe=Path(tmp)/'clock.c',Path(tmp)/'clock.exe';c.write_text(source,encoding='utf-8')
        cmd=['gcc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-sign-compare']
        if a.sanitize:cmd+=['-fsanitize=address,undefined','-fno-omit-frame-pointer']
        subprocess.run(cmd+[str(c),'-o',str(exe)],check=True)
        output=subprocess.check_output([str(exe)],text=True).strip()
    assert output=='OK actual divider/pixel callbacks and command TE configuration'
    result={'scope':'HOST_C_CLOCK_CALLBACK_GRAPH_FIXTURE_ONLY','hardware_tested':False,
        'sanitizers':a.sanitize,'byte_rate':170400000,'lane_rate':1363200000,
        'pixel_rate':181760000,'fixed_pll_mux_routes_checked':2,
        'direct_bit_route':{'phy_pixel_div':5,'rcg_m':2,'rcg_n':3},
        'half_bit_route':{'phy_pixel_div':1,'rcg_m':4,'rcg_n':15},
        'pixel_divider_has_no_set_rate_parent':True,
        'actual_divider_and_pixel_callbacks_executed':True,
        'te_interface_and_pingpong_config_checked':True,
        'te_vsync_count_19p2mhz_120hz_3200lines':50,
        'te_absent_clock_and_callback_fail_closed':True,
        'ccf_core_locking_and_hardware_not_emulated':True,
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'sources':manifest}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(output)


if __name__=='__main__':main()
