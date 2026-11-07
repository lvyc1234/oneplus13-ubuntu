/* SPDX-License-Identifier: GPL-2.0-only */
/* Host-only external I/O; production functions/types are inserted by the checker. */
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#ifndef EOPNOTSUPP
#define EOPNOTSUPP 95
#endif
#define BIT(n) (1U<<(n))
#define ALIGN(n,a) (((n)+(a)-1)&~((a)-1))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define MIPI_DSI_MSG_USE_LPM 2
#define MIPI_DSI_MODE_VIDEO 1
struct mipi_dsi_host;
struct mipi_dsi_device;
/* MIPI_TYPES */
struct mipi_dsi_host {const struct mipi_dsi_host_ops *ops;};
struct device {int of_node;};
struct mipi_dsi_device {struct mipi_dsi_host *host;bool attached;unsigned int channel,lanes,format,dsc_slice_per_pkt;unsigned long mode_flags,hs_rate;void *dsc;struct device dev;};
struct platform_device {struct device dev;};
struct clk {bool on;};
struct mutex {bool held,dev;};
struct msm_dsi_phy {int unused;};
struct msm_dsi_phy_shared_timings {bool byte_intf_clk_div_2;};
struct msm_dsi_host;
struct test_ops {
 void *(*tx_buf_get)(struct msm_dsi_host *);
 void (*tx_buf_put)(struct msm_dsi_host *);
 int (*dma_base_get)(struct msm_dsi_host *,uint64_t *);
 int (*link_clk_set_rate)(struct msm_dsi_host *);
 int (*link_clk_enable)(struct msm_dsi_host *);
 void (*link_clk_disable)(struct msm_dsi_host *);
};
struct test_cfg {int num_regulators;};
static const struct test_cfg test_cfg={1};
struct msm_dsi_cfg_handler {const struct test_ops *ops;const struct test_cfg *cfg;};
struct msm_dsi_host {
 struct mipi_dsi_host base;struct mutex cmd_mutex,dev_mutex;void *supplies;
 const struct msm_dsi_cfg_handler *cfg_hnd;struct platform_device *pdev;
 int id,tx_size,dma_comp;bool aa569_batch,power_on,cphy_mode;
 unsigned int channel,lanes,format,dsc_slice_per_pkt,num_data_lanes;void *dsc;unsigned long hs_rate;
 unsigned long mode_flags,byte_clk_rate,pixel_clk_rate,byte_intf_clk_rate;
 struct clk *byte_src_clk,*pixel_src_clk,*dsi_pll_byte_clk,*dsi_pll_pixel_clk;
 struct clk *esc_clk,*byte_clk,*pixel_clk,*byte_intf_clk;
 u32 dma_cmd_ctrl_restore;
};
struct msm_dsi {struct mipi_dsi_host *host;};
static struct {struct msm_dsi *dsi[2];bool bonded,sync;} manager;
#define DSI_0 0
#define IS_BONDED_DSI() manager.bonded
#define IS_SYNC_NEEDED() manager.sync
static struct msm_dsi *dsi_mgr_get_dsi(int id) {return manager.dsi[id];}
static struct msm_dsi_host *to_msm_dsi_host(struct mipi_dsi_host *h) {return (struct msm_dsi_host *)h;}
#define DBG(...) do {} while(0)
#define pr_err(...) do {} while(0)
#define dev_err(dev,...) do {(void)(dev);} while(0)
#define dev_warn(dev,...) do {(void)(dev);} while(0)
#define DRM_DEV_ERROR(dev,...) do {(void)(dev);} while(0)
#define MIPI_DSI_FMT_RGB101010 30
#define MSM_DSI_VER_MAJOR_6G 6
#define MSM_DSI_6G_VER_MINOR_V2_1_0 1
static bool msm_dsi_host_version_geq(struct msm_dsi_host *h,int a,int b) {(void)h;(void)a;(void)b;return true;}
static bool msm_dsi_host_is_wide_bus_enabled(struct mipi_dsi_host *h) {(void)h;return true;}
static bool of_device_is_compatible(int node,const char *name) {(void)name;return node==1;}
static int dsi_dev_attach(struct platform_device *);
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define ASSERT(c) do {if(!(c)){fprintf(stderr,"assert line %d\n",__LINE__);abort();}}while(0)
#define CHECK(c) ASSERT(c)
#define REG_DSI_CTRL 0
#define REG_DSI_CLK_CTRL 1
#define REG_DSI_RESET 2
#define DSI_CTRL_ENABLE BIT(0)
#define DSI_CTRL_CMD_MODE_EN BIT(1)
#define DSI_CLK_CTRL_ENABLE_CLKS 255
#define DSI_IRQ_MASK_CMD_DMA_DONE 1
#define DSI_RESET_TOGGLE_DELAY_MS 20
static u8 dma[4098];
static u32 regs[3];
static int pm,pm_gets,pm_puts,maps,unmaps,mapped,steps,fail_step,fail_pm,fail_map,fail_iova;
static int kicks,wait_result,reset_count,irq_on,hs_on,lock_count,unlock_count,legacy_calls,opp_vote;
static int packed_size,attach_error,attach_calls,regulators,fail_regulator,fail_pinctrl;
static void mutex_lock(struct mutex *m) {ASSERT(!m->held);m->held=true;lock_count++;}
static void mutex_unlock(struct mutex *m) {ASSERT(m->held&&!mapped&&!irq_on&&!hs_on);if(!m->dev)ASSERT(!pm);m->held=false;unlock_count++;}
static int pm_runtime_resume_and_get(struct device *d) {(void)d;pm_gets++;if(fail_pm)return -EIO;ASSERT(!pm);pm++;return 0;}
static void pm_runtime_put(struct device *d) {(void)d;ASSERT(pm==1);pm--;pm_puts++;}
static int clock_op(void) {ASSERT(pm==1);return ++steps==fail_step?-EIO:0;}
static int clk_set_parent(struct clk *a,struct clk *b) {(void)a;(void)b;return clock_op();}
static int dev_pm_opp_set_rate(struct device *d,unsigned long r) {(void)d;if(!r){opp_vote=0;return 0;}int ret=clock_op();if(!ret)opp_vote=1;return ret;}
static int clk_set_rate(struct clk *a,unsigned long r) {(void)a;(void)r;return clock_op();}
static int clk_prepare_enable(struct clk *c) {int r=clock_op();if(r)return r;ASSERT(!c->on);c->on=true;return 0;}
static void clk_disable_unprepare(struct clk *c) {ASSERT(c->on);c->on=false;}
static void *buf_get(struct msm_dsi_host *h) {ASSERT(h->cmd_mutex.held&&!mapped);maps++;if(fail_map)return (void *)(intptr_t)-ENOMEM;mapped=1;return dma+1;}
static void buf_put(struct msm_dsi_host *h) {ASSERT(h->cmd_mutex.held&&mapped);mapped=0;unmaps++;}
static int base_get(struct msm_dsi_host *h,uint64_t *base) {ASSERT(h->cmd_mutex.held&&pm==1&&irq_on);*base=0x1000;return fail_iova?-EIO:0;}
static u32 dsi_read(struct msm_dsi_host *h,u32 reg) {(void)h;ASSERT(pm==1);return regs[reg];}
static void dsi_write(struct msm_dsi_host *h,u32 reg,u32 value) {(void)h;ASSERT(pm==1);regs[reg]=value;if(reg==REG_DSI_RESET&&value)reset_count++;}
static void dsi_set_tx_power_mode(int lp,struct msm_dsi_host *h) {(void)h;ASSERT(pm==1);hs_on=!lp;}
static void dsi_intr_ctrl(struct msm_dsi_host *h,int mask,int enable) {(void)h;(void)mask;ASSERT(pm==1);irq_on=enable;}
static void reinit_completion(int *c) {*c=0;}
static void dsi_wait4video_eng_busy(struct msm_dsi_host *h) {ASSERT(h->cmd_mutex.held&&pm==1);}
static void msm_dsi_host_cmd_xfer_commit(struct mipi_dsi_host *h,u32 base,u32 len) {ASSERT(to_msm_dsi_host(h)->cmd_mutex.held&&pm==1&&irq_on);ASSERT(base==0x1000);kicks++;packed_size=len;}
static int wait_for_completion_timeout(int *c,int timeout) {(void)c;ASSERT(kicks&&pm==1&&irq_on&&timeout==200);return wait_result;}
static int msecs_to_jiffies(int ms) {return ms;}
static void msleep(int ms) {(void)ms;ASSERT(pm==1);}
static void wmb(void) {}
static int msm_dsi_manager_cmd_xfer(int id,const struct mipi_dsi_msg *msg) {(void)id;legacy_calls++;return msg->tx_len;}
static int regulator_bulk_enable(int n,void *supplies) {(void)n;(void)supplies;if(fail_regulator)return -EIO;ASSERT(!regulators);regulators=1;return 0;}
static void regulator_bulk_disable(int n,void *supplies) {(void)n;(void)supplies;ASSERT(regulators==1);regulators=0;}
static void msm_dsi_sfpb_config(struct msm_dsi_host *host,bool enable) {(void)host;(void)enable;}
static int pinctrl_pm_select_default_state(struct device *d) {(void)d;return fail_pinctrl?-EIO:0;}
static void dsi_timing_setup(struct msm_dsi_host *h,bool bonded) {(void)h;(void)bonded;ASSERT(pm==1);}
static void dsi_ctrl_enable(struct msm_dsi_host *h,struct msm_dsi_phy_shared_timings *t,struct msm_dsi_phy *p) {(void)h;(void)t;(void)p;ASSERT(pm==1);}
void msm_dsi_host_xfer_restore(struct mipi_dsi_host *,const struct mipi_dsi_msg *);
/* REAL_FUNCTIONS */
/* VECTORS */
static struct clk clocks[8];
static struct platform_device pdev;
static const struct test_ops ops={buf_get,buf_put,base_get,dsi_link_clk_set_rate_6g_v2_9,dsi_link_clk_enable_6g,dsi_link_clk_disable_6g};
static const struct msm_dsi_cfg_handler cfg={&ops,&test_cfg};
static const struct mipi_dsi_host_ops host_ops={.attach=dsi_host_attach,.transfer=dsi_host_transfer,.transfer_batch=dsi_host_transfer_batch};
static struct msm_dsi_host h;
static struct msm_dsi d;
static struct mipi_dsi_device peripheral;
static void fresh(void) {
 memset(&h,0,sizeof(h));memset(clocks,0,sizeof(clocks));memset(&manager,0,sizeof(manager));
 memset(dma,0xa5,sizeof(dma));regs[0]=0x101;regs[1]=regs[2]=0;
 pm=pm_gets=pm_puts=maps=unmaps=mapped=steps=fail_step=fail_pm=fail_map=fail_iova=0;
 kicks=reset_count=irq_on=hs_on=lock_count=unlock_count=legacy_calls=packed_size=0;wait_result=1;
 regulators=fail_regulator=fail_pinctrl=0;opp_vote=attach_error=attach_calls=0;h.base.ops=&host_ops;h.cfg_hnd=&cfg;h.pdev=&pdev;h.tx_size=4096;h.aa569_batch=h.power_on=true;h.dev_mutex.dev=true;
 h.num_data_lanes=4;pdev.dev.of_node=1;
 h.byte_clk_rate=100;h.pixel_clk_rate=200;h.byte_intf_clk_rate=50;
 h.esc_clk=&clocks[0];h.byte_clk=&clocks[1];h.pixel_clk=&clocks[2];h.byte_intf_clk=&clocks[3];
 h.byte_src_clk=&clocks[4];h.pixel_src_clk=&clocks[5];h.dsi_pll_byte_clk=&clocks[6];h.dsi_pll_pixel_clk=&clocks[7];
 d.host=&h.base;manager.dsi[0]=&d;peripheral=(struct mipi_dsi_device){.host=&h.base,.attached=true};
}
static void balanced(void) {
 CHECK(!pm&&!mapped&&!h.cmd_mutex.held&&!irq_on&&!hs_on&&!opp_vote&&pm_puts<=pm_gets);
 CHECK(lock_count==unlock_count);CHECK(dma[0]==0xa5&&dma[4097]==0xa5);
 for(size_t i=0;i<ARRAY_SIZE(clocks);i++)CHECK(!clocks[i].on);
}
static void invalid(struct mipi_dsi_msg *m,size_t n,int error) {
 CHECK(mipi_dsi_transfer_batch(&peripheral,m,n)==error);balanced();
 CHECK(!maps&&!pm_gets&&!kicks);
}
static int dsi_dev_attach(struct platform_device *p) {
 (void)p;attach_calls++;
 if(h.aa569_batch) {
   CHECK(!peripheral.attached);
   CHECK(!mipi_dsi_transfer_batch(&peripheral,messages,1));
 }
 return attach_error;
}
int main(void) {
 size_t first=0;
 /* Independent vendor-format expectations for every production on group. */
 for(size_t g=0;g<ARRAY_SIZE(groups);g++) {
   fresh();CHECK(!mipi_dsi_transfer_batch(&peripheral,messages+first,groups[g].count));
   CHECK(kicks==1&&packed_size==(int)groups[g].len&&maps==1&&unmaps==1&&pm_gets==1&&pm_puts==1);
   CHECK(lock_count==1&&unlock_count==1&&!reset_count&&regs[0]==0x101);
   CHECK(!memcmp(dma+1,groups[g].bytes,groups[g].len));balanced();
   /* Ordinary message packing still marks each packet last. */
   for(size_t i=0;i<groups[g].count;i++) {
     fresh();mutex_lock(&h.cmd_mutex);
     int n=dsi_cmd_dma_add(&h,&messages[first+i]);
     CHECK(n>0&&(dma[4]&BIT(7))&&maps==1&&unmaps==1);
     mutex_unlock(&h.cmd_mutex);balanced();
   }
   first+=groups[g].count;
 }
 CHECK(first==150);
 struct mipi_dsi_msg m[65];for(size_t i=0;i<ARRAY_SIZE(m);i++)m[i]=messages[0];
 fresh();invalid(NULL,1,-EINVAL);fresh();invalid(m,0,-EINVAL);fresh();invalid(m,65,-EINVAL);
 fresh();h.tx_size=3;invalid(m,1,-EMSGSIZE);
 fresh();h.tx_size=8;CHECK(!mipi_dsi_transfer_batch(&peripheral,m,1));balanced();
 fresh();h.tx_size=8;invalid(m,2,-EMSGSIZE);
 for(int c=0;c<9;c++) {
   m[1]=messages[0];
   if(c==0)m[1].rx_len=1;
   if(c==1)m[1].rx_buf=dma;
   if(c==2)m[1].channel=1;
   if(c==3)m[1].tx_buf=NULL;
   if(c==4)m[1].tx_len=0;
   if(c==5)m[1].tx_len=101;
   if(c==6)m[1].type=0;
   if(c==7)m[1].flags=0;
   if(c==8)m[1].flags=3;
   fresh();invalid(m,2,-EINVAL);
 }
 m[1]=messages[0];
 for(int c=0;c<7;c++) {
   fresh();
   if(c==0)h.aa569_batch=false;
   if(c==1)h.power_on=false;
   if(c==2)h.cphy_mode=true;
   if(c==3)h.mode_flags=MIPI_DSI_MODE_VIDEO;
   if(c==4)manager.bonded=true;
   if(c==5)manager.sync=true;
   if(c==6)h.id=1;
   invalid(m,1,-EOPNOTSUPP);
 }
 fresh();peripheral.attached=false;CHECK(!mipi_dsi_transfer_batch(&peripheral,m,1));balanced();
 fresh();struct mipi_dsi_host_ops absent={0};h.base.ops=&absent;invalid(m,1,-EOPNOTSUPP);
 /* Mapping and PM/parent/rate/each enable failure must stop before kickoff. */
 fresh();fail_map=1;CHECK(mipi_dsi_transfer_batch(&peripheral,m,2)==-ENOMEM);CHECK(maps==1&&!unmaps&&!pm_gets);balanced();
 fresh();fail_pm=1;CHECK(mipi_dsi_transfer_batch(&peripheral,m,2)==-EIO);CHECK(!pm_puts&&!kicks);balanced();
 for(int i=1;i<=9;i++) {
   fresh();fail_step=i;CHECK(mipi_dsi_transfer_batch(&peripheral,m,2)==-EIO);
   CHECK(steps==i&&pm_gets==1&&pm_puts==1&&!kicks);balanced();
 }
 fresh();fail_iova=1;CHECK(mipi_dsi_transfer_batch(&peripheral,m,2)==-EIO);CHECK(!kicks&&!reset_count);balanced();
 fresh();wait_result=0;CHECK(mipi_dsi_transfer_batch(&peripheral,m,2)==-ETIMEDOUT);
 CHECK(kicks==1&&reset_count==1&&regs[0]==0x101);balanced();
 wait_result=1;CHECK(!mipi_dsi_transfer_batch(&peripheral,m,2));CHECK(kicks==2);balanced();
 fresh();for(size_t i=0;i<2;i++)m[i].flags=0;
 CHECK(!mipi_dsi_transfer_batch(&peripheral,m,2));balanced();
 /* Existing public single-message host callback still uses the manager. */
 fresh();CHECK(dsi_host_transfer(&h.base,&m[0])==(ssize_t)m[0].tx_len);CHECK(legacy_calls==1);balanced();
 /* Exercise actual attachment: component_add may synchronously start DRM. */
 fresh();h.aa569_batch=false;peripheral.attached=false;peripheral.dev.of_node=1;peripheral.lanes=4;
 CHECK(!dsi_host_attach(&h.base,&peripheral)&&h.aa569_batch&&attach_calls==1&&kicks==1);balanced();
 fresh();peripheral.attached=false;peripheral.dev.of_node=1;attach_error=-EIO;
 CHECK(dsi_host_attach(&h.base,&peripheral)==-EIO&&!h.aa569_batch&&kicks==1);balanced();
 fresh();peripheral.dev.of_node=0;CHECK(!dsi_host_attach(&h.base,&peripheral)&&!h.aa569_batch&&!kicks);balanced();
 /* Execute real host power-on failure paths; no leaked PM/OPP/rails. */
 struct msm_dsi_phy_shared_timings shared={true};
 fresh();h.power_on=false;fail_regulator=1;
 CHECK(msm_dsi_host_power_on(&h.base,&shared,false,NULL)==-EIO&&!regulators&&!pm_gets);balanced();
 fresh();h.power_on=false;fail_pm=1;
 CHECK(msm_dsi_host_power_on(&h.base,&shared,false,NULL)==-EIO&&!regulators&&!pm_puts);balanced();
 for(int i=1;i<=9;i++) {
   fresh();h.power_on=false;fail_step=i;
   CHECK(msm_dsi_host_power_on(&h.base,&shared,false,NULL)==-EIO&&!h.power_on&&!regulators);
   CHECK(pm_gets==1&&pm_puts==1);balanced();
 }
 fresh();h.power_on=false;fail_pinctrl=1;
 CHECK(msm_dsi_host_power_on(&h.base,&shared,false,NULL)==-EIO&&!h.power_on&&!regulators);balanced();
 puts("OK real DSI batches, resources, fault cleanup and legacy controls");return 0;
}
