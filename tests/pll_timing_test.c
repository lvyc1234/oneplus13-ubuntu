/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;typedef int32_t s32;
#define BIT(n) (1U<<(n))
#define FRAC_BITS 18
#define VCO_REF_CLK_RATE 19200000
#define DSI_PHY_7NM_QUIRK_V4_0 BIT(0)
#define DSI_PHY_7NM_QUIRK_V4_1 BIT(1)
#define DSI_PHY_7NM_QUIRK_V5_2 BIT(4)
#define DSI_PHY_7NM_QUIRK_V7_2 BIT(5)
#define DBG(...) do {} while(0)
#define __iomem
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define S_DIV_ROUND_UP(n,d) (((n)>=0)?(((n)+(d)-1)/(d)):(((n)-(d)+1)/(d)))
#define max_t(t,a,b) ((t)(a)>(t)(b)?(t)(a):(t)(b))
#define clamp_t(t,n,a,b) ((t)(n)<(t)(a)?(t)(a):(t)(n)>(t)(b)?(t)(b):(t)(n))
#define mult_frac(n,a,b) ((uint64_t)(n)*(a)/(b))
#define NSEC_PER_MSEC 1000000
#define CHECK(c) do {if(!(c)){fprintf(stderr,"line %d\n",__LINE__);abort();}}while(0)
static u64 div_u64(u64 n,u32 d) {return n/d;}
static u64 div_u64_rem(u64 n,u32 d,u32 *r) {*r=n%d;return n/d;}
struct clk_hw {int unused;};
struct clk_rate_request {unsigned long rate;};
struct msm_dsi_phy_cfg {u32 quirks;unsigned long min_pll_rate,max_pll_rate;};
struct msm_dsi_phy {const struct msm_dsi_phy_cfg *cfg;void *pll_base;int id;};
/* PLL_CONFIG */
struct dsi_pll_7nm {struct clk_hw clk_hw;u64 vco_current_rate;struct msm_dsi_phy *phy;};
#define to_pll_7nm(hw) ((struct dsi_pll_7nm *)(hw))
struct dsi_pll_regs {u32 decimal_div_start,frac_div_start_low,frac_div_start_mid,frac_div_start_high,pll_clock_inverters,pll_lockdet_rate,pll_prop_gain_rate;};
/* Vendor structs intentionally contain only fields read by the extracted function. */
struct vendor_config {bool disable_prescaler;u32 frac_bits,lock_timer;};
struct dsi_pll_3nm {struct vendor_config pll_configuration;struct dsi_pll_regs reg_setup;};
struct dsi_pll_resource {u64 vco_ref_clk_rate,vco_current_rate;};
/* Vendor function spells its config tag the same as platform; rename at insertion. */
struct msm_dsi_phy_shared_timings {u32 clk_pre,clk_post;bool clk_pre_inc_by_2,byte_intf_clk_div_2;};
struct msm_dsi_phy_clk_request {unsigned long bitclk_rate,escclk_rate;};
/* TIMING_STRUCT */
#define REG_DSI_7nm_PHY_PLL_DECIMAL_DIV_START_1 0
#define REG_DSI_7nm_PHY_PLL_FRAC_DIV_START_LOW_1 4
#define REG_DSI_7nm_PHY_PLL_FRAC_DIV_START_MID_1 8
#define REG_DSI_7nm_PHY_PLL_FRAC_DIV_START_HIGH_1 12
static u32 regs[4];static int bias;
static u32 readl(void *p) {CHECK(bias==1);return *(u32 *)p;}
static void dsi_pll_enable_pll_bias(struct dsi_pll_7nm *p) {(void)p;CHECK(!bias);bias++;}
static void dsi_pll_disable_pll_bias(struct dsi_pll_7nm *p) {(void)p;CHECK(bias==1);bias--;}
/* REAL_FUNCTIONS */
/* VECTORS */
int main(void) {
 struct msm_dsi_phy_cfg cfg={DSI_PHY_7NM_QUIRK_V7_2,600000000,5000000000ULL};
 struct msm_dsi_phy phy={&cfg,regs,0};
 struct dsi_pll_7nm p={.phy=&phy};
 struct dsi_pll_3nm vendor={.pll_configuration={.frac_bits=18}};
 struct dsi_pll_resource resource={.vco_ref_clk_rate=19200000};
 for(size_t i=0;i<ARRAY_SIZE(cases);i++) {
   struct dsi_pll_config c={0};p.vco_current_rate=cases[i].rate;
   dsi_pll_calc_dec_frac(&p,&c);
   CHECK(c.decimal_div_start==cases[i].dec&&c.frac_div_start==cases[i].frac&&c.pll_clock_inverters==cases[i].inv);
   resource.vco_current_rate=cases[i].rate;vendor_dec_frac(&vendor,&resource);
   CHECK(vendor.reg_setup.decimal_div_start==c.decimal_div_start);
   CHECK((vendor.reg_setup.frac_div_start_low|(vendor.reg_setup.frac_div_start_mid<<8)|(vendor.reg_setup.frac_div_start_high<<16))==c.frac_div_start);
   CHECK(vendor.reg_setup.pll_clock_inverters==c.pll_clock_inverters);
   regs[0]=c.decimal_div_start;regs[1]=c.frac_div_start&255;regs[2]=(c.frac_div_start>>8)&255;regs[3]=c.frac_div_start>>16;
   CHECK(dsi_pll_7nm_vco_recalc_rate(&p.clk_hw,19200000)==cases[i].actual&&!bias);
   struct clk_rate_request req={cases[i].rate};
   CHECK(!dsi_pll_7nm_clk_determine_rate(&p.clk_hw,&req));
   CHECK(req.rate==(cases[i].rate<600000000?600000000:cases[i].rate>5000000000ULL?5000000000ULL:cases[i].rate));
 }
 struct msm_dsi_dphy_timing t={0};struct msm_dsi_phy_clk_request req={1363200000,19200000};
 CHECK(!msm_dsi_dphy_timing_calc_v4(&t,&req)&&t.shared_timings.byte_intf_clk_div_2);
 printf("TIMING [0,%u,%u,%u,%u,%u,%u,%u,%u,2,4,0,%u,%u]\n",t.clk_zero,t.clk_prepare,t.clk_trail,t.hs_exit,t.hs_zero,t.hs_prepare,t.hs_trail,t.hs_rqst,t.shared_timings.clk_pre,t.shared_timings.clk_post);
 struct msm_dsi_dphy_timing expected=t;
 for(int delta=-16;delta<=16;delta++) {memset(&t,0,sizeof(t));req.bitclk_rate=1363200000+delta;CHECK(!msm_dsi_dphy_timing_calc_v4(&t,&req));CHECK(!memcmp(&t,&expected,sizeof(t)));}
 req.bitclk_rate=0;CHECK(msm_dsi_dphy_timing_calc_v4(&t,&req)==-EINVAL);
 req.bitclk_rate=1363200000;req.escclk_rate=0;CHECK(msm_dsi_dphy_timing_calc_v4(&t,&req)==-EINVAL);
 puts("OK real platform/vendor PLL and D-PHY timing");return 0;
}
