/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdint.h>
#include <stdbool.h>
#include <limits.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define CLK_DIVIDER_ONE_BASED BIT(0)
#define CLK_DIVIDER_POWER_OF_TWO BIT(1)
#define CLK_DIVIDER_EVEN_INTEGERS BIT(2)
#define CLK_DIVIDER_ROUND_CLOSEST BIT(3)
#define CLK_SET_RATE_PARENT BIT(4)
#define DIV_ROUND_UP_ULL(n,d) (((n)+(d)-1)/(d))
#define min(a,b) ((a)<(b)?(a):(b))
static uint64_t host_abs(uint64_t n) {int64_t value=(int64_t)n;return value<0?(uint64_t)(-(value+1))+1:(uint64_t)value;}
#define abs(n) host_abs(n)
#define CHECK(c) do {if(!(c)){fprintf(stderr,"line %d\n",__LINE__);abort();}}while(0)
static unsigned int __roundup_pow_of_two(unsigned int n) {unsigned int p=1;while(p<n)p<<=1;return p;}
static unsigned int __rounddown_pow_of_two(unsigned int n) {unsigned int p=1;while((p<<1)<=n)p<<=1;return p;}
static unsigned int clk_div_mask(unsigned int width) {return (1U<<width)-1;}
struct clk_hw {unsigned long flags,current;struct clk_hw *parent;int kind;};
struct clk_rate_request {unsigned long rate,best_parent_rate;struct clk_hw *best_parent_hw;};
struct clk_div_table {unsigned int val,div;};
struct frac_entry {unsigned int num,den;};
struct freq_tbl {unsigned long freq;u8 src,pre_div;u32 m,n;};
struct parent_map {u8 src,cfg;};
struct clk_regmap {struct clk_hw hw;void *regmap;};
struct clk_rcg2 {struct clk_regmap clkr;u32 hid_width,cmd_rcgr;const struct parent_map *parent_map;};
#define to_clk_rcg2(hw) ((struct clk_rcg2 *)(hw))
#define CFG_REG 4
#define CFG_SRC_SEL_MASK (7<<8)
#define CFG_SRC_SEL_SHIFT 8
#define CFG_SRC_DIV_SHIFT 0
static struct freq_tbl programmed;static int configured;
static int regmap_read(void *map,u32 address,u32 *value) {(void)map;(void)address;*value=1;return 0;}
static int clk_rcg2_configure(struct clk_rcg2 *rcg,const struct freq_tbl *f) {(void)rcg;programmed=*f;configured++;return 0;}
static int clk_hw_get_num_parents(struct clk_hw *hw) {(void)hw;return 1;}
static unsigned long clk_hw_get_flags(struct clk_hw *hw) {return hw->flags;}
static long clk_hw_round_rate(struct clk_hw *,unsigned long);
int divider_determine_rate(struct clk_hw *,struct clk_rate_request *,const struct clk_div_table *,u8,unsigned long);
#define DEFAULT_TEARCHECK_SYNC_THRESH_START 4
#define DEFAULT_TEARCHECK_SYNC_THRESH_CONTINUE 4
#define INTF_0 0
#define PINGPONG_0 0
static void debug_ignore(void *p,const char *fmt,...) {(void)p;(void)fmt;}
#define DPU_DEBUG_CMDENC(...) debug_ignore(__VA_ARGS__)
struct dpu_hw_tear_check {u32 vsync_count,hw_vsync_mode,sync_cfg_height,vsync_init_val,sync_threshold_start,sync_threshold_continue,start_pos,rd_ptr_irq;};
struct drm_display_mode {u32 vdisplay,vtotal;};
struct dpu_kms {unsigned long vsync;};
struct te_hw {int idx;struct {void (*enable_tearcheck)(struct te_hw *,struct dpu_hw_tear_check *);}ops;};
struct dpu_encoder_phys {bool has_intf_te;struct te_hw *hw_intf,*hw_pp;struct drm_display_mode cached_mode;struct dpu_kms *dpu_kms;};
struct dpu_encoder_phys_cmd {int unused;};
static struct dpu_encoder_phys_cmd cmd_enc;
static struct dpu_encoder_phys_cmd *to_dpu_encoder_phys_cmd(struct dpu_encoder_phys *p) {(void)p;return &cmd_enc;}
static unsigned long dpu_kms_get_clk_rate(struct dpu_kms *k,const char *name) {(void)name;return k->vsync;}
static u32 drm_mode_vrefresh(struct drm_display_mode *m) {(void)m;return 120;}
static struct dpu_hw_tear_check te_config;static int te_calls;
static void capture_te(struct te_hw *hw,struct dpu_hw_tear_check *cfg) {(void)hw;te_config=*cfg;te_calls++;}
/* REAL_FUNCTIONS */
static long clk_hw_round_rate(struct clk_hw *hw,unsigned long rate) {
 if(hw->kind==0)return hw->current; /* already set lane PLL; no parent propagation */
 struct clk_rate_request req={rate,hw->parent->current,hw->parent};
 CHECK(!divider_determine_rate(hw,&req,NULL,4,CLK_DIVIDER_ONE_BASED));
 return req.rate;
}
int main(void) {
 const struct parent_map map={0,0};
 for(int half=0;half<2;half++) {
   struct clk_hw bit={.current=half?681600000:1363200000};
   struct clk_hw phy_pixel={.parent=&bit,.kind=1};
   struct clk_rate_request req={181760000,0,&phy_pixel};
   struct clk_rcg2 rcg={.hid_width=5,.parent_map=&map};
   CHECK(!clk_pixel_determine_rate(&rcg.clkr.hw,&req));
   CHECK(req.rate==181760000&&req.best_parent_rate==(half?681600000:272640000));
   CHECK(bit.current==(half?681600000:1363200000));
   CHECK(!clk_pixel_set_rate(&rcg.clkr.hw,req.rate,req.best_parent_rate));
   CHECK(programmed.m==(half?4:2)&&programmed.n==(half?15:3)&&programmed.pre_div==1);
   CHECK(bit.current/req.best_parent_rate==(half?1:5));
   CHECK(clk_pixel_set_rate(&rcg.clkr.hw,181760000,123456789)==-EINVAL);
 }
 CHECK(configured==2);
 struct te_hw intf={.ops={capture_te}},pp={.ops={capture_te}};
 struct dpu_kms kms={19200000};
 struct dpu_encoder_phys phys={.has_intf_te=true,.hw_intf=&intf,.hw_pp=&pp,.cached_mode={3168,3200},.dpu_kms=&kms};
 dpu_encoder_phys_cmd_tearcheck_config(&phys);
 CHECK(te_calls==1&&te_config.vsync_count==50&&te_config.sync_cfg_height==6400);
 CHECK(te_config.start_pos==3168&&te_config.rd_ptr_irq==3169&&te_config.hw_vsync_mode==1);
 phys.has_intf_te=false;dpu_encoder_phys_cmd_tearcheck_config(&phys);CHECK(te_calls==2);
 kms.vsync=0;dpu_encoder_phys_cmd_tearcheck_config(&phys);CHECK(te_calls==2);
 kms.vsync=19200000;phys.hw_pp=NULL;dpu_encoder_phys_cmd_tearcheck_config(&phys);CHECK(te_calls==2);
 puts("OK actual divider/pixel callbacks and command TE configuration");return 0;
}
