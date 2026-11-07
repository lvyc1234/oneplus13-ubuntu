# SPDX-License-Identifier: GPL-2.0-only
"""Execute the actual automatic kernel endpoint against a fake transport/clock."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
typedef uint64_t u64;
typedef long ssize_t;
#define HZ 100U
#define NSEC_PER_SEC 1000000000ULL
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"%d: %s\n",__LINE__,#x);exit(1); } } while(0)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define spin_lock_irqsave(p,f) do { (void)(p);(f)=0; } while(0)
#define spin_unlock_irqrestore(p,f) do { (void)(p);(void)(f); } while(0)
#define __ATTR(n,m,r,w) {0}
#include "oneplus13-otg-auto-policy.h"
struct work_struct { bool queued; };
struct delayed_work { unsigned long delay; };
struct wakeup_source { bool held; };
struct device_attribute { int ignored; };
struct qcom_battmgr {
 int lock,op13_otg_lock;
 bool service_up,op13_otg_stopping,op13_otg_poisoned,op13_otg_awake;
 bool op13_otg_auto_source,op13_otg_auto_started,op13_otg_auto_starting;
 bool op13_otg_auto_detach_pending,op13_otg_auto_notifications_ready;
 unsigned int op13_otg_auto_generation,op13_otg_auto_start_generation;
 u64 op13_otg_supply_deadline;
 struct work_struct op13_otg_auto_work;
 struct delayed_work op13_otg_supply_restore_work;
 struct wakeup_source *op13_otg_wakeup;
 struct op13_otg_supply_lease op13_otg_supply_lease;
};
struct device { struct qcom_battmgr *data; };
static bool oneplus13_otg_auto=true,oneplus13_otg_supply_lease,oneplus13_otg_lease_enabled;
static bool machine=true;
static int system_freezable_wq,op13_otg_boot_used,depth;
static u64 now=1000000000ULL;
static unsigned int values[65],sets[100],set_values[100],nsets,gets,renewals;
static unsigned int fail_property,detach_property;
static void mutex_lock(int *p) { (void)p;CHECK(!depth);depth++; }
static void mutex_unlock(int *p) { (void)p;CHECK(depth==1);depth--; }
static u64 ktime_get_boottime_ns(void) { return now; }
static unsigned long nsecs_to_jiffies(u64 n) { return n/(NSEC_PER_SEC/HZ); }
static void mod_delayed_work(int q,struct delayed_work *w,unsigned long d) {
 (void)q;CHECK(depth==1);CHECK(d>0 && d<=15*HZ+1);w->delay=d;renewals++;
}
static void queue_work(int q,struct work_struct *w) { (void)q;w->queued=true; }
static void cancel_work_sync(struct work_struct *w) { w->queued=false; }
static int atomic_cmpxchg(int *p,int a,int b) { int old=*p;if(old==a)*p=b;return old; }
static bool op13_otg_machine(void) { return machine; }
static void *dev_get_drvdata(struct device *d) { return d->data; }
static bool sysfs_streq(const char *a,const char *b) { return !strcmp(a,b); }
static int kstrtouint(const char *s,int radix,unsigned int *v) {
 char *end;unsigned long n=strtoul(s,&end,radix);
 if(end==s || (*end && strcmp(end,"\n")) || n>UINT32_MAX)return -1;
 *v=n;return 0;
}
static int sysfs_emit(char *buf,const char *format,...) {
 va_list ap;va_start(ap,format);int n=vsnprintf(buf,4096,format,ap);va_end(ap);return n;
}
static void __pm_stay_awake(struct wakeup_source *w) { CHECK(w);w->held=true; }
static void op13_otg_awake_release(struct qcom_battmgr *b) { b->op13_otg_awake=false;b->op13_otg_wakeup->held=false; }
static int op13_otg_supply_transport_healthy(void *p) {
 struct qcom_battmgr *b=p;return b->service_up && !b->op13_otg_poisoned;
}
static int getv(void *p,unsigned int prop,unsigned int *v) {
 CHECK(depth==1);CHECK(op13_otg_supply_transport_healthy(p));gets++;*v=values[prop];return 0;
}
static int setv(void *p,unsigned int prop,unsigned int v) {
 struct qcom_battmgr *b=p;CHECK(depth==1);CHECK(op13_otg_supply_transport_healthy(p));
 CHECK(nsets<100);sets[nsets]=prop;set_values[nsets++]=v;
 if(prop==fail_property) { b->op13_otg_poisoned=true;return -1; }
 values[prop]=v;
 if(prop==detach_property) { b->op13_otg_auto_generation++;b->op13_otg_auto_source=false; }
 return 0;
}
static int op13_otg_supply_expired(void *p) {
 struct qcom_battmgr *b=p;return b->op13_otg_stopping || now>=b->op13_otg_supply_deadline;
}
static struct op13_otg_supply_io op13_otg_supply_io(struct qcom_battmgr *b) {
 return (struct op13_otg_supply_io){b,getv,setv,op13_otg_supply_transport_healthy,op13_otg_supply_expired};
}
#include "oneplus13-otg-auto-kernel.h"
static void event(struct qcom_battmgr *b,unsigned int e) {
 unsigned char wire[16]={10,128,0,0,2,0,0,0,7,0,0,0,0,0,0,0};
 wire[12]=e&255;wire[13]=(e>>8)&255;
 CHECK(op13_otg_auto_notification(b,wire,sizeof(wire)));
}
static ssize_t command(struct device *d,const char *s) {
 return oneplus13_otg_auto_store(d,NULL,s,strlen(s));
}
static void start(struct device *d) {
 CHECK(command(d,"arm")>0);event(d->data,0x50);
 char text[64];snprintf(text,sizeof(text),"on %u",d->data->op13_otg_auto_generation);
 CHECK(command(d,text)>0);CHECK(values[64]==1250 && values[24]==1);
}
int main(int argc,char **argv) {
 CHECK(argc==2);(void)dev_attr_oneplus13_otg_auto;(void)op13_otg_auto_remove;
 struct wakeup_source wake={0};
 struct qcom_battmgr b={.service_up=true,.op13_otg_auto_notifications_ready=true,.op13_otg_wakeup=&wake};
 struct device d={&b};
 if(!strcmp(argv[1],"repeat")) {
  for(int i=0;i<3;i++) { start(&d);CHECK(command(&d,"off")>0);
   CHECK(b.op13_otg_supply_lease.state==2);CHECK(!values[17]&&!values[18]&&!values[24]&&!values[64]); }
  CHECK(op13_otg_boot_used==1);
 } else if(!strcmp(argv[1],"order")) {
  start(&d);CHECK(nsets==4);CHECK(sets[0]==17 && sets[1]==18 && sets[2]==64 && sets[3]==24);
  CHECK(command(&d,"close")>0);CHECK(nsets==8);
  CHECK(sets[4]==24 && sets[5]==64 && sets[6]==18 && sets[7]==17);CHECK(!wake.held);
 } else if(!strcmp(argv[1],"expired")) {
  start(&d);now=b.op13_otg_supply_deadline;u64 old=b.op13_otg_supply_deadline;
  unsigned int old_renew=renewals,old_sets=nsets,old_gets=gets;
  CHECK(command(&d,"keepalive")<0);CHECK(command(&d,"on 1")<0);
  CHECK(b.op13_otg_supply_deadline==old && renewals==old_renew && nsets==old_sets && gets==old_gets);
  CHECK(command(&d,"close")>0);
 } else if(!strcmp(argv[1],"long")) {
  start(&d);for(int i=0;i<70;i++){now+=3*NSEC_PER_SEC;CHECK(command(&d,"keepalive")>0);}
  CHECK(now>120*NSEC_PER_SEC && values[24]==1 && nsets==4);CHECK(command(&d,"close")>0);
 } else if(!strcmp(argv[1],"generation")) {
  CHECK(command(&d,"arm")>0);CHECK(command(&d,"on 1")<0);event(&b,0x50);
  event(&b,0x51);CHECK(command(&d,"on 1")<0 && nsets==2);
  event(&b,0x50);CHECK(command(&d,"on 3")<0 && nsets==2);
  op13_otg_auto_detach_worker(&b.op13_otg_auto_work);CHECK(!values[17]&&!values[18]);
 } else if(!strcmp(argv[1],"detach-between")) {
  CHECK(command(&d,"arm")>0);event(&b,0x50);detach_property=64;
  CHECK(command(&d,"on 1")<0);CHECK(b.op13_otg_supply_lease.state==2);
  for(unsigned int i=0;i<nsets;i++)CHECK(!(sets[i]==24 && set_values[i]==1));
 } else if(!strcmp(argv[1],"poison")) {
  CHECK(command(&d,"arm")>0);event(&b,0x50);fail_property=24;
  CHECK(command(&d,"on 1")<0);CHECK(b.op13_otg_supply_lease.state==3);
  unsigned int old=nsets;CHECK(command(&d,"arm")<0 && command(&d,"close")<0 && nsets==old);
 } else if(!strcmp(argv[1],"detach")) {
  start(&d);event(&b,0x51);CHECK(b.op13_otg_auto_work.queued);
  op13_otg_auto_detach_worker(&b.op13_otg_auto_work);
  CHECK(b.op13_otg_supply_lease.state==2 && !values[24]&&!values[17]&&!wake.held);
 } else if(!strcmp(argv[1],"exclusive")) {
  oneplus13_otg_supply_lease=true;CHECK(command(&d,"arm")==-EOPNOTSUPP);CHECK(!nsets);
  oneplus13_otg_supply_lease=false;oneplus13_otg_lease_enabled=true;
  CHECK(command(&d,"arm")==-EOPNOTSUPP);CHECK(!nsets);
 } else if(!strcmp(argv[1],"subscribe")) {
  b.op13_otg_auto_notifications_ready=false;CHECK(command(&d,"arm")<0 && !nsets);
  b.op13_otg_auto_notifications_ready=true;machine=false;CHECK(command(&d,"arm")<0 && !nsets);
 } else if(!strcmp(argv[1],"early-watchdog")) {
  start(&d);now+=3*NSEC_PER_SEC;CHECK(command(&d,"keepalive")>0);
  mutex_lock(&b.lock);CHECK(op13_otg_auto_watchdog_early(&b));mutex_unlock(&b.lock);CHECK(values[24]==1);
  now=b.op13_otg_supply_deadline;CHECK(!op13_otg_auto_watchdog_early(&b));
 } else if(!strcmp(argv[1],"crash-watchdog")) {
  start(&d);now=b.op13_otg_supply_deadline;
  CHECK(op13_otg_auto_watchdog_worker(&b));CHECK(b.op13_otg_supply_lease.state==2);
  CHECK(!values[24]&&!values[64]&&!values[18]&&!values[17]&&!wake.held);
  CHECK(sets[4]==24 && sets[5]==64 && sets[6]==18 && sets[7]==17);
 } else if(!strcmp(argv[1],"decode")) {
  unsigned char p[17]={10,128,0,0,2,0,0,0,7,0,0,0,0x50,0,0,0};unsigned int v=9;
  CHECK(op13_otg_auto_decode(p,16,&v) && v==1);p[12]=0x51;
  CHECK(op13_otg_auto_decode(p,16,&v) && v==0);
  for(unsigned int n=0;n<=17;n++)if(n!=16)CHECK(!op13_otg_auto_decode(p,n,&v));
  p[12]=0x50;p[13]=1;CHECK(!op13_otg_auto_decode(p,16,&v));p[13]=0;
  const int fields[]={0,4,8};for(unsigned int i=0;i<3;i++){p[fields[i]]^=1;CHECK(!op13_otg_auto_decode(p,16,&v));p[fields[i]]^=1;}
 } else if(!strcmp(argv[1],"status")) {
  start(&d);char text[4096];CHECK(oneplus13_otg_auto_show(&d,NULL,text)>0);
  CHECK(strstr(text,"state=1 generation=1 source=1 healthy=1"));
 } else CHECK(0);
 return 0;
}
'''


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc'), 'Linux GCC required')
class AutomaticKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='op13-auto-kernel-')
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        for name in ('otg-auto-policy.h','otg-auto-kernel.h','otg-supply-lease.h'):
            shutil.copyfile(ROOT/'tools/oneplus13/usb-draft'/name,directory/('oneplus13-'+name))
        source=directory/'test.c';source.write_text(HARNESS)
        cls.binary=directory/'test'
        subprocess.run(['gcc','-std=c11','-O1','-Wall','-Wextra','-Werror',
            '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',
            str(source),'-o',str(cls.binary)],check=True,timeout=30)

    def check(self,name):
        p=subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=5,
            env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1:halt_on_error=1'})
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)

    def test_repeated_cycles_after_proven_cleanup(self): self.check('repeat')
    def test_real_property_and_saved_value_restore_order(self): self.check('order')
    def test_expired_heartbeat_cannot_extend_or_write(self): self.check('expired')
    def test_live_heartbeat_does_not_cut_power_at_120_seconds(self): self.check('long')
    def test_stale_generation_and_queued_detach_refuse_supply(self): self.check('generation')
    def test_detach_between_current_and_vbus_prevents_enable(self): self.check('detach-between')
    def test_error_poison_blocks_cleanup_retry_and_rearm(self): self.check('poison')
    def test_kernel_detach_worker_restores_owned_values(self): self.check('detach')
    def test_manual_and_automatic_owners_are_exclusive(self): self.check('exclusive')
    def test_subscription_and_exact_phone_are_required(self): self.check('subscribe')
    def test_old_queued_watchdog_reschedules_renewed_deadline(self): self.check('early-watchdog')
    def test_process_death_watchdog_restores_all_owned_values(self): self.check('crash-watchdog')
    def test_exact_notification_header_length_and_full_width(self): self.check('decode')
    def test_status_exports_current_generation_and_health(self): self.check('status')


if __name__=='__main__':unittest.main()
