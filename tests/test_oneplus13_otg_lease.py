# SPDX-License-Identifier: GPL-2.0-only
"""Compile the shipped fixed lease policy; simulated transport, no device I/O."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "otg-protocol.h"
#include "otg-lease.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x);exit(1); } } while (0)
struct step { char op; unsigned int prop, value; };
struct fake {
 unsigned int ap,sw,calls,fail_at,expire_at,mismatch_at,poison_at,poisoned,sends;
 struct step trace[32];
};
static int transact(struct fake *f, char op, unsigned int p, unsigned int *v) {
 CHECK(p==OP13_OTG_AP_ENABLE || p==OP13_OTG_SWITCH);
 CHECK(f->calls<32);f->trace[f->calls++]=(struct step){op,p,*v};
 if(f->poisoned)return -1;
 f->sends++;
 if(op=='s') { if(p==OP13_OTG_AP_ENABLE)f->ap=*v;else f->sw=*v; }
 if(f->calls==f->poison_at)f->poisoned=1;
 if(f->calls==f->fail_at || f->poisoned)return -1;
 if(op=='g')*v=p==OP13_OTG_AP_ENABLE?f->ap:f->sw;
 if(op=='g' && f->calls==f->mismatch_at)*v^=1;
 return 0;
}
static int get(void *c,unsigned int p,unsigned int *v){*v=0;return transact(c,'g',p,v);}
static int set(void *c,unsigned int p,unsigned int v){CHECK(v<=1);return transact(c,'s',p,&v);}
static int expired(void *c){struct fake *f=c;return f->expire_at && f->calls>=f->expire_at;}
static struct op13_otg_lease_io io(struct fake *f){return (struct op13_otg_lease_io){f,get,set,expired};}
static void started(struct op13_otg_lease *l,struct fake *f){
 struct op13_otg_lease_io x=io(f);
 CHECK(!op13_otg_lease_start(l,&x));CHECK(f->calls==8 && f->ap==1 && f->sw==1);
 CHECK(l->state==OP13_OTG_LEASE_ACTIVE && l->used && l->dirty_ap && l->dirty_switch);
}
int main(int argc,char **argv){
 CHECK(argc==2);struct op13_otg_lease l={0};struct fake f={0};struct op13_otg_lease_io x=io(&f);
 if(!strcmp(argv[1],"success")) {
  started(&l,&f);CHECK(!op13_otg_lease_restore(&l,&x));
  const struct step want[]={{'g',17,0},{'g',18,0},{'s',17,1},{'g',17,0},{'s',18,1},{'g',18,0},
                            {'g',17,0},{'g',18,0},{'s',18,0},{'g',18,0},
                            {'s',17,0},{'g',17,0},{'g',17,0},{'g',18,0}};
  CHECK(f.calls==14);for(unsigned int i=0;i<14;i++)CHECK(f.trace[i].op==want[i].op && f.trace[i].prop==want[i].prop && f.trace[i].value==want[i].value);
  CHECK(!f.ap && !f.sw && !l.dirty_ap && !l.dirty_switch && l.state==OP13_OTG_LEASE_RESTORED);
 } else if(!strcmp(argv[1],"baseline")) {
  for(unsigned int a=0;a<2;a++)for(unsigned int s=0;s<2;s++)if(a||s){
   l=(struct op13_otg_lease){0};f=(struct fake){.ap=a,.sw=s};x=io(&f);
   CHECK(op13_otg_lease_start(&l,&x));CHECK(l.state==OP13_OTG_LEASE_REFUSED && f.calls==2);
   CHECK(!op13_otg_lease_restore(&l,&x) && f.calls==2 && l.state==OP13_OTG_LEASE_REFUSED);
  }
 } else if(!strcmp(argv[1],"baseline-errors")) {
  for(unsigned int at=1;at<=2;at++){
   l=(struct op13_otg_lease){0};f=(struct fake){.fail_at=at};x=io(&f);
   CHECK(op13_otg_lease_start(&l,&x));CHECK(f.calls==at && !l.dirty_ap && !l.dirty_switch);
  }
 } else if(!strcmp(argv[1],"start-errors")) {
  for(unsigned int at=3;at<=8;at++){
   l=(struct op13_otg_lease){0};f=(struct fake){.fail_at=at};x=io(&f);
   CHECK(op13_otg_lease_start(&l,&x));CHECK(!f.ap && !f.sw);
   CHECK(l.state==OP13_OTG_LEASE_RESTORED && !l.dirty_ap && !l.dirty_switch);
  }
 } else if(!strcmp(argv[1],"readback")) {
  for(unsigned int at=4;at<=8;at+=2){
   l=(struct op13_otg_lease){0};f=(struct fake){.mismatch_at=at};x=io(&f);
   CHECK(op13_otg_lease_start(&l,&x));CHECK(!f.ap && !f.sw && l.state==OP13_OTG_LEASE_RESTORED);
  }
 } else if(!strcmp(argv[1],"expiry")) {
  for(unsigned int at=2;at<=6;at+=2){
   l=(struct op13_otg_lease){0};f=(struct fake){.expire_at=at};x=io(&f);
   CHECK(op13_otg_lease_start(&l,&x));CHECK(!f.ap && !f.sw);
   CHECK(l.state==(at==2?OP13_OTG_LEASE_REFUSED:OP13_OTG_LEASE_RESTORED));
  }
 } else if(!strcmp(argv[1],"poison")) {
  for(unsigned int at=3;at<=8;at++){
   l=(struct op13_otg_lease){0};f=(struct fake){.poison_at=at};x=io(&f);
   CHECK(op13_otg_lease_start(&l,&x));CHECK(l.state==OP13_OTG_LEASE_UNKNOWN && f.sends==at);
   unsigned int calls=f.calls;CHECK(op13_otg_lease_restore(&l,&x));CHECK(f.calls==calls);
  }
 } else if(!strcmp(argv[1],"restore-errors")) {
  for(unsigned int at=9;at<=14;at++){
   l=(struct op13_otg_lease){0};f=(struct fake){0};x=io(&f);started(&l,&f);f.fail_at=at;
   CHECK(op13_otg_lease_restore(&l,&x));CHECK(l.state==OP13_OTG_LEASE_UNKNOWN);
   unsigned int calls=f.calls;CHECK(op13_otg_lease_restore(&l,&x));CHECK(f.calls==calls);
  }
 } else if(!strcmp(argv[1],"restore-readback")) {
  started(&l,&f);f.mismatch_at=10;CHECK(op13_otg_lease_restore(&l,&x));
  CHECK(l.state==OP13_OTG_LEASE_UNKNOWN && l.dirty_switch && l.dirty_ap && f.calls==12);
 } else if(!strcmp(argv[1],"no-reacquire")) {
  started(&l,&f);CHECK(op13_otg_lease_start(&l,&x) && f.calls==8);
  CHECK(!op13_otg_lease_restore(&l,&x));CHECK(op13_otg_lease_start(&l,&x) && f.calls==14);
  CHECK(!op13_otg_lease_restore(&l,&x) && f.calls==14);
 } else if(!strcmp(argv[1],"idle")) {
  CHECK(!op13_otg_lease_restore(&l,&x) && !f.calls && !l.used && !l.state);
 } else CHECK(0);
 return 0;
}
'''


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc'),
                     'Actual lease policy harness requires Linux GCC')
class OtgLeaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='op13-otg-lease-')
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / 'lease.c'
        source.write_text(HARNESS)
        cls.binary = source.with_suffix('')
        result = subprocess.run(['gcc', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
            '-I', str(ROOT/'tools/oneplus13/usb-draft'), str(source), '-o', str(cls.binary)],
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr)

    def check(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True,
            timeout=5, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1'})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_success_order_and_reverse_verified_restore(self): self.check('success')
    def test_nonzero_baseline_refuses_without_write(self): self.check('baseline')
    def test_baseline_errors_never_mutate(self): self.check('baseline-errors')
    def test_each_start_failure_restores_dirty_properties(self): self.check('start-errors')
    def test_start_get_readback_required(self): self.check('readback')
    def test_expiry_at_each_start_boundary(self): self.check('expiry')
    def test_ambiguous_transport_poison_is_terminal_unknown(self): self.check('poison')
    def test_restore_failure_is_terminal_and_other_property_attempted(self): self.check('restore-errors')
    def test_restore_readback_mismatch_is_not_clean(self): self.check('restore-readback')
    def test_only_one_acquisition_and_no_repeat_rollback(self): self.check('no-reacquire')
    def test_default_idle_cleanup_has_no_requests(self): self.check('idle')


if __name__ == '__main__':
    unittest.main()
