#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the actual staged PHY setup function with synthetic failure stubs."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path

PREFIX=r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef int64_t s64;
#define MSM_CSIPHY_PAD_SINK 0
#define PHY_MODE_MIPI_DPHY 1
#define dev_err(device,...) ((void)(device))
struct device { int unused; };
struct csiphy_lanes_cfg { unsigned int num_data; };
struct csi2_config { struct csiphy_lanes_cfg lane_cfg; };
struct phy_configure_opts_mipi_dphy { unsigned int configured; };
union phy_configure_opts { struct phy_configure_opts_mipi_dphy mipi_dphy; };
struct format_info { const void *formats; unsigned int nformats; };
struct resource { struct format_info *formats; };
struct camss { struct device *dev; };
struct csiphy_device {
 struct resource *res;
 struct { int code; } fmt[1];
 struct { struct csi2_config *csi2; } cfg;
 struct camss *camss;
 struct { int entity; } subdev;
 void *phy;
};
static int calls[4],failure;
static s64 frequency;
static u8 csiphy_get_bpp(const void *formats,unsigned int count,int code)
{ (void)formats;(void)count;(void)code;return 10; }
static s64 camss_get_link_freq(const int *entity,u8 bpp,u8 lanes)
{ (void)entity;(void)bpp;(void)lanes;return frequency; }
static int phy_mipi_dphy_get_default_config_for_hsclk(unsigned long long hz,unsigned int lanes,struct phy_configure_opts_mipi_dphy *cfg)
{ calls[0]++;if(failure==1 || !hz || !lanes || lanes>4)return -EINVAL;cfg->configured=1;return 0; }
static int phy_set_mode(void *phy,int mode)
{ assert(phy && mode==PHY_MODE_MIPI_DPHY);calls[1]++;return failure==2?-EIO:0; }
static int phy_configure(void *phy,union phy_configure_opts *opts)
{ assert(phy && opts->mipi_dphy.configured==1);calls[2]++;return failure==3?-ERANGE:0; }
static int phy_power_on(void *phy)
{ assert(phy);calls[3]++;return failure==4?-EBUSY:0; }
'''
SUFFIX=r'''
int main(void)
{
 struct device device={0};struct camss camss={.dev=&device};
 struct format_info formats={.formats=NULL,.nformats=1};
 struct resource res={.formats=&formats};struct csi2_config cfg={.lane_cfg={.num_data=4}};
 struct csiphy_device phy={.res=&res,.cfg={.csi2=&cfg},.camss=&camss,.phy=&device};
 const int expected[]={0,-EINVAL,-EIO,-ERANGE,-EBUSY};
 for(failure=0;failure<=4;failure++) {
  memset(calls,0,sizeof(calls));frequency=800000000;
  assert(csiphy_stream_on(&phy)==expected[failure]);
  for(int stage=0;stage<4;stage++)assert(calls[stage]==(!failure || stage<failure));
 }
 failure=0;frequency=-EPIPE;memset(calls,0,sizeof(calls));
 assert(csiphy_stream_on(&phy)==-EINVAL);
 for(int stage=0;stage<4;stage++)assert(calls[stage]==0);
 frequency=800000000;cfg.lane_cfg.num_data=0;memset(calls,0,sizeof(calls));
 assert(csiphy_stream_on(&phy)==-EINVAL && calls[0]==1);
 for(int stage=1;stage<4;stage++)assert(calls[stage]==0);
 puts("CAMERA_ACTUAL_PHY_SETUP_SUCCESS_AND_FAILURE_BOUNDARIES_PASS cases=7 hardware=no");
 return 0;
}
'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve()
    if out.exists():raise ValueError('Preserve prior source-bound checks')
    out.mkdir(parents=True)
    path=a.kernel/'drivers/media/platform/qcom/camss/camss-csiphy.c';source=path.read_text()
    start=source.index('static int csiphy_stream_on(struct csiphy_device *csiphy)\n')
    opening=source.index('{',start);level=1;end=opening+1
    while level:
        if source[end]=='{':level+=1
        elif source[end]=='}':level-=1
        end+=1
    function=source[start:end];generated=PREFIX+function+SUFFIX
    test=out/'actual-phy-function.c';test.write_text(generated)
    results=[]
    for mode,flags in [('release',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
        exe=out/('phy-errors-'+mode)
        subprocess.run(['gcc','-std=gnu11','-Wall','-Wextra','-Werror',*flags,str(test),'-o',str(exe)],check=True,timeout=30)
        env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1')
        r=subprocess.run([exe],capture_output=True,text=True,timeout=10,env=env)
        (out/(mode+'.log')).write_text(r.stdout+r.stderr)
        assert r.returncode==0 and 'cases=7 hardware=no' in r.stdout
        results.append({'configuration':mode,'cases':7,'returncode':0})
    sha=lambda value:hashlib.sha256(value).hexdigest()
    report={'scope':'ACTUAL_STAGED_CAMERA_FUNCTION_SYNTHETIC_ERRORS_ONLY','phone_operations':0,
            'staged_source_sha256':sha(path.read_bytes()),'function_sha256':sha(function.encode()),
            'generated_test_sha256':sha(test.read_bytes()),'results':results,
            'electrical_or_sensor_operation_claimed':False,'hardware_qualified':False}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
