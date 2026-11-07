#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the actual isolated C-PHY setup functions without hardware."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path

PREFIX=r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t s64;
#define U64_MAX UINT64_MAX
#define NSEC_PER_SEC 1000000000ULL
#define DIV_ROUND_CLOSEST_ULL(x,d) (((x)+(d)/2)/(d))
#define do_div(x,d) ((x)/=(d))
#define CSI2_MAX_DATA_LANES 4
#define CSI2_MAX_DATA_TRIOS 3
#define MSM_CSIPHY_PAD_SINK 0
enum phy_mode { PHY_MODE_INVALID, PHY_MODE_MIPI_DPHY, PHY_MODE_MIPI_CPHY };
enum v4l2_mbus_type { V4L2_MBUS_UNKNOWN, V4L2_MBUS_CSI2_DPHY, V4L2_MBUS_CSI2_CPHY };
struct phy_configure_opts_mipi_dphy { unsigned int lanes,configured;u64 hs_clk_rate; };
struct phy_configure_opts_mipi_cphy { u64 symbol_rate;unsigned int trios;u32 t_prepare_ns,t_preamble_ns; };
union phy_configure_opts { struct phy_configure_opts_mipi_dphy mipi_dphy;struct phy_configure_opts_mipi_cphy mipi_cphy; };
struct mipi_csi2phy_stream_cfg { s64 link_freq;u8 num_data_lanes;u32 t_prepare_ns,t_preamble_ns; };
struct mipi_csi2phy_device_regs { const void *cphy;unsigned int num_cphy; };
struct mipi_csi2phy_device;
struct mipi_csi2phy_hw_ops { int (*validate)(struct mipi_csi2phy_device *,struct mipi_csi2phy_stream_cfg *); };
struct mipi_csi2phy_soc_cfg { struct mipi_csi2phy_device_regs reg_info;const struct mipi_csi2phy_hw_ops *ops; };
struct mipi_csi2phy_device { enum phy_mode phy_mode;struct mipi_csi2phy_stream_cfg stream_cfg;const struct mipi_csi2phy_soc_cfg *soc_cfg;int configured; };
struct phy { struct mipi_csi2phy_device *data; };
static struct mipi_csi2phy_device *phy_get_drvdata(struct phy *phy) { return phy->data; }
struct v4l2_mbus_config { enum v4l2_mbus_type type;u64 link_freq;struct { struct { unsigned int num_data_lanes;u32 t_prepare_ns,t_preamble_ns; } mipi_csi2; } bus; };
struct csiphy_lanes_cfg { unsigned int num_data;enum v4l2_mbus_type phy_type; };
struct csi2_config { struct csiphy_lanes_cfg lane_cfg; };
struct format_info { const void *formats;unsigned int nformats; };
struct resource { struct format_info *formats; };
struct csiphy_device {
 struct resource *res;struct { int code; } fmt[1];
 struct { struct csi2_config *csi2; } cfg;
 struct { int entity; } subdev;struct phy *phy;
};
static int failure,calls[4],remote_error;
static u64 frequency;
static struct v4l2_mbus_config remote_cfg;
static u8 csiphy_get_bpp(const void *f,unsigned int n,int c) { (void)f;(void)n;(void)c;return 10; }
static s64 camss_get_link_freq(const int *e,u8 b,unsigned int l) { (void)e;(void)b;(void)l;return frequency; }
static int csiphy_get_remote_mbus_config(struct csiphy_device *p,struct v4l2_mbus_config *c)
{ (void)p;*c=remote_cfg;return remote_error; }
static int phy_mipi_dphy_get_default_config_for_hsclk(u64 hz,unsigned int n,struct phy_configure_opts_mipi_dphy *c)
{ calls[0]++;if(failure==1 || !hz || !n || n>4)return -EINVAL;c->configured=1;c->lanes=n;c->hs_clk_rate=hz;return 0; }
static int phy_mipi_dphy_config_validate(struct phy_configure_opts_mipi_dphy *c)
{ return c->configured && c->hs_clk_rate?0:-EINVAL; }
'''

BRIDGE=r'''
static int phy_set_mode(struct phy *p,enum phy_mode mode)
{ calls[1]++;return failure==2?-EIO:phy_qcom_mipi_csi2_set_mode(p,mode,0); }
static int phy_configure(struct phy *p,union phy_configure_opts *o)
{ calls[2]++;return failure==3?-ERANGE:phy_qcom_mipi_csi2_configure(p,o); }
static int phy_power_on(struct phy *p)
{ assert(p);calls[3]++;return failure==4?-EBUSY:0; }
'''

SUFFIX=r'''
int main(void)
{
 int cases=0;
 const struct mipi_csi2phy_hw_ops ops={0};
 struct mipi_csi2phy_soc_cfg soc={.ops=&ops};
 struct mipi_csi2phy_device device={.phy_mode=PHY_MODE_MIPI_DPHY,.soc_cfg=&soc,.stream_cfg={.num_data_lanes=4}};
 struct phy generic={.data=&device};
 struct format_info formats={0};struct resource res={.formats=&formats};
 struct csi2_config cfg={.lane_cfg={.num_data=4,.phy_type=V4L2_MBUS_CSI2_DPHY}};
 struct csiphy_device phy={.res=&res,.cfg={.csi2=&cfg},.phy=&generic};
 const int expected[]={0,-EINVAL,-EIO,-ERANGE,-EBUSY};
 for(failure=0;failure<=4;failure++) {
  memset(calls,0,sizeof(calls));frequency=800000000;
  assert(csiphy_stream_on(&phy)==expected[failure]);
  for(int stage=0;stage<4;stage++)assert(calls[stage]==(!failure || stage<failure));
  cases++;
 }
 failure=0;frequency=0;memset(calls,0,sizeof(calls));
 assert(csiphy_stream_on(&phy)==-EINVAL && !calls[3]);cases++;
 cfg.lane_cfg.phy_type=V4L2_MBUS_CSI2_CPHY;cfg.lane_cfg.num_data=3;
 device.phy_mode=PHY_MODE_MIPI_CPHY;device.stream_cfg.num_data_lanes=3;
 remote_cfg=(struct v4l2_mbus_config){.type=V4L2_MBUS_CSI2_CPHY,.link_freq=1000000000,
   .bus.mipi_csi2={.num_data_lanes=3,.t_prepare_ns=38,.t_preamble_ns=200}};
 memset(calls,0,sizeof(calls));
 assert(csiphy_stream_on(&phy)==-EOPNOTSUPP && !calls[3]);cases++;
 soc.reg_info.cphy=&soc;soc.reg_info.num_cphy=1;
 assert(csiphy_stream_on(&phy)==0);
 assert(device.stream_cfg.link_freq==1000000000 && device.stream_cfg.t_prepare_ns==38);cases++;
 for(int bad=0;bad<7;bad++) {
  struct v4l2_mbus_config saved=remote_cfg;
  switch(bad) {
   case 0:remote_cfg.type=V4L2_MBUS_CSI2_DPHY;break;
   case 1:remote_cfg.link_freq=0;break;
   case 2:remote_cfg.link_freq=U64_MAX;break;
   case 3:remote_cfg.bus.mipi_csi2.num_data_lanes=2;break;
   case 4:remote_cfg.bus.mipi_csi2.t_prepare_ns=0;break;
   case 5:remote_cfg.bus.mipi_csi2.t_preamble_ns=0;break;
   case 6:remote_error=-EOPNOTSUPP;break;
  }
  memset(calls,0,sizeof(calls));
  assert(csiphy_stream_on(&phy)<0 && !calls[1] && !calls[2] && !calls[3]);
  remote_cfg=saved;remote_error=0;cases++;
 }
 union phy_configure_opts opts={.mipi_cphy={.symbol_rate=2000000000,.trios=3,.t_prepare_ns=38,.t_preamble_ns=200}};
 for(int bad=0;bad<5;bad++) {
  union phy_configure_opts saved=opts;
  switch(bad) {
   case 0:opts.mipi_cphy.trios=4;break;
   case 1:opts.mipi_cphy.trios=2;break;
   case 2:opts.mipi_cphy.symbol_rate=1;break;
   case 3:opts.mipi_cphy.symbol_rate=U64_MAX;break;
   case 4:opts.mipi_cphy.t_prepare_ns=0;break;
  }
  assert(phy_qcom_mipi_csi2_configure(&generic,&opts)==-EINVAL);opts=saved;cases++;
 }
 assert(phy_qcom_mipi_csi2_set_mode(&generic,PHY_MODE_MIPI_DPHY,0)==-EOPNOTSUPP);cases++;
 assert(phy_qcom_mipi_csi2_set_mode(&generic,PHY_MODE_MIPI_CPHY,1)==-EOPNOTSUPP);cases++;
 struct mipi_csi2phy_stream_cfg timing={.t_prepare_ns=38,.t_preamble_ns=200};
 assert(phy_qcom_cphy_settle_cnt(&timing,400000000)==42);cases++;
 assert(phy_qcom_cphy_settle_cnt(&timing,0)==-ERANGE);cases++;
 timing.t_prepare_ns=1000;timing.t_preamble_ns=1000;
 assert(phy_qcom_cphy_settle_cnt(&timing,400000000)==-ERANGE);cases++;
 timing.t_prepare_ns=UINT32_MAX;timing.t_preamble_ns=UINT32_MAX;
 assert(phy_qcom_cphy_settle_cnt(&timing,UINT32_MAX)==-ERANGE);cases++;
 printf("CAMERA_ACTUAL_CPHY_BOUNDARIES_PASS cases=%d hardware=no\n",cases);
 return 0;
}
'''

def extract(path,start):
    source=path.read_text();a=source.index(start);b=source.index('\n}\n',a)+2
    return source[a:b]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    core=a.kernel/'drivers/phy/qualcomm/phy-qcom-mipi-csi2-core.c'
    functions=[extract(core,'static int phy_qcom_mipi_csi2_configure('),
               extract(core,'static int phy_qcom_mipi_csi2_set_mode('),
               extract(a.kernel/'drivers/phy/qualcomm/phy-qcom-mipi-csi2-3ph-dphy.c',
                       'static int phy_qcom_cphy_settle_cnt(')]
    camss=extract(a.kernel/'drivers/media/platform/qcom/camss/camss-csiphy.c',
                  'static int csiphy_stream_on(struct csiphy_device *csiphy)')
    generated=PREFIX+'\n'.join(functions)+BRIDGE+camss+SUFFIX
    test=out/'actual-cphy-functions.c';test.write_text(generated)
    results=[]
    for mode,flags in [('release',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
        exe=out/('cphy-errors-'+mode)
        subprocess.run(['gcc','-std=gnu11','-Wall','-Wextra','-Werror',*flags,str(test),'-o',str(exe)],check=True,timeout=30)
        env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1')
        r=subprocess.run([exe],capture_output=True,text=True,timeout=10,env=env)
        (out/(mode+'.log')).write_text(r.stdout+r.stderr)
        assert r.returncode==0 and 'cases=26 hardware=no' in r.stdout
        results.append({'configuration':mode,'cases':26,'returncode':0})
    digest=lambda b:hashlib.sha256(b).hexdigest()
    report={'scope':'ACTUAL_CPHY_FUNCTION_SYNTHETIC_BOUNDARIES_ONLY','phone_operations':0,
            'functions_sha256':digest(('\n'.join(functions)+camss).encode()),
            'generated_test_sha256':digest(test.read_bytes()),'results':results,
            'hardware_qualified':False,'phone_register_table_present':False}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
