#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute the real static SPR runtime with host MMIO, PM and firmware substitutes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
from spr import encode_profile, parse_xml, register_plan


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source', required=True, type=Path)
    p.add_argument('--patched-source', type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--sanitize', action='store_true')
    p.add_argument('--cc', nargs='+', default=['gcc'])
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    pin = json.loads((repo/'devices/oneplus13/source.json').read_text())['kernel']['commit']
    if subprocess.check_output(['git', '-C', str(a.kernel_source), 'rev-parse', 'HEAD'], text=True).strip() != pin:
        raise ValueError('Kernel pin mismatch')
    tree = a.patched_source or a.kernel_source
    prefix = tree/'drivers/gpu/drm/msm/disp/dpu1'
    component = repo/'devices/oneplus13/kernel/spr-aa569'
    spec = importlib.util.spec_from_file_location('display_check', Path(__file__).with_name('check-display.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extract = module.extract_function
    core = (component/'dpu_aa569_spr.c').read_text()
    runtime = (component/'dpu_aa569_spr_runtime.c').read_text()
    header = (component/'dpu_aa569_spr.h').read_text()
    encoder = (prefix/'dpu_encoder.c').read_text()
    catalog = (prefix/'dpu_hw_catalog.c').read_text()
    dspps = (prefix/'catalog/dpu_12_0_sm8750.h').read_text()
    ctl = (prefix/'dpu_hw_ctl.c').read_text()
    mdss = (tree/'drivers/gpu/drm/msm/msm_mdss.c').read_text()
    def array(source, start):
        pos = source.index(start)
        return source[pos:source.index('\n};', pos)+3]
    scalar = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint32_t u32;
#define BIT(n) (UINT32_C(1)<<(n))
#define GENMASK(h,l) ((UINT32_MAX>>(31-(h)))&(UINT32_MAX<<(l)))
#define READ_ONCE(x) (x)
#define atomic_read(p) (*(p))
#define ENOENT 2
#define EINVAL 22
#define EBUSY 16
#define EHOSTDOWN 112
#ifndef EBADMSG
#define EBADMSG 74
#endif
#define GFP_KERNEL 0
#define CHECK(x) do {if(!(x)){fprintf(stderr,"SPR runtime check failed %d\n",__LINE__);exit(1);}}while(0)
static u32 get_unaligned_le32(const void *p){const u8*b=p;return (u32)b[0]|((u32)b[1]<<8)|((u32)b[2]<<16)|((u32)b[3]<<24);}
static u32 crc32_le(u32 c,const u8*p,size_t n){while(n--){c^=*p++;for(int i=0;i<8;i++)c=(c>>1)^((c&1)?0xedb88320U:0);}return c;}
'''
    declarations = r'''
struct dpu_pp_blk {const char *name;u32 base,len,version;};
struct dpu_dspp_sub_blks {struct dpu_pp_blk pcc,gc,spr;};
struct dpu_dspp_cfg {const char*name;int id;u32 base,len;const struct dpu_dspp_sub_blks*sblk;};
struct dpu_mdss_cfg {unsigned int dspp_count;const struct dpu_dspp_cfg*dspp;};
enum dpu_dspp {DSPP_NONE,DSPP_0,DSPP_1,DSPP_2,DSPP_3,DSPP_MAX};
#define DPU_DSPP_PCC 1
#define DPU_DSPP_GC 2
#define DPU_DSPP_SPR 3
#define DSPP_IDX 29
struct dpu_hw_ctl {u32 pending_dspp_flush_mask[4],pending_flush_mask;};
struct drm_dsc_config {bool native_422,native_420,convert_rgb;int dsc_version_major,dsc_version_minor,bits_per_component,bits_per_pixel,pic_width,pic_height,slice_width,slice_height;};
struct drm_encoder {bool enabled;unsigned long frame_busy_mask[1];int frame_done_timeout_cnt;
 struct {int intf_type;bool is_cmd_mode;int num_of_h_tiles,h_tile_instance[2];}disp_info;
 struct drm_dsc_config*dsc;};
#define dpu_encoder_virt drm_encoder
#define to_dpu_encoder_virt(e) (e)
#define INTF_DSI 1
#define INTF_DP 2
static struct drm_dsc_config*dpu_encoder_get_dsc_config(struct drm_encoder*e){return e->dsc;}
struct drm_device {struct drm_encoder*enc[2];int n;void*dev_private;};
#define drm_for_each_encoder(e,d) for(int idx=0;idx<(d)->n&&((e)=(d)->enc[idx],1);idx++)
#define drm_for_each_encoder_mask(e,d,m) drm_for_each_encoder(e,d) if((m)&BIT(idx))
struct device_node {bool optin,target,reset;};
struct device {struct device_node*of_node;struct device*parent;};
struct platform_device {struct device dev;};
struct aa569_spr_runtime;
struct dpu_kms {struct aa569_spr_runtime*aa569_spr;bool aa569_spr_clocks_on;
 struct dpu_mdss_cfg*catalog;void*mmio;struct drm_device*dev;struct platform_device*pdev;};
struct msm_drm_private {struct dpu_kms*kms;};
#define to_dpu_kms(p) (p)
struct drm_display_mode {int hdisplay,vdisplay,htotal,vtotal,clock;u32 flags;};
struct drm_crtc {struct drm_device*dev;};
struct drm_crtc_state {bool enable,self_refresh_active;struct drm_display_mode adjusted_mode;u32 encoder_mask;};
static u32 mmio[0x80000/4],offsets[256],values[256],writes,reads,barriers;
static u32 readl(const void*p){ptrdiff_t off=(const u8*)p-(const u8*)mmio;CHECK(off>=0&&(size_t)off<sizeof(mmio)&&!(off&3));reads++;return mmio[off/4];}
static void writel(u32 v,void*p){ptrdiff_t off=(u8*)p-(u8*)mmio;CHECK(off>=0&&(size_t)off<sizeof(mmio)&&!(off&3)&&writes<256);offsets[writes]=off;values[writes++]=v;mmio[off/4]=v;}
static void wmb(void){barriers++;}
static bool machine=true,root_present=true,root_compiled=false;
static struct device_node root_node;
static const char *root_model="Qualcomm Technologies, Inc. Sun MTP,dodge",*root_chassis="handset";
static struct device_node*of_find_node_by_path(const char*s){CHECK(!strcmp(s,"/"));return root_present?&root_node:NULL;}
static void of_node_put(struct device_node*n){CHECK(n==&root_node);}
static int of_property_read_string(struct device_node*n,const char*s,const char**out){CHECK(n==&root_node);if(!strcmp(s,"model"))*out=root_model;else {CHECK(!strcmp(s,"chassis-type"));*out=root_chassis;}return *out?0:-EINVAL;}
static bool of_device_is_compatible(struct device_node*n,const char*s){if(n==&root_node){if(!strcmp(s,"qcom,sun"))return machine&&!root_compiled;CHECK(!strcmp(s,"oneplus,dodge")||!strcmp(s,"qcom,sm8750"));return machine&&root_compiled;}CHECK(!strcmp(s,"qcom,sm8750-dpu"));return n->target;}
static bool of_property_read_bool(struct device_node*n,const char*s){if(!strcmp(s,"resets"))return n->reset;CHECK(!strcmp(s,"oneplus,aa569-static-spr"));return n->optin;}
static int dev_err_probe(struct device*d,int ret,const char*fmt){(void)d;(void)fmt;return ret;}
struct firmware {const u8*data;size_t size;};
static struct firmware fw;static int fw_error,requests,releases;static bool alloc_fail;
static int request_firmware_direct(const struct firmware**out,const char*path,struct device*d){(void)d;CHECK(!strcmp(path,"qcom/sm8750/oneplus/dodge/aa569-spr-v1.bin"));requests++;if(fw_error)return fw_error;*out=&fw;return 0;}
static void release_firmware(const struct firmware*f){CHECK(f==&fw);releases++;}
static void*drmm_kzalloc(struct drm_device*d,size_t n,int flags){(void)d;(void)flags;return alloc_fail?NULL:calloc(1,n);}
struct reset_control {int unused;};static struct reset_control reset_object;
static int reset_mode,assert_error,deassert_error,asserts,deasserts,sleeps,puts_reset;
#define IS_ERR(p) ((intptr_t)(p)<0&&(intptr_t)(p)>-4096)
#define PTR_ERR(p) ((int)(intptr_t)(p))
static struct reset_control*reset_control_get_optional_exclusive(struct device*d,const char*s){(void)d;(void)s;return reset_mode==0?NULL:reset_mode==1?(void*)(intptr_t)-517:&reset_object;}
static int reset_control_assert(struct reset_control*r){CHECK(r==&reset_object);asserts++;return assert_error;}
static int reset_control_deassert(struct reset_control*r){CHECK(r==&reset_object);deasserts++;return deassert_error;}
static void reset_control_put(struct reset_control*r){CHECK(r==&reset_object);puts_reset++;}
static void msleep(int ms){CHECK(ms==20);sleeps++;}
'''
    strip = lambda s: re.sub(r'^#include[^\n]*\n', '', s, flags=re.M)
    synthetic = (repo/'devices/oneplus13/test-fixtures/spr-synthetic.xml').read_bytes()
    blob = encode_profile(synthetic)
    default, modes = parse_xml(synthetic)
    expected = []
    for op in register_plan(modes[default]):
        expected.extend((op['relative_offset']+4*i, v) for i, v in enumerate(op['values']))
    fixture = 'static u8 profile_data[]={'+','.join(str(x) for x in blob)+'};\n'
    fixture += 'static const u32 expected[][2]={'+','.join('{%d,%d}'%v for v in expected)+'};\n'
    functions = '\n'.join([extract(encoder,'bool dpu_encoder_spr_quiescent('),
        extract(encoder,'bool dpu_encoder_aa569_format('),
        extract(ctl,'static void dpu_hw_ctl_update_pending_flush_dspp_sub_blocks('),
        extract(mdss,'static int msm_mdss_reset(')])
    suffix = r'''
int main(void){
 struct device_node parent_node={.reset=true},node={.target=true};
 struct device parent={.of_node=&parent_node};struct platform_device pdev={.dev={.of_node=&node,.parent=&parent}};
 struct drm_encoder enc={.disp_info={INTF_DSI,true,1,{0,0}}},other={0};
 struct drm_dsc_config dsc={true,false,false,1,2,10,128,1440,3168,720,22};enc.dsc=&dsc;
 struct drm_device dev={.enc={&enc,&other},.n=2};
 struct dpu_mdss_cfg catalog={4,sm8750_dspp};
 struct dpu_kms kms={.catalog=&catalog,.mmio=mmio,.dev=&dev,.pdev=&pdev};
 struct msm_drm_private priv={&kms};dev.dev_private=&priv;
 fw.data=profile_data;fw.size=sizeof(profile_data);
 CHECK(!dpu_aa569_spr_load(&kms)&&!requests&&!reads&&!writes&&!kms.aa569_spr);
 node.optin=true;machine=false;CHECK(dpu_aa569_spr_load(&kms)==-EINVAL&&!requests);machine=true;
 root_present=false;CHECK(dpu_aa569_spr_load(&kms)==-EINVAL&&!requests);root_present=true;
 root_model="Qualcomm Technologies, Inc. Sun MTP";CHECK(dpu_aa569_spr_load(&kms)==-EINVAL&&!requests);
 root_model="OnePlus Pad 2 Pro";CHECK(dpu_aa569_spr_load(&kms)==-EINVAL&&!requests);
 root_model="OnePlus 13 (AA569 native display experimental candidate)";root_compiled=true;
 CHECK(aa569_spr_machine());root_compiled=false;CHECK(!aa569_spr_machine());
 root_model="Qualcomm Technologies, Inc. Sun MTP,dodge";
 root_chassis="tablet";CHECK(dpu_aa569_spr_load(&kms)==-EINVAL&&!requests);root_chassis="handset";
 node.target=false;CHECK(dpu_aa569_spr_load(&kms)==-EINVAL&&!requests);node.target=true;
 parent_node.reset=false;CHECK(dpu_aa569_spr_load(&kms)==-EINVAL&&!requests);parent_node.reset=true;
 fw_error=-ENOENT;CHECK(dpu_aa569_spr_load(&kms)==-ENOENT&&!kms.aa569_spr);fw_error=0;
 profile_data[0]^=1;CHECK(dpu_aa569_spr_load(&kms)==-EINVAL&&!kms.aa569_spr);profile_data[0]^=1;
 alloc_fail=true;CHECK(dpu_aa569_spr_load(&kms)==-ENOMEM&&!kms.aa569_spr);alloc_fail=false;
 CHECK(!dpu_aa569_spr_load(&kms)&&kms.aa569_spr&&!writes&&!reads);
 CHECK(dpu_aa569_spr_restore(&kms)==-EHOSTDOWN&&!writes&&!reads);
 kms.aa569_spr_clocks_on=true;
 other.frame_busy_mask[0]=1;CHECK(dpu_aa569_spr_restore(&kms)==-EBUSY&&!writes&&!reads);other.frame_busy_mask[0]=0;
 other.frame_done_timeout_cnt=1;CHECK(dpu_aa569_spr_restore(&kms)==-EBUSY&&!writes&&!reads);other.frame_done_timeout_cnt=0;
 other.enabled=true;other.disp_info.intf_type=INTF_DP;CHECK(dpu_aa569_spr_restore(&kms)==-EBUSY&&!writes);other.enabled=false;
 mmio[(0x5a000+0x12400+4)/4]=BIT(25);
 CHECK(dpu_aa569_spr_restore(&kms)==-EBUSY&&!writes&&!kms.aa569_spr->enabled);
 mmio[(0x5a000+0x12400+4)/4]=0;
 const u32 physical[]={0xae6a400,0xae6b400,0xae6c400,0xae6d400};
 for(int cycle=0;cycle<3;cycle++) {
  writes=reads=barriers=0;CHECK(!dpu_aa569_spr_restore(&kms)&&kms.aa569_spr->enabled);
  CHECK(writes==4*sizeof(expected)/sizeof(expected[0])&&barriers==1);
  for(u32 pipe=0;pipe<4;pipe++)for(u32 j=0;j<sizeof(expected)/sizeof(expected[0]);j++) {
   u32 k=pipe*sizeof(expected)/sizeof(expected[0])+j;
   CHECK(offsets[k]+0xae01000==physical[pipe]+expected[j][0]);CHECK(values[k]==expected[j][1]);
  }
  writes=0;CHECK(!dpu_aa569_spr_suspend(&kms)&&!kms.aa569_spr->enabled&&writes==8);
  for(u32 i=0;i<8;i++)CHECK(!values[i]&&offsets[i]+0xae01000==physical[i/2]+4+4*(i%2));
  kms.aa569_spr_clocks_on=false;writes=reads=0;CHECK(dpu_aa569_spr_restore(&kms)==-EHOSTDOWN&&!reads&&!writes);
  memset(mmio,0,sizeof(mmio));kms.aa569_spr_clocks_on=true;
 }
 struct drm_crtc crtc={&dev};struct drm_crtc_state state={true,false,{1440,3168,1562,3200,599808,0},1};
 CHECK(!dpu_aa569_spr_check(&crtc,&state));
 state.self_refresh_active=true;CHECK(dpu_aa569_spr_check(&crtc,&state)==-EINVAL);state.self_refresh_active=false;
 state.adjusted_mode.flags=1;CHECK(dpu_aa569_spr_check(&crtc,&state)==-EINVAL);state.adjusted_mode.flags=0;
 state.adjusted_mode.clock--;CHECK(dpu_aa569_spr_check(&crtc,&state)==-EINVAL);state.adjusted_mode.clock++;
 state.encoder_mask=3;CHECK(dpu_aa569_spr_check(&crtc,&state)==-EINVAL);state.encoder_mask=0;CHECK(dpu_aa569_spr_check(&crtc,&state)==-EINVAL);state.encoder_mask=1;
 enc.disp_info.h_tile_instance[0]=1;CHECK(dpu_aa569_spr_check(&crtc,&state)==-EINVAL);enc.disp_info.h_tile_instance[0]=0;
 enc.disp_info.is_cmd_mode=false;CHECK(dpu_aa569_spr_check(&crtc,&state)==-EINVAL);enc.disp_info.is_cmd_mode=true;
 dsc.bits_per_pixel=256;CHECK(dpu_aa569_spr_check(&crtc,&state)==-EINVAL);dsc.bits_per_pixel=128;
 dsc.native_422=false;CHECK(dpu_aa569_spr_check(&crtc,&state)==-EINVAL);dsc.native_422=true;
 CHECK(!dpu_aa569_spr_check(&crtc,&state));
 struct dpu_hw_ctl ctl={0};
 dpu_hw_ctl_update_pending_flush_dspp_sub_blocks(&ctl,DSPP_0,DPU_DSPP_SPR);
 CHECK(ctl.pending_dspp_flush_mask[0]==BIT(8)&&ctl.pending_flush_mask==BIT(29));
 struct dpu_hw_ctl unchanged=ctl;dpu_hw_ctl_update_pending_flush_dspp_sub_blocks(&ctl,0,DPU_DSPP_SPR);
 CHECK(!memcmp(&ctl,&unchanged,sizeof(ctl)));dpu_hw_ctl_update_pending_flush_dspp_sub_blocks(&ctl,DSPP_MAX,DPU_DSPP_SPR);CHECK(!memcmp(&ctl,&unchanged,sizeof(ctl)));
 CHECK(!msm_mdss_reset(&pdev.dev)&&!asserts&&!puts_reset);reset_mode=1;CHECK(msm_mdss_reset(&pdev.dev)==-517&&!asserts);reset_mode=2;
 assert_error=-5;CHECK(msm_mdss_reset(&pdev.dev)==-5&&asserts==1&&!deasserts&&!sleeps&&puts_reset==1);assert_error=0;
 deassert_error=-6;CHECK(msm_mdss_reset(&pdev.dev)==-6&&deasserts==1&&sleeps==1&&puts_reset==2);deassert_error=0;
 CHECK(!msm_mdss_reset(&pdev.dev)&&deasserts==2&&sleeps==2&&puts_reset==3);
 free(kms.aa569_spr);kms.aa569_spr=NULL;writes=reads=0;
 CHECK(!dpu_aa569_spr_restore(&kms)&&!dpu_aa569_spr_suspend(&kms)&&!reads&&!writes);
 CHECK(!dpu_aa569_spr_check(&crtc,&state));
 puts("SPR all-pipe preflight, physical addresses, three PM cycles, format/flush/reset guards passed");
 return 0;
}
'''
    combined = '\n'.join([scalar, strip(header), strip(core), declarations,
        array(catalog,'static const struct dpu_dspp_sub_blks sm8750_aa569_dspp_sblk[]'),
        array(dspps,'static const struct dpu_dspp_cfg sm8750_dspp[]'),
        functions, strip(runtime), fixture, suffix])
    with tempfile.TemporaryDirectory(prefix='oneplus13-spr-runtime-') as temp:
        source, exe = Path(temp)/'runtime.c', Path(temp)/'runtime.exe'
        source.write_text(combined)
        command = a.cc + ['-std=gnu11', '-Wall', '-Wextra', '-Werror']
        if a.sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command+[str(source), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    kms = (prefix/'dpu_kms.c').read_text()
    crtc = (prefix/'dpu_crtc.c').read_text()
    resume = extract(kms,'static int __maybe_unused dpu_runtime_resume(')
    suspend = extract(kms,'static int __maybe_unused dpu_runtime_suspend(')
    if not (resume.index('clk_bulk_prepare_enable(') < resume.index('dpu_aa569_spr_restore(') < resume.index('dpu_encoder_virt_runtime_resume(')):
        raise ValueError('SPR restore ordering is not before encoder restore')
    if suspend.index('dpu_aa569_spr_suspend(') > suspend.index('clk_bulk_disable_unprepare('):
        raise ValueError('SPR suspend must precede clocks off')
    if '|| dpu_kms->aa569_spr' not in extract(crtc,'static struct msm_display_topology dpu_crtc_get_topology('):
        raise ValueError('SPR resource ownership must not depend on CTM/gamma')
    report = {'scope': 'HOST_C_STATIC_SPR_RUNTIME_ONLY', 'hardware_tested': False,
              'kernel_commit': pin, 'sanitizers': a.sanitize,
              'backend': 'QUIESCENT_FIXED_PROFILE_MMIO_NO_DMA',
              'all_pipe_udc_preflight_before_any_write': True, 'powered_idle_guards': True,
              'physical_spr_addresses': ['0xae6a400','0xae6b400','0xae6c400','0xae6d400'],
              'profile_missing_or_invalid_rejected': True, 'optin_absent_noop': True,
              'abl_rewritten_and_compiled_phone_identity_checked': True,
              'pm_restore_cycles': 3, 'aa569_format_exclusivity_checked': True,
              'spr_flush_bit': 8, 'reset_errors_propagated': True,
              'restore_before_encoder_restore': True, 'disable_before_clocks_off': True,
              'resource_reservation_without_color_properties': True,
              'dma_buffers_or_submissions': False,
              'runtime_source_sha256': hashlib.sha256(runtime.encode()).hexdigest()}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
