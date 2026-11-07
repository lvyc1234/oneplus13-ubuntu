# SPDX-License-Identifier: GPL-2.0-only
"""Exercise actual kernel lease store/work/remove using mocked kernel APIs."""
import os
import shutil
import sys
import unittest
from pathlib import Path
import subprocess
import tempfile

SOURCE=Path(__file__).resolve().parents[1]
HEADER=SOURCE/'tools/oneplus13/usb-draft/otg-kernel.h'
body='\n'.join(line for line in HEADER.read_text().splitlines() if not line.startswith('#include'))
HARNESS=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <stdarg.h>
#include "otg-protocol.h"
#include "otg-lease.h"
#define CHECK(x) do{if(!(x)){fprintf(stderr,"line %d: %s\n",__LINE__,#x);exit(1);}}while(0)
#define module_param_named(...)
#define MODULE_PARM_DESC(...)
#define HZ 100U
#define NSEC_PER_SEC 1000000000ULL
#define PMIC_GLINK_OWNER_BATTMGR 32778
#define PMIC_GLINK_REQ_RESP 1
#define cpu_to_le32(x) (x)
#define ATOMIC_INIT(x) (x)
typedef uint64_t u64;typedef long ssize_t;typedef int atomic_t;
struct completion{unsigned int done;};
struct work_struct{int tag;};
struct delayed_work{struct work_struct work;int scheduled;};
struct wakeup_source{int held,freed;};
struct device{void *data;};
struct device_attribute{int dummy;};
struct pmic_glink_hdr{unsigned int owner,type,opcode;};
struct qcom_battmgr_property_request{struct pmic_glink_hdr hdr;unsigned int battery,property,value;};
struct qcom_battmgr{
 int lock,op13_otg_lock,op13_otg_error;
 bool service_up,op13_otg_poisoned,op13_otg_pending,op13_otg_stopping,op13_otg_awake;
 unsigned int op13_otg_opcode,op13_otg_property,op13_otg_value;
 struct completion op13_otg_ack;struct op13_otg_lease op13_otg_lease;
 struct delayed_work op13_otg_restore_work;struct work_struct enable_work;
 u64 op13_otg_deadline;struct wakeup_source *op13_otg_wakeup;void *client;
};
static struct qcom_battmgr b;static struct wakeup_source wake;static struct device dev;
static unsigned int ap,sw,sends,schedules,cancels,drains,mode;
static u64 now;static int mutex_depth,spin_depth;static bool phone=true;
static int atomic_cmpxchg(atomic_t *p,int old,int next){int prior=*p;if(prior==old)*p=next;return prior;}
static void mutex_lock(int *p){(void)p;CHECK(!mutex_depth);mutex_depth++;}
static void mutex_unlock(int *p){(void)p;CHECK(mutex_depth==1);mutex_depth--;}
static void spin_lock_irq(int *p){(void)p;CHECK(!spin_depth);spin_depth++;}
static void spin_unlock_irq(int *p){(void)p;CHECK(spin_depth==1);spin_depth--;}
#define spin_lock_irqsave(p,f) do{(f)=0;spin_lock_irq(p);}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(f);spin_unlock_irq(p);}while(0)
static void reinit_completion(struct completion *c){c->done=0;}
static unsigned long wait_for_completion_timeout(struct completion *c,unsigned long t){CHECK(t==HZ);return c->done?1:0;}
static u64 ktime_get_boottime_ns(void){return now;}
static bool op13_otg_machine(void){return phone;}
static void __pm_stay_awake(struct wakeup_source *w){CHECK(w && !w->held && !w->freed);w->held=1;}
static void __pm_relax(struct wakeup_source *w){CHECK(w && w->held && !w->freed);w->held=0;}
static void wakeup_source_unregister(struct wakeup_source *w){CHECK(w && !w->held && !w->freed);w->freed=1;}
static bool schedule_delayed_work(struct delayed_work *w,unsigned long delay){CHECK(delay==120*HZ);CHECK(mutex_depth==1);CHECK(wake.held);if(w->scheduled)return false;w->scheduled=1;schedules++;return true;}
static void cancel_delayed_work_sync(struct delayed_work *w){CHECK(!mutex_depth);w->scheduled=0;cancels++;}
static void cancel_work_sync(struct work_struct *w){(void)w;CHECK(!mutex_depth);drains++;}
static void *dev_get_drvdata(struct device *d){return d->data;}
static int sysfs_streq(const char *a,const char *b){size_t n=strlen(a);if(n && a[n-1]=='\n')n--;return strlen(b)==n && !memcmp(a,b,n);}
static int sysfs_emit(char *out,const char *format,...){va_list ap;va_start(ap,format);int n=vsprintf(out,format,ap);va_end(ap);return n;}
#define __ATTR(name,mode,show,store) {0}
#define to_delayed_work(w) ((struct delayed_work *)((char *)(w)-offsetof(struct delayed_work,work)))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
static int pmic_glink_send(void *client,const void *p,size_t n){
 (void)client;const struct qcom_battmgr_property_request *r=p;
 CHECK(n==sizeof(*r));CHECK(mutex_depth==1 && !spin_depth);
 CHECK(b.op13_otg_pending && b.service_up && !b.op13_otg_poisoned);
 CHECK(r->property==17 || r->property==18);
 if(r->hdr.opcode==OP13_OTG_SET){CHECK(schedules==1);CHECK(wake.held);CHECK(r->value<=1);CHECK(r->property==17?b.op13_otg_lease.dirty_ap:b.op13_otg_lease.dirty_switch);if(r->property==17)ap=r->value;else sw=r->value;}
 sends++;if(mode==1)return -EIO;
 if(mode==2 && r->hdr.opcode==OP13_OTG_SET){b.op13_otg_error=-EREMOTEIO;}else b.op13_otg_error=0;
 b.op13_otg_value=r->hdr.opcode==OP13_OTG_GET?(r->property==17?ap:sw):0;
 b.op13_otg_pending=false;b.op13_otg_ack.done++;return 0;
}
''' + body + r'''
static ssize_t command(const char *s){return oneplus13_otg_lease_store(&dev,NULL,s,strlen(s));}
static void setup(void){b=(struct qcom_battmgr){.service_up=true,.op13_otg_wakeup=&wake};wake=(struct wakeup_source){0};dev.data=&b;}
int main(int argc,char **argv){
 CHECK(argc==2);setup();char status[128];
 CHECK(dev_attr_oneplus13_otg_lease.dummy==0);
 if(!strcmp(argv[1],"disabled-no-traffic")){CHECK(command("start")<0 && !sends && !schedules);}
 else if(!strcmp(argv[1],"wrong-phone-no-traffic")){oneplus13_otg_lease_enabled=true;phone=false;CHECK(command("start")<0 && !sends && !schedules);}
 else if(!strcmp(argv[1],"invalid-no-traffic")){oneplus13_otg_lease_enabled=true;CHECK(command("start123")<0 && !sends && !schedules);}
 else if(!strcmp(argv[1],"start-store-manual-restore")){oneplus13_otg_lease_enabled=true;CHECK(command("start\n")==6);CHECK(sends==8 && wake.held && b.op13_otg_lease.state==1);CHECK(command("restore\n")==8);CHECK(sends==14 && !wake.held && b.op13_otg_lease.state==2);CHECK(oneplus13_otg_lease_show(&dev,NULL,status)>0);CHECK(!strcmp(status,"state=2 used=1 dirty_ap=0 dirty_switch=0\n"));}
 else if(!strcmp(argv[1],"watchdog-restores")){oneplus13_otg_lease_enabled=true;CHECK(command("start")>0);now=121*NSEC_PER_SEC;op13_otg_restore_worker(&b.op13_otg_restore_work.work);CHECK(sends==14 && !wake.held && b.op13_otg_lease.state==2);}
 else if(!strcmp(argv[1],"remove-restores-drains")){oneplus13_otg_lease_enabled=true;CHECK(command("start")>0);op13_otg_lease_remove(&b);CHECK(sends==14 && !wake.held && wake.freed && b.op13_otg_stopping && cancels==1 && drains==1);CHECK(command("start")<0);}
 else if(!strcmp(argv[1],"idle-remove-no-traffic")){op13_otg_lease_remove(&b);CHECK(!sends && !schedules && wake.freed && cancels==1 && drains==1);}
 else if(!strcmp(argv[1],"rebind-latch")){oneplus13_otg_lease_enabled=true;CHECK(command("start")>0);CHECK(command("restore")>0);unsigned int before=sends;setup();CHECK(command("start")<0 && sends==before);}
 else if(!strcmp(argv[1],"poison-watchdog-no-retry")){oneplus13_otg_lease_enabled=true;CHECK(command("start")>0);mode=1;CHECK(command("restore")<0);CHECK(b.op13_otg_lease.state==3 && b.op13_otg_poisoned);unsigned int before=sends;op13_otg_restore_worker(&b.op13_otg_restore_work.work);CHECK(sends==before && !wake.held);}
 else if(!strcmp(argv[1],"firmware-error-ack-no-rollback")){oneplus13_otg_lease_enabled=true;mode=2;CHECK(command("start")<0);CHECK(sends==3 && b.op13_otg_poisoned && b.op13_otg_lease.state==3);unsigned int before=sends;CHECK(command("restore")<0);op13_otg_restore_worker(&b.op13_otg_restore_work.work);CHECK(sends==before && !wake.held);}
 else if(!strcmp(argv[1],"expired-set-no-enable")){oneplus13_otg_lease_enabled=true;now=100; b.op13_otg_deadline=100;mutex_lock(&b.lock);CHECK(op13_otg_lease_set(&b,17,1)==-ETIME && !sends);mutex_unlock(&b.lock);}
 else if(!strcmp(argv[1],"refused-preserved-watchdog")){oneplus13_otg_lease_enabled=true;ap=1;CHECK(command("start")<0 && sends==2 && !wake.held);op13_otg_restore_worker(&b.op13_otg_restore_work.work);CHECK(sends==2 && b.op13_otg_lease.state==4);}
 else CHECK(0);
 CHECK(!mutex_depth && !spin_depth);return 0;
}
'''
cases=['disabled-no-traffic','wrong-phone-no-traffic','invalid-no-traffic','start-store-manual-restore','watchdog-restores','remove-restores-drains','idle-remove-no-traffic','rebind-latch','poison-watchdog-no-retry','firmware-error-ack-no-rollback','expired-set-no-enable','refused-preserved-watchdog']

@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc'),
                     'Actual kernel scaffold harness requires Linux GCC')
class OtgKernelScaffoldTests(unittest.TestCase):
    def test_negative_control_error_ack_poison_order(self):
        corrected = ('\tif (!ret)\n\t\tret = battmgr->op13_otg_error;\n'
                     '\tif (ret)\n\t\tbattmgr->op13_otg_poisoned = true;')
        original = ('\tif (ret)\n\t\tbattmgr->op13_otg_poisoned = true;\n'
                    '\telse\n\t\tret = battmgr->op13_otg_error;')
        self.assertIn(corrected, HARNESS)
        with tempfile.TemporaryDirectory(prefix='op13-error-ack-control-') as temporary:
            directory = Path(temporary)
            source, binary = directory/'old.c', directory/'old'
            source.write_text(HARNESS.replace(corrected, original))
            compiled = subprocess.run(['gcc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', '-O2', '-I', str(SOURCE/'tools/oneplus13/usb-draft'),
                str(source), '-o', str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            ran = subprocess.run([str(binary), 'firmware-error-ack-no-rollback'],
                                 capture_output=True, text=True, timeout=5)
            self.assertNotEqual(ran.returncode, 0, 'Old callback-error ordering must fail')

    def test_actual_store_watchdog_remove_in_release_and_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix='op13-kernel-scaffold-') as temporary:
            directory = Path(temporary)
            source = directory/'scaffold.c'
            source.write_text(HARNESS)
            variants = [('release', ['-O2']), ('asan-ubsan', ['-O1',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie'])]
            for variant, extra in variants:
                binary = directory/variant
                compiled = subprocess.run(['gcc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', *extra, '-I', str(SOURCE/'tools/oneplus13/usb-draft'),
                    str(source), '-o', str(binary)], capture_output=True, text=True, timeout=30)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                for case in cases:
                    with self.subTest(variant=variant, case=case):
                        ran = subprocess.run([str(binary), case], capture_output=True, text=True,
                            timeout=5, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1'})
                        self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == '__main__': unittest.main()
