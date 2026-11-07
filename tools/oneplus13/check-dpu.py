#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile real DPU functions with captured registers and a vendor math oracle."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from dsc import c_initializer, decode_pps
from panel import vendor_pps


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source', required=True, type=Path)
    p.add_argument('--patched-source', type=Path, help='Patched kernel tree; defaults to kernel-source')
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--keep-source', type=Path)
    p.add_argument('--sanitize', action='store_true')
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    pin = json.loads((repo/'devices/oneplus13/source.json').read_text())['kernel']['commit']
    if subprocess.check_output(['git','-C',str(a.kernel_source),'rev-parse','HEAD'],text=True).strip() != pin:
        raise ValueError('Kernel reference differs from pin')
    def read(name):
        return subprocess.check_output(['git','-c','gc.auto=0','-c','maintenance.auto=false',
            '-C',str(a.kernel_source),'show',pin+':'+name]).decode()
    spec = importlib.util.spec_from_file_location('display_check',Path(__file__).with_name('check-display.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extract = module.extract_function
    prefix = 'drivers/gpu/drm/msm/disp/dpu1/'
    old_encoder, old_dsc = read(prefix+'dpu_encoder.c'), read(prefix+'dpu_hw_dsc_1_2.c')
    patched = a.patched_source or a.kernel_source
    new_encoder = (patched/prefix/'dpu_encoder.c').read_text()
    new_dsc = (patched/prefix/'dpu_hw_dsc_1_2.c').read_text()
    pp = (patched/prefix/'dpu_hw_pingpong.c').read_text()
    line_sig = 'static u32\ndpu_encoder_dsc_initial_line_calc_1_2('
    config_sig = 'static void dpu_hw_dsc_config_1_2('
    baseline = extract(old_encoder,line_sig).replace('dpu_encoder_dsc_initial_line_calc_1_2','baseline_lines')
    baseline += extract(old_dsc,config_sig).replace('dpu_hw_dsc_config_1_2','baseline_config')
    definitions = new_dsc[new_dsc.index('#define DSC_CMN_MAIN_CNF'):new_dsc.index('static int _dsc_calc_output_buf_max_addr')]
    functions = '\n'.join([extract(new_encoder,line_sig),
        extract(new_dsc,'static int _dsc_calc_output_buf_max_addr('),
        extract(new_dsc,config_sig),extract(pp,'static void dpu_hw_pp_setup_ppb_fifo_size('),
        extract(new_encoder,'static void dpu_encoder_update_ppb_size(')])
    vendor = (repo/'devices/oneplus13/test-fixtures/vendor-dce-initial-lines.c').read_text()
    vendor_func = extract(vendor,'static int _dce_dsc_initial_line_calc(')
    if hashlib.sha256(vendor_func.encode()).hexdigest() != 'dd0685e60d46c5460b8992da214c213905a84a389d702fb53e3feb7932b42223':
        raise ValueError('Vendor reference function was changed')
    source = read('drivers/gpu/drm/display/drm_dsc_helper.c')
    helpers = '\n'.join(extract(source,sig) for sig in ['u32 drm_dsc_get_bpp_int(', 'u32 drm_dsc_flatness_det_thresh('])
    header = read('include/drm/display/drm_dsc.h').replace('#include <drm/display/drm_dp.h>','')
    fixture = json.loads((repo/'devices/oneplus13/panel-aa569-120hz.json').read_text())
    cfg = decode_pps(vendor_pps(fixture['on_packets']))
    declarations = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint16_t __be16;
#define __packed __attribute__((packed))
struct dp_sdp_header {u8 bytes[4];};
#define BIT(n) (1U<<(n))
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define mult_frac(n,a,b) ((n)*(a)/(b))
static int host_warn(int condition) {return condition;}
#define WARN_ON_ONCE(c) host_warn(!!(c))
#define DSC_MODE_SPLIT_PANEL BIT(0)
#define DSC_MODE_MULTIPLEX BIT(1)
#define DSC_MODE_VIDEO BIT(2)
#define DPU_DSC_NATIVE_42x_EN 1
#define DPU_DSC_FULL_ICH_PREC 2
#define MAX_CHANNELS_PER_ENC 4
#define PP_PPB_FIFO_SIZE 0x1c
struct dpu_hw_blk_reg_map {u32 reg[1024], offset[128], value[128], count;};
static void capture(struct dpu_hw_blk_reg_map *hw,u32 off,u32 val) {
 if((off&3)||off>=4096||hw->count>=128)abort();
 hw->reg[off/4]=val;hw->offset[hw->count]=off;hw->value[hw->count++]=val;
}
#define DPU_REG_WRITE(hw,off,val) capture(hw,off,val)
struct test_block {u32 base,len;};
struct dpu_dsc_sub_blks {struct test_block enc,ctl;};
struct dpu_dsc_cfg {unsigned long features;const struct dpu_dsc_sub_blks *sblk;};
struct dpu_hw_dsc {struct dpu_hw_blk_reg_map hw;const struct dpu_dsc_cfg *caps;};
struct dpu_hw_pingpong;
struct test_pp_ops {void (*setup_ppb_fifo_size)(struct dpu_hw_pingpong *,u32);};
struct dpu_hw_pingpong {struct dpu_hw_blk_reg_map hw;struct test_pp_ops ops;};
struct dpu_encoder_virt {struct dpu_hw_pingpong *hw_pp[MAX_CHANNELS_PER_ENC];};
struct drm_display_mode {u32 hdisplay;};
#define DSC_BPP(c) ((c).bits_per_pixel>>4)
'''
    vendor_struct = 'struct msm_display_dsc_info {struct drm_dsc_config config;int num_active_ss_per_enc,initial_lines;};\n'
    suffix = r'''
#define CHECK(c) do {if(!(c)){fprintf(stderr,"failed line %d\n",__LINE__);return 1;}}while(0)
int main(void) {
 unsigned int native_cases=0,rgb_cases=0;
 for(int native=0;native<2;native++)for(int bpc=8;bpc<=10;bpc+=2)
 for(int bpp=4;bpp<=10;bpp+=3)for(int active=1;active<=2;active++)
 for(int mode=0;mode<4;mode++) {
  struct drm_dsc_config c=aa569_dsc;c.native_422=native;c.bits_per_component=bpc;
  c.bits_per_pixel=bpp<<4;c.slice_chunk_size=c.slice_width*bpp/8;
  struct msm_display_dsc_info v={.config=c,.num_active_ss_per_enc=active};
  CHECK(!_dce_dsc_initial_line_calc(&v,720*active,mode));
  CHECK(dpu_encoder_dsc_initial_line_calc_1_2(&c,720*active,mode)==(u32)v.initial_lines);
  if(native)native_cases++;else {
   CHECK(baseline_lines(&c,720*active,mode)==(u32)v.initial_lines);rgb_cases++;
  }
 }
 CHECK(native_cases==48&&rgb_cases==48);
 struct drm_dsc_config c=aa569_dsc;
 CHECK(dpu_encoder_dsc_initial_line_calc_1_2(&c,720,3)==4);
 CHECK(baseline_lines(&c,720,3)==3);
 struct dpu_dsc_sub_blks blocks={.enc={0x100,0x100},.ctl={0xf00,0x24}};
 struct dpu_dsc_cfg caps={.features=BIT(DPU_DSC_NATIVE_42x_EN)|BIT(DPU_DSC_FULL_ICH_PREC),.sblk=&blocks};
 struct dpu_hw_dsc patched={.caps=&caps},old={.caps=&caps};
 dpu_hw_dsc_config_1_2(&patched,&c,3,4);baseline_config(&old,&c,3,4);
 CHECK(patched.hw.reg[(0x100+ENC_DF_CTRL)/4]&BIT(12));
 CHECK(!(old.hw.reg[(0x100+ENC_DF_CTRL)/4]&BIT(12)));
 CHECK(!(patched.hw.reg[(0xf00+DSC_CFG)/4]&BIT(17)));
 CHECK(old.hw.reg[(0xf00+DSC_CFG)/4]&BIT(17));
 CHECK(((patched.hw.reg[(0x100+DSC_MAIN_CONF)/4]>>10)&0x3ff)==256);
 CHECK(patched.hw.count==old.hw.count);
 for(u32 i=0;i<patched.hw.count;i++) {
  CHECK(patched.hw.offset[i]==old.hw.offset[i]);
  u32 off=patched.hw.offset[i],diff=patched.hw.value[i]^old.hw.value[i];
  if(off==0x100+ENC_DF_CTRL)CHECK(diff==BIT(12));
  else if(off==0xf00+DSC_CFG)CHECK(diff==BIT(17));
  else CHECK(diff==0);
 }
 /* No precision capability means no invented precision bit on older cores. */
 caps.features=BIT(DPU_DSC_NATIVE_42x_EN);blocks.ctl.len=0x10;
 memset(&patched.hw,0,sizeof(patched.hw));dpu_hw_dsc_config_1_2(&patched,&c,3,4);
 CHECK(!(patched.hw.reg[(0x100+ENC_DF_CTRL)/4]&BIT(12)));
 CHECK(patched.hw.reg[(0xf00+DSC_CFG)/4]&BIT(17));
 for(u32 i=0;i<patched.hw.count;i++)CHECK(patched.hw.offset[i]<0xf10);
 /* Common RGB8bpc path is byte-identical to baseline. */
 blocks.ctl.len=0x24;caps.features=BIT(DPU_DSC_NATIVE_42x_EN)|BIT(DPU_DSC_FULL_ICH_PREC);
 c.native_422=false;c.bits_per_component=8;c.convert_rgb=true;
 memset(&patched.hw,0,sizeof(patched.hw));memset(&old.hw,0,sizeof(old.hw));
 dpu_hw_dsc_config_1_2(&patched,&c,3,3);baseline_config(&old,&c,3,3);
 CHECK(!memcmp(&patched.hw,&old.hw,sizeof(patched.hw)));
 /* FIFO sizes for two noncontiguous PP slots and a replay after PM loss. */
 struct dpu_hw_pingpong pp0={.ops={dpu_hw_pp_setup_ppb_fifo_size}},pp3={.ops={dpu_hw_pp_setup_ppb_fifo_size}};
 struct dpu_encoder_virt enc={.hw_pp={&pp0,NULL,NULL,&pp3}};
 struct drm_display_mode mode={.hdisplay=1440};
 dpu_encoder_update_ppb_size(&enc,&mode);
 CHECK(pp0.hw.reg[0x1c/4]==720&&pp3.hw.reg[0x1c/4]==720);
 memset(&pp0.hw,0,sizeof(pp0.hw));dpu_encoder_update_ppb_size(&enc,&mode);
 CHECK(pp0.hw.reg[0x1c/4]==720);
 enc.hw_pp[0]=NULL;enc.hw_pp[3]=NULL;dpu_encoder_update_ppb_size(&enc,&mode);
 puts("96 vendor/RGB math cases, register captures, legacy guard and PPB replay passed");
 return 0;
}
'''
    combined = '\n'.join([declarations, header, vendor_struct, helpers, definitions, vendor_func, functions, baseline, c_initializer(cfg), suffix])
    with tempfile.TemporaryDirectory(prefix='oneplus13-dpu-') as tmp:
        c, exe = Path(tmp)/'dpu.c',Path(tmp)/'dpu.exe'
        c.write_text(combined,encoding='utf-8')
        if a.keep_source:
            a.keep_source.write_text(combined,encoding='utf-8')
        command = ['gcc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter']
        if a.sanitize:
            command += ['-fsanitize=address,undefined','-fno-omit-frame-pointer']
        subprocess.run(command+[str(c),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    # Check actual integration/capability gates, not only isolated callbacks.
    init = extract(pp,'struct dpu_hw_pingpong *dpu_hw_pingpong_init(')
    if 'if (mdss_rev->core_major_ver >= 12)\n\t\tc->ops.setup_ppb_fifo_size' not in init:
        raise ValueError('Missing PPB hardware-generation guard')
    enable = extract(new_encoder,'static void _dpu_encoder_virt_enable_helper(')
    resume = extract(new_encoder,'void dpu_encoder_virt_runtime_resume(')
    if 'dpu_encoder_update_ppb_size(' not in enable or '_dpu_encoder_virt_enable_helper(' not in resume:
        raise ValueError('PPB restore integration missing')
    report = {'scope':'HOST_C_DPU_BUFFER_AND_REGISTER_ONLY','hardware_tested':False,
              'kernel_commit':pin,'sanitizers':a.sanitize,
              'native422_vendor_math_cases':48,'rgb_baseline_math_cases':48,
              'aa569_initial_lines':4,'baseline_initial_lines':3,
              'physical_bpp_q4':128,'hardware_bpp_field_q4':256,
              'full_ich_precision_capability_checked':True,
              'modern_native422_wrapper_bit17_cleared':True,
              'legacy_ctl_bounds_checked':True,'rgb8_register_baseline_match':True,
              'ppb_fifo_entries_per_720px_pipe':720,'ppb_pm_restore_call_present':True,
              'vendor_function_sha256':hashlib.sha256(vendor_func.encode()).hexdigest(),
              'dpu_functions_sha256':hashlib.sha256(functions.encode()).hexdigest()}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
