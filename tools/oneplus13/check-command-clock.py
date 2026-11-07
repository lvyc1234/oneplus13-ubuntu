#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual pinned/patched DSI clock functions; never access a device."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source', required=True, type=Path)
    p.add_argument('--dsi-source', type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--keep-source', type=Path)
    p.add_argument('--sanitize', action='store_true')
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    pin = json.loads((repo / 'devices/oneplus13/source.json').read_text())['kernel']['commit']
    actual = subprocess.check_output(['git', '-C', str(a.kernel_source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != pin:
        raise ValueError('Clock baseline does not match pinned kernel')
    spec = importlib.util.spec_from_file_location('display_check', Path(__file__).with_name('check-display.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extract = module.extract_function
    def kernel_file(name):
        return subprocess.check_output(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
            '-C', str(a.kernel_source), 'show', pin+':'+name]).decode()
    name = 'drivers/gpu/drm/msm/dsi/dsi_host.c'
    original = kernel_file(name)
    patched = (a.dsi_source or a.kernel_source / name).read_text()
    signatures = ['static unsigned long\ndsi_adjust_pclk_for_compression(',
                  'static unsigned long dsi_get_pclk_rate(',
                  'unsigned long dsi_byte_clk_get_rate(']
    baseline = '\n'.join(extract(original, sig) for sig in signatures)
    for one in ['dsi_adjust_pclk_for_compression', 'dsi_get_pclk_rate', 'dsi_byte_clk_get_rate']:
        baseline = baseline.replace(one, 'baseline_'+one)
    signatures[2:2] = ['static bool dsi_use_native422_hs_rate(',
                       'static int dsi_native422_cmd_rates(']
    signatures += ['static int dsi_calc_pclk(', 'int dsi_calc_clk_rate_6g(',
                   'void msm_dsi_host_get_phy_clk_req(']
    functions = '\n'.join(extract(patched, sig) for sig in signatures)
    # Compile the original DRM DSC structure rather than a handwritten DSC ABI.
    header = kernel_file('include/drm/display/drm_dsc.h')
    original_header_sha = hashlib.sha256(header.encode()).hexdigest()
    header = header.replace('#include <drm/display/drm_dp.h>', '')
    prefix = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
typedef uint16_t __be16;
#define __packed __attribute__((packed))
struct dp_sdp_header { u8 bytes[4]; };
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define DIV_ROUND_UP_ULL(n,d) DIV_ROUND_UP(n,d)
#define mult_frac(n,a,b) ((unsigned long)((uint64_t)(n)*(a)/(b)))
#define DBG(...) do {} while(0)
#define pr_err(...) do {} while(0)
static int host_warn(int condition) {return condition;}
#define WARN_ON_ONCE(c) host_warn(!!(c))
#define MIPI_DSI_MODE_VIDEO 1
#define MIPI_DSI_FMT_RGB101010 30
static int mipi_dsi_pixel_format_to_bpp(int format) {return format;}
struct drm_display_mode {int clock,hdisplay,htotal,vdisplay,vtotal;};
struct mipi_dsi_host {int unused;};
struct msm_dsi_host;
struct test_ops {int (*calc_clk_rate)(struct msm_dsi_host *,bool);};
struct msm_dsi_cfg_handler {const struct test_ops *ops;};
struct msm_dsi_host {
  struct mipi_dsi_host base;
  unsigned int lanes, dsc_slice_per_pkt;
  int format; unsigned long mode_flags,hs_rate;
  bool cphy_mode,aa569_batch; int id;
  struct drm_dsc_config *dsc;
  struct drm_display_mode *mode;
  unsigned long pixel_clk_rate,byte_clk_rate,esc_clk_rate,esc_clk;
  const struct msm_dsi_cfg_handler *cfg_hnd;
};
#define to_msm_dsi_host(host) ((struct msm_dsi_host *)(host))
static unsigned long clk_get_rate(unsigned long clock) {return clock;}
struct msm_dsi_phy_clk_request {unsigned long bitclk_rate,escclk_rate;bool aa569_fixed_timing;};
#define DSI_0 0
'''
    suffix = r'''
#define CHECK(c) do {if(!(c)){fprintf(stderr,"check failed line %d\n",__LINE__);return 1;}} while(0)
int main(void) {
 struct drm_display_mode mode={599808,1440,1562,3168,3200};
 struct drm_dsc_config dsc={.bits_per_component=10,.bits_per_pixel=128,
 .native_422=true,.pic_width=1440,.pic_height=3168,.slice_width=720,
 .slice_height=22,.slice_count=2,.slice_chunk_size=720};
 const struct test_ops ops={.calc_clk_rate=dsi_calc_clk_rate_6g};
 const struct msm_dsi_cfg_handler cfg={.ops=&ops};
 struct msm_dsi_host h={.lanes=4,.format=30,.dsc=&dsc,.mode=&mode,
 .dsc_slice_per_pkt=2,.hs_rate=1363200000,.esc_clk=19200000,.cfg_hnd=&cfg};
 struct msm_dsi_phy_clk_request req={0};
 CHECK(baseline_dsi_byte_clk_get_rate(&h.base,false,&mode)*8==1457280000UL);
 CHECK(dsi_byte_clk_get_rate(&h.base,false,&mode)==170400000UL);
 CHECK(!dsi_calc_clk_rate_6g(&h,false));
 CHECK(h.byte_clk_rate==170400000UL && h.pixel_clk_rate==181760000UL);
 msm_dsi_host_get_phy_clk_req(&h.base,&req,false);
 CHECK(req.bitclk_rate==1363200000UL && req.escclk_rate==19200000UL);
 CHECK(!req.aa569_fixed_timing);
 h.aa569_batch=true;
 msm_dsi_host_get_phy_clk_req(&h.base,&req,false);
 CHECK(req.aa569_fixed_timing&&req.bitclk_rate==1363200000UL);
 for(int c=0;c<7;c++) {
   h.id=c==0?1:0;h.lanes=c==1?3:4;h.cphy_mode=c==2;
   h.mode_flags=c==3?MIPI_DSI_MODE_VIDEO:0;h.hs_rate=c==4?1363200001:1363200000;
   dsc.native_422=c!=5;
   memset(&req,0xff,sizeof(req));
   msm_dsi_host_get_phy_clk_req(&h.base,&req,c==6);
   CHECK(!req.bitclk_rate&&!req.escclk_rate&&!req.aa569_fixed_timing);
 }
 h.id=0;h.lanes=4;h.cphy_mode=false;h.mode_flags=0;h.hs_rate=1363200000;
 dsc.native_422=true;h.aa569_batch=false;
 /* A non-byte-aligned ceiling must be rounded DOWN. */
 h.hs_rate+=7;
 CHECK(!dsi_calc_clk_rate_6g(&h,false) && h.byte_clk_rate*8<=h.hs_rate);
 CHECK(h.byte_clk_rate==170400000UL);
 h.hs_rate=1363200000;
 /* Existing pathways must produce exactly their original clock result. */
 for(int c=0;c<6;c++) {
   h.mode_flags=(c==1)?MIPI_DSI_MODE_VIDEO:0;
   h.cphy_mode=(c==2); bool bonded=(c==3);
   dsc.native_422=(c!=4); h.hs_rate=(c==0)?0:1363200000;
   h.dsc=(c==5)?NULL:&dsc;
   CHECK(dsi_byte_clk_get_rate(&h.base,bonded,&mode)==
         baseline_dsi_byte_clk_get_rate(&h.base,bonded,&mode));
   CHECK(!dsi_calc_clk_rate_6g(&h,bonded));
   CHECK(h.pixel_clk_rate==baseline_dsi_get_pclk_rate(&mode,h.dsc,bonded));
 }
 h.dsc=&dsc;dsc.native_422=true;h.mode_flags=0;h.cphy_mode=false;
 /* Unsafe geometry, rates, packet grouping and over-budget refresh fail. */
 for(int c=0;c<10;c++) {
   struct msm_dsi_host bad=h; struct drm_dsc_config bd=dsc;
   struct drm_display_mode bm=mode;bad.dsc=&bd;bad.mode=&bm;
   if(c==0)bad.hs_rate=100000000;
   if(c==1)bad.hs_rate=1;
   if(c==2)bad.lanes=0;
   if(c==3)bad.format=24;
   if(c==4)bd.slice_chunk_size=0;
   if(c==5)bad.dsc_slice_per_pkt=3;
   if(c==6)bm.clock=0;
   if(c==7)bm.clock=2000000;
   if(c==8)bm.htotal=1439;
   if(c==9)bd.pic_width=1441;
   CHECK(dsi_calc_clk_rate_6g(&bad,false)<0);
   CHECK(dsi_byte_clk_get_rate(&bad.base,false,&bm)==0);
 }
 puts("native422 command clock, PHY request, 6 legacy controls, 10 invalid controls passed");
 return 0;
}
'''
    # drm_dsc_get_bpp_int is unchanged and required by the real clock path.
    helpers = kernel_file('drivers/gpu/drm/display/drm_dsc_helper.c')
    helper = extract(helpers, 'u32 drm_dsc_get_bpp_int(')
    combined = prefix+header+helper+baseline+functions+suffix
    with tempfile.TemporaryDirectory(prefix='oneplus13-clock-') as tmp:
        source, exe = Path(tmp)/'clock.c', Path(tmp)/'clock.exe'
        source.write_text(combined, encoding='utf-8')
        if a.keep_source:
            a.keep_source.write_text(combined, encoding='utf-8')
        command = ['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror']
        if a.sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command+[str(source), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    line_bytes = 2*720+7
    minimum_us = (line_bytes*3168*8*1_000_000 + 1363200000*4-1)//(1363200000*4)
    if minimum_us > 6800:
        raise ValueError('Public packet lower bound exceeds stock transfer budget')
    report = {'scope': 'HOST_C_DSI_COMMAND_CLOCK_ONLY', 'hardware_tested': False,
              'kernel_commit': pin, 'dsi_source_sha256': hashlib.sha256(patched.encode()).hexdigest(),
              'drm_header_sha256': original_header_sha, 'sanitizers': a.sanitize,
              'lane_bit_rate': 1363200000, 'byte_clk_rate': 170400000,
              'pixel_clk_rate': 181760000, 'phy_request_matches_byte_clock': True,
              'baseline_lane_bit_rate': 1457280000,
              'legacy_baseline_controls': 6, 'invalid_controls_rejected': 10,
              'non_byte_aligned_ceiling_rounded_down': True,
              'stock_transfer_budget_us': 6800,
              'packet_only_minimum_transfer_us_ceil': minimum_us,
              'dpu_transfer_window_programmed': False,
              'pll_rounding_verified': False}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
