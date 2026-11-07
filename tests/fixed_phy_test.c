/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint32_t u32;typedef int32_t s32;
#define NSEC_PER_MSEC 1000000
#define S_DIV_ROUND_UP(n,d) (((n)>=0)?(((n)+(d)-1)/(d)):(((n)-(d)+1)/(d)))
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define max_t(t,a,b) ((t)(a)>(t)(b)?(t)(a):(t)(b))
#define mult_frac(n,a,b) ((uint64_t)(n)*(a)/(b))
#define DBG(...) do {} while(0)
#define DSI_0 0
#define MSM_DSI_PHY_STANDALONE 0
#define CHECK(c) do {if(!(c)){fprintf(stderr,"line %d\n",__LINE__);abort();}}while(0)
struct msm_dsi_phy_shared_timings {u32 clk_pre,clk_post;bool clk_pre_inc_by_2,byte_intf_clk_div_2;};
struct msm_dsi_phy_clk_request {unsigned long bitclk_rate,escclk_rate;bool aa569_fixed_timing;};
/* TYPES */
struct msm_dsi_phy_cfg {int unused;};
static const struct msm_dsi_phy_cfg dsi_phy_3nm_8750_cfgs={0},other_cfg={1};
struct msm_dsi_phy {struct msm_dsi_dphy_timing timing;const struct msm_dsi_phy_cfg *cfg;int id,usecase;bool cphy_mode;};
/* REGISTERS */
static u32 registers[14];static int writes;
static void writel(u32 value,u32 *p) {CHECK(p>=registers&&p<registers+14);*p=value;writes++;}
/* FUNCTIONS */
static void program(struct msm_dsi_phy *phy) {
 struct msm_dsi_dphy_timing *timing=&phy->timing;u32 *base=registers;
 /* WRITE_TIMINGS */
}
int main(void) {
 struct msm_dsi_phy phy={.cfg=&dsi_phy_3nm_8750_cfgs};
 struct msm_dsi_phy_clk_request req={1363200000,19200000,true};
 const u32 expected[14]={0,44,12,12,29,26,12,12,11,2,4,0,36,17};
 CHECK(!msm_dsi_dphy_timing_calc_v4(&phy.timing,&req));
 CHECK(!dsi_aa569_fixed_timing(&phy,&req));program(&phy);
 CHECK(writes==14&&!memcmp(registers,expected,sizeof(expected)));
 CHECK(phy.timing.shared_timings.clk_pre==36&&phy.timing.shared_timings.clk_post==17);
 CHECK(!phy.timing.shared_timings.clk_pre_inc_by_2&&phy.timing.shared_timings.byte_intf_clk_div_2);
 for(int i=0;i<7;i++) {
   phy.cfg=i==0?&other_cfg:&dsi_phy_3nm_8750_cfgs;phy.id=i==1?1:0;
   phy.cphy_mode=i==2;phy.usecase=i==3?1:0;
   req.bitclk_rate=i==4?1363200001:i==5?0:1363200000;req.escclk_rate=i==6?0:19200000;
   memset(&phy.timing,0xa5,sizeof(phy.timing));struct msm_dsi_dphy_timing before=phy.timing;
   CHECK(dsi_aa569_fixed_timing(&phy,&req)==-EINVAL);
   CHECK(!memcmp(&before,&phy.timing,sizeof(before))&&writes==14);
 }
 phy.cfg=&dsi_phy_3nm_8750_cfgs;phy.id=phy.usecase=0;phy.cphy_mode=false;
 req.aa569_fixed_timing=false;req.escclk_rate=19200000;
 for(int i=-16;i<=16;i++) {
   req.bitclk_rate=1363200000+i;memset(&phy.timing,0,sizeof(phy.timing));
   CHECK(!msm_dsi_dphy_timing_calc_v4(&phy.timing,&req));
   struct msm_dsi_dphy_timing before=phy.timing;
   CHECK(!dsi_aa569_fixed_timing(&phy,&req));
   CHECK(!memcmp(&before,&phy.timing,sizeof(before)));
 }
 puts("OK fixed AA569 timing, register writes, rejection and legacy controls");return 0;
}
