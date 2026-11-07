#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute actual DPU budget/check/ICC functions; never measures frame timing."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import tempfile


def native_opp_rates(blob):
    from stock import Fdt, cells
    tree = Fdt(blob)
    if tree.nodes['/']['compatible'].split(b'\0')[:2] != [b'oneplus,dodge',b'qcom,sm8750']:
        raise ValueError('Wrong native board')
    mdp = [p for p,v in tree.nodes.items() if b'qcom,sm8750-dpu' in v.get('compatible', b'').split(b'\0')]
    if (len(mdp) != 1 or 'oneplus,aa569-static-spr' not in tree.nodes[mdp[0]] or
            any(tree.nodes.get('/'.join(mdp[0].split('/')[:i]) or '/',{}).get('status') == b'disabled\0'
                for i in range(1,len(mdp[0].split('/'))+1))):
        raise ValueError('Actual enabled native AA569 DPU DTB required')
    handle = cells(tree.nodes[mdp[0]]['operating-points-v2'])
    opp = [p for p,v in tree.nodes.items() if cells(v.get('phandle', b'')) == handle]
    if len(handle) != 1 or len(opp) != 1:
        raise ValueError('Invalid MDP OPP reference')
    values = [v['opp-hz'] for p,v in tree.nodes.items()
              if p.startswith(opp[0]+'/') and 'opp-hz' in v and v.get('status') != b'disabled\0']
    if any(len(v)!=8 for v in values):raise ValueError('Invalid 64-bit OPP frequency')
    rates = sorted(int.from_bytes(v,'big') for v in values)
    if not rates or not rates[0] or max(rates) != 575000000:
        raise ValueError('Unexpected fixed SM8750 MDP OPP ceiling')
    return rates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-source', required=True, type=Path)
    parser.add_argument('--patched-source', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--native-dtb', required=True, type=Path)
    args = parser.parse_args()
    from stock import Fdt
    blob = args.native_dtb.read_bytes()
    rates = native_opp_rates(blob)
    tree=Fdt(blob)
    mdp=next(p for p,v in tree.nodes.items() if b'qcom,sm8750-dpu' in v.get('compatible',b'').split(b'\0'))
    highest=next(p for p,v in tree.nodes.items() if v.get('opp-hz')==(575000000).to_bytes(8,'big'))
    for bad in range(6):
        fixture=Fdt(blob)
        if bad==0:fixture.nodes[mdp].pop('oneplus,aa569-static-spr')
        if bad==1:fixture.nodes[mdp]['operating-points-v2']=b'\xff'*4
        if bad==2:fixture.nodes[highest]['opp-hz']=(1000000000).to_bytes(8,'big')
        if bad==3:fixture.nodes[mdp]['status']=b'disabled\0'
        if bad==4:fixture.nodes['/']['compatible']=b'qcom,sm8750\0'
        if bad==5:fixture.nodes[highest]['opp-hz']=b'\0'*4
        try:native_opp_rates(fixture.to_blob())
        except (ValueError,KeyError):pass
        else:raise AssertionError('Invalid native OPP input accepted')
    repo = Path(__file__).resolve().parents[2]
    pin = json.loads((repo/'devices/oneplus13/source.json').read_text())['kernel']['commit']
    if subprocess.check_output(['git', '-C', str(args.kernel_source), 'rev-parse', 'HEAD'], text=True).strip() != pin:
        raise ValueError('Kernel pin mismatch')
    path = 'drivers/gpu/drm/msm/disp/dpu1/dpu_core_perf.c'
    source = ((args.patched_source or args.kernel_source)/path).read_text()
    baseline = subprocess.check_output(['git', '-C', str(args.kernel_source), 'show', pin+':'+path], text=True)
    spec = importlib.util.spec_from_file_location('display_check', Path(__file__).with_name('check-display.py'))
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    extract = check.extract_function
    signatures = ['static int dpu_aa569_spr_scale(', 'static int dpu_aa569_spr_calc(', 'static int dpu_aa569_spr_budget(',
                  'int dpu_core_perf_crtc_check(', 'static int _dpu_core_perf_crtc_update_bus(']
    plane_source = ((args.patched_source or args.kernel_source)/'drivers/gpu/drm/msm/disp/dpu1/dpu_plane.c').read_text()
    baseline_plane = subprocess.check_output(['git', '-C', str(args.kernel_source), 'show',
        pin+':drivers/gpu/drm/msm/disp/dpu1/dpu_plane.c'], text=True)
    for signature in ['static u64 _dpu_plane_calc_bw(', 'static u64 _dpu_plane_calc_clk(']:
        if extract(plane_source, signature) != extract(baseline_plane, signature):
            raise ValueError('Original plane helpers must remain unchanged')
    # The untouched kernel BW helper compares signed porch sums with u32.
    # Limit the host-only -Wextra exception to that original helper; retain
    # all diagnostics for the new wrapper, budget and integration functions.
    functions = ('#pragma GCC diagnostic push\n#pragma GCC diagnostic ignored "-Wsign-compare"\n'+
                 extract(plane_source, 'static u64 _dpu_plane_calc_bw(')+
                 '\n#pragma GCC diagnostic pop\n')
    functions += '\n'.join(extract(plane_source, s) for s in [
        'static u64 _dpu_plane_calc_clk(', 'int dpu_plane_calc_aa569_perf('])
    functions += '\n'+'\n'.join(extract(source, s) for s in signatures)
    functions += '\n'+extract(source, 'u64 dpu_aa569_spr_max_pipe_rate(')
    for signature, name, new_name in [(signatures[3], 'dpu_core_perf_crtc_check', 'baseline_check'),
                                      (signatures[4], '_dpu_core_perf_crtc_update_bus', 'baseline_bus')]:
        functions += '\n'+extract(baseline, signature).replace(name+'(', new_name+'(', 1)
    functions += ('\n#pragma GCC diagnostic push\n#pragma GCC diagnostic ignored "-Wsign-compare"\n'+
                  extract(plane_source, 'static int dpu_plane_split(')+'\n#pragma GCC diagnostic pop\n')
    prefix = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint64_t u64;typedef uint32_t u32;
#define U32_MAX UINT32_MAX
#define EINVAL 22
#define E2BIG 7
#define ENODEV 19
#ifndef EOVERFLOW
#define EOVERFLOW 75
#endif
#define check_mul_overflow(a,b,out) __builtin_mul_overflow(a,b,out)
#define check_add_overflow(a,b,out) __builtin_add_overflow(a,b,out)
#define mult_frac(x,n,d) ((u64)(x)*(n)/(d))
#define do_div(n,d) ((n)/=(d))
#define max(a,b) ((a)>(b)?(a):(b))
#define DIV_ROUND_UP_ULL(a,b) (((a)+(b)-1)/(b))
#define DPU_ERROR(...) ((void)0)
#define DRM_DEBUG_ATOMIC(...) ((void)0)
#define CHECK(x) do{if(!(x)){fprintf(stderr,"Budget check failed at %d\n",__LINE__);exit(1);}}while(0)
enum {DPU_PERF_MODE_NORMAL,DPU_PERF_MODE_MINIMUM,DPU_PERF_MODE_FIXED};
enum {RT_CLIENT,NRT_CLIENT};
struct dpu_core_perf_params {u32 max_per_pipe_ib;u64 bw_ctl,core_clk_rate;};
struct dpu_perf_cfg {u32 max_bw_high,min_dram_ib,bw_inefficiency_factor,clk_inefficiency_factor,min_prefill_lines;};
struct dpu_caps {u32 max_linewidth;};
struct dpu_mdss_cfg {struct dpu_perf_cfg *perf;struct dpu_caps *caps;};
struct drm_rect {int x1,y1,x2,y2;};
struct dpu_sw_pipe_cfg {struct drm_rect src_rect,dst_rect;unsigned rotation;};
struct drm_display_mode {int hdisplay,vdisplay,vtotal,vsync_start,vsync_end;};
struct msm_format {int bpp,width,height;};
struct drm_plane {struct dpu_mdss_cfg *catalog;int base;};
struct drm_plane_state {struct drm_plane *plane;bool visible;struct msm_format *fb;struct drm_rect src,dst;unsigned rotation;};
struct dpu_plane_state {struct drm_plane_state base;struct dpu_sw_pipe_cfg pipe_cfg[2];};
#define PIPES_PER_PLANE 2
#define to_dpu_plane(p) (p)
#define dpu_plane drm_plane
#define to_dpu_plane_state(p) ((struct dpu_plane_state *)(p))
static int drm_rect_width(const struct drm_rect*r){return r->x2-r->x1;}
static int drm_rect_height(const struct drm_rect*r){return r->y2-r->y1;}
static int drm_mode_vrefresh(const struct drm_display_mode*m){(void)m;return 120;}
static const struct msm_format *msm_framebuffer_format(struct msm_format*f){return f;}
struct dpu_core_perf {struct dpu_perf_cfg *perf_cfg;u64 max_opp_clk_rate,max_core_clk_rate;struct {u32 mode;}perf_tune;u32 fix_core_ab_vote,fix_core_ib_vote;};
struct dpu_kms {void *aa569_spr;int num_paths,path[4];struct dpu_core_perf perf;};
struct drm_crtc_state {bool enable;struct drm_display_mode adjusted_mode;struct drm_plane_state *planes[2];unsigned num_mixers;};
#define drm_atomic_crtc_state_for_each_plane_state(plane,pstate,state) \
 for(unsigned idx=0;idx<2&&((pstate)=(state)->planes[idx])&&((plane)=(pstate)->plane);idx++)
struct dpu_crtc_state {struct drm_crtc_state base;struct dpu_core_perf_params new_perf;};
struct drm_crtc {void *dev;};
static struct dpu_kms kms;
static struct dpu_core_perf_params input, aggregate;
static unsigned icc_calls;static int icc_error_at=-1;static u32 icc_avg[4],icc_peak[4];
static u64 div_u64_rem(u64 v,u32 d,u32*r){*r=v%d;return v/d;}
static u64 div_u64(u64 v,u32 d){return v/d;}
static u64 div64_u64(u64 v,u64 d){return v/d;}
static unsigned dpu_crtc_get_num_lm(const struct drm_crtc_state*s){return s->num_mixers;}
static struct dpu_kms *_dpu_plane_get_kms(void*p){(void)p;return &kms;}
static bool dpu_use_virtual_planes;
#define PIPES_PER_STAGE 2
#define DPU_DEBUG_PLANE(...) ((void)0)
#define min(a,b) ((a)<(b)?(a):(b))
/* Geometry fixture: no rotation, clipping or scaling. Execute the real split
 * and budget code; this deliberately does not claim DRM geometry coverage. */
static void drm_rect_fp_to_int(struct drm_rect*d,const struct drm_rect*s){*d=(struct drm_rect){s->x1>>16,s->y1>>16,s->x2>>16,s->y2>>16};}
static void drm_rect_rotate(struct drm_rect*r,int w,int h,unsigned rot){(void)r;(void)w;(void)h;CHECK(rot==0);}
static void drm_rect_rotate_inv(struct drm_rect*r,int w,int h,unsigned rot){drm_rect_rotate(r,w,h,rot);}
static bool drm_rect_clip_scaled(struct drm_rect*s,struct drm_rect*d,const struct drm_rect*clip){(void)s;CHECK(d->x1>=clip->x1&&d->x2<=clip->x2&&d->y1>=clip->y1&&d->y2<=clip->y2);return true;}
static struct dpu_kms *_dpu_crtc_get_kms(struct drm_crtc*c){(void)c;return &kms;}
static int dpu_crtc_get_client_type(struct drm_crtc*c){(void)c;return RT_CLIENT;}
#define to_dpu_crtc_state(p) ((struct dpu_crtc_state *)(p))
static void _dpu_core_perf_calc_crtc(struct dpu_core_perf*p,struct drm_crtc*c,struct drm_crtc_state*s,struct dpu_core_perf_params*out){(void)p;(void)c;(void)s;*out=input;}
/* Deliberately expose an old zero state: the new enabled state must be checked
 * directly, without assuming that the generic aggregator already sees it. */
static void dpu_core_perf_aggregate(void*d,int t,struct dpu_core_perf_params*out){(void)d;(void)t;*out=aggregate;}
static int icc_set_bw(int path,u32 avg,u32 peak){unsigned n=icc_calls++;(void)path;icc_avg[n]=avg;icc_peak[n]=peak;return (int)n==icc_error_at?-5:0;}
'''
    rng = random.Random(6800)
    cases = [(0, 0, 0), (1, 1, 1), (102, 102, 102), (103, 103, 103),
             (2211840000, 580608000, 800000)]
    cases += [(rng.randrange(1, 10_000_000_000), rng.randrange(1, 600_000_000),
               rng.randrange(1, 2_000_000)) for _ in range(256)]
    fixture = 'static const u64 vectors[][6]={\n'
    for bw, clock, peak in cases:
        scaled = lambda n: (n*125+101)//102
        values = [bw, clock, peak, scaled(bw), scaled(clock), max(scaled(peak), (scaled(bw)+999)//1000)]
        fixture += '{'+','.join(str(n)+'ULL' for n in values)+'},\n'
    fixture += '};\n'
    suffix = r'''
int main(void){
 struct dpu_perf_cfg cfg={.max_bw_high=28500000,.min_dram_ib=800000,.bw_inefficiency_factor=120,.clk_inefficiency_factor=105,.min_prefill_lines=35};
 struct drm_crtc crtc={0};struct dpu_crtc_state state={.base.enable=true,.base.adjusted_mode={1440,3168,3200,3184,3188},.base.num_mixers=2};
 struct dpu_caps caps={4096};struct dpu_mdss_cfg catalog={&cfg,&caps};struct drm_plane plane={.catalog=&catalog};struct msm_format format={4,1440,3168};
 struct dpu_plane_state pstate={.base={.plane=&plane,.visible=true,.fb=&format},.pipe_cfg={{{0,0,720,3168},{0,0,720,3168},0},{{720,0,1440,3168},{720,0,1440,3168},0}}};
 state.base.planes[0]=&pstate.base;
 kms.perf.perf_cfg=&cfg;kms.perf.max_opp_clk_rate=1000000000;kms.num_paths=2;
 for(unsigned i=0;i<sizeof(vectors)/sizeof(vectors[0]);i++){
  const u64*v=vectors[i];struct dpu_core_perf_params p={.bw_ctl=v[0],.core_clk_rate=v[1],.max_per_pipe_ib=v[2]};
  CHECK(!dpu_aa569_spr_budget(&p,1000000000));
  CHECK(p.bw_ctl==v[3]&&p.core_clk_rate==v[4]&&p.max_per_pipe_ib==v[5]);
 }
 for(int i=0;i<5;i++){
  struct dpu_core_perf_params p={.bw_ctl=2000000000,.core_clk_rate=580608000,.max_per_pipe_ib=800000};
  u64 limit=1000000000;
  if(i==0)p.bw_ctl=UINT64_MAX;
  if(i==1)p.core_clk_rate=UINT64_MAX;
  if(i==2)p.max_per_pipe_ib=UINT32_MAX;
  if(i==3)limit=600000000;
  if(i==4)limit=0;
  struct dpu_core_perf_params saved=p;CHECK(dpu_aa569_spr_budget(&p,limit)<0);CHECK(!memcmp(&p,&saved,sizeof(p)));
 }
 input=(struct dpu_core_perf_params){.bw_ctl=2211840000,.core_clk_rate=580608000,.max_per_pipe_ib=800000};
 kms.aa569_spr=&kms;
 CHECK(!dpu_core_perf_crtc_check(&crtc,&state.base));
 CHECK(state.new_perf.core_clk_rate==355764706&&state.new_perf.bw_ctl==3557647059ULL);
 /* NEW visible geometry is authoritative even though old cached votes differ. */
 struct dpu_core_perf_params full=state.new_perf;
 pstate.pipe_cfg[1].src_rect.x2=720;pstate.pipe_cfg[0].src_rect.x2=360;pstate.pipe_cfg[0].dst_rect.x2=360;
 CHECK(!dpu_core_perf_crtc_check(&crtc,&state.base));CHECK(state.new_perf.bw_ctl<full.bw_ctl);
 pstate.pipe_cfg[0].dst_rect.y2=0;CHECK(dpu_core_perf_crtc_check(&crtc,&state.base)==-EINVAL);
 pstate.pipe_cfg[0].dst_rect.y2=3168;pstate.pipe_cfg[0].src_rect.x2=720;pstate.pipe_cfg[0].dst_rect.x2=720;pstate.pipe_cfg[1].src_rect.x2=1440;
 /* A fresh enable has an empty old aggregate. Still reject the NEW limits. */
 kms.perf.max_opp_clk_rate=300000000;
 CHECK(dpu_core_perf_crtc_check(&crtc,&state.base)==-E2BIG);
 CHECK(!baseline_check(&crtc,&state.base));
 kms.perf.max_opp_clk_rate=1000000000;cfg.max_bw_high=2500000;
 CHECK(dpu_core_perf_crtc_check(&crtc,&state.base)==-E2BIG);CHECK(!baseline_check(&crtc,&state.base));
 cfg.max_bw_high=28500000;
 for(int mode=1;mode<=2;mode++){kms.perf.perf_tune.mode=mode;CHECK(dpu_core_perf_crtc_check(&crtc,&state.base)==-EINVAL);}
 kms.perf.perf_tune.mode=0;kms.aa569_spr=NULL;
 for(int mode=0;mode<=2;mode++){
  kms.perf.perf_tune.mode=mode;
  CHECK(!dpu_core_perf_crtc_check(&crtc,&state.base));struct dpu_core_perf_params current=state.new_perf;
  CHECK(!baseline_check(&crtc,&state.base));CHECK(!memcmp(&current,&state.new_perf,sizeof(current)));
 }
 kms.perf.perf_tune.mode=0;kms.aa569_spr=&kms;
 CHECK(!dpu_core_perf_crtc_check(&crtc,&state.base));aggregate=state.new_perf;
 for(int fail=0;fail<2;fail++){
  icc_calls=0;icc_error_at=fail;CHECK(_dpu_core_perf_crtc_update_bus(&kms,&crtc)==-5);CHECK(icc_calls==(unsigned)fail+1);
 }
 icc_calls=0;icc_error_at=-1;CHECK(!_dpu_core_perf_crtc_update_bus(&kms,&crtc));
 CHECK(icc_calls==2&&icc_avg[0]==1778824&&icc_avg[1]==1778824&&icc_peak[0]==3557648);
 kms.num_paths=0;CHECK(_dpu_core_perf_crtc_update_bus(&kms,&crtc)==-ENODEV);
 kms.aa569_spr=NULL;CHECK(!_dpu_core_perf_crtc_update_bus(&kms,&crtc));kms.num_paths=2;
 for(int mode=0;mode<=2;mode++){
  kms.perf.perf_tune.mode=mode;icc_calls=0;icc_error_at=0;CHECK(!_dpu_core_perf_crtc_update_bus(&kms,&crtc));
  u32 a=icc_avg[0],p=icc_peak[0];icc_calls=0;CHECK(!baseline_bus(&kms,&crtc));CHECK(a==icc_avg[0]&&p==icc_peak[0]);
 }
 /* Actual compiled-DTB ceiling, not the old 1GHz success fixture. */
 kms.aa569_spr=&kms;kms.perf.max_opp_clk_rate=ACTUAL_MDP_OPP;
 kms.perf.max_core_clk_rate=1000000000;kms.perf.perf_tune.mode=0;
 CHECK(!dpu_core_perf_crtc_check(&crtc,&state.base));
 for(unsigned mixers=0;mixers<4;mixers++){
  if(mixers!=2){state.base.num_mixers=mixers;CHECK(dpu_core_perf_crtc_check(&crtc,&state.base)==-EINVAL);}
 }
 state.base.num_mixers=2;
 pstate.base.src=(struct drm_rect){0,0,1440<<16,3168<<16};pstate.base.dst=(struct drm_rect){0,0,1440,3168};
 kms.aa569_spr=NULL;
 CHECK(!dpu_plane_split(&plane,&pstate.base,&state.base));
 CHECK(drm_rect_width(&pstate.pipe_cfg[0].src_rect)==1440&&!drm_rect_width(&pstate.pipe_cfg[1].src_rect));
 /* Replay old full-width requirement against the actual 575MHz OPP. */
 struct dpu_core_perf_params old_full={.core_clk_rate=580608000};
 CHECK(dpu_aa569_spr_budget(&old_full,ACTUAL_MDP_OPP)==-E2BIG);
 kms.aa569_spr=&kms;
 CHECK(!dpu_plane_split(&plane,&pstate.base,&state.base));
 CHECK(drm_rect_width(&pstate.pipe_cfg[0].src_rect)==720&&drm_rect_width(&pstate.pipe_cfg[1].src_rect)==720);
 CHECK(!dpu_core_perf_crtc_check(&crtc,&state.base));
 CHECK(state.new_perf.core_clk_rate==355764706&&state.new_perf.bw_ctl==3557647059ULL);
 CHECK(state.new_perf.core_clk_rate<=ACTUAL_MDP_OPP);
 u64 limit=dpu_aa569_spr_max_pipe_rate(&kms.perf);
 struct dpu_core_perf_params boundary={.core_clk_rate=(limit*105+99)/100};
 CHECK(!dpu_aa569_spr_budget(&boundary,ACTUAL_MDP_OPP));
 boundary=(struct dpu_core_perf_params){.core_clk_rate=((limit+1)*105+99)/100};
 CHECK(dpu_aa569_spr_budget(&boundary,ACTUAL_MDP_OPP)==-E2BIG);
 for(unsigned factor=0;factor<=200;factor++){
  cfg.clk_inefficiency_factor=factor;
  for(unsigned j=1;j<=17;j++){
   kms.perf.max_opp_clk_rate=27100003ULL*j;
   u64 raw=dpu_aa569_spr_max_pipe_rate(&kms.perf),f=factor?factor:100;
   boundary=(struct dpu_core_perf_params){.core_clk_rate=(raw*f+99)/100};
   CHECK(!dpu_aa569_spr_budget(&boundary,kms.perf.max_opp_clk_rate));
   boundary=(struct dpu_core_perf_params){.core_clk_rate=((raw+1)*f+99)/100};
   CHECK(dpu_aa569_spr_budget(&boundary,kms.perf.max_opp_clk_rate)==-E2BIG);
  }
 }
 cfg.clk_inefficiency_factor=105;
 kms.perf.max_opp_clk_rate=UINT64_MAX;CHECK(!dpu_aa569_spr_max_pipe_rate(&kms.perf));
 CHECK(!dpu_aa569_spr_max_pipe_rate(NULL));
 kms.perf.max_opp_clk_rate=0;CHECK(dpu_plane_split(&plane,&pstate.base,&state.base)==-EINVAL);
 kms.perf.max_opp_clk_rate=ACTUAL_MDP_OPP;
 state.base.num_mixers=1;CHECK(dpu_plane_split(&plane,&pstate.base,&state.base)==-EINVAL);
 puts("AA569 budget, actual-DTB OPP, real plane split and legacy controls passed");
 return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='oneplus13-budget-') as tmp:
        c, exe = Path(tmp)/'budget.c', Path(tmp)/'budget.exe'
        c.write_text('#define ACTUAL_MDP_OPP '+str(max(rates))+'ULL\n'+prefix+functions+fixture+suffix)
        command = ['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror']
        if args.sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command+[str(c), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    report = {'scope': 'HOST_C_DPU_COMMAND_BUDGET_ONLY', 'hardware_tested': False,
              'kernel_commit': pin, 'sanitizers': args.sanitize,
              'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'nominal_refresh_hz': 120, 'stock_budget_us': 6800,
              'ceiling_multiplier': [125, 102], 'arithmetic_vectors': len(cases),
              'overflow_peak_opp_zero_limit_rejected': 5,
              'fresh_enable_new_state_limits_checked': True, 'baseline_underbudget_rejected': True,
              'new_atomic_plane_geometry_checked': True,
              'original_plane_math_unchanged': True,
              'plane_source_sha256': hashlib.sha256(plane_source.encode()).hexdigest(),
              'nonoptin_legacy_modes_unchanged': 3, 'icc_error_positions': 2,
              'icc_per_path_average_ceiling': True, 'missing_icc_path_rejected': True,
              'te_timing_changed': False, 'hardware_transfer_timer_programmed': False,
              'exact_vendor_hal_algorithm': False}
    report.update(actual_native_dtb_sha256=hashlib.sha256(args.native_dtb.read_bytes()).hexdigest(),
                  actual_mdp_opp_max_hz=max(rates),actual_dtb_opp_mode_eligible=True,
                  fixed_two_mixer_clock_hz=355764706,full_width_old_budget_rejected=True,
                  actual_plane_split_executed=True,pipe_limit_rounding_boundaries=3417,
                  native_dtb_negative_controls=6,plane_geometry_fixture='NO_ROTATION_SCALING_OR_CLIPPING')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
