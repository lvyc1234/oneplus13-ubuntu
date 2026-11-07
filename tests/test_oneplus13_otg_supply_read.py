# SPDX-License-Identifier: GPL-2.0-only
"""Compile the real GET-only decoder and patched callback/sysfs methods."""
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
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "otg-protocol.h"
#include "otg-supply-read.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); exit(1); } } while (0)
#define le32_to_cpu(x) (x)
#define BATTMGR_NOTIFICATION 7U
#define QCOM_BATTMGR_SC8280XP 1
#define QCOM_BATTMGR_X1E80100 2
#define __ATTR(name, mode, show, store) { {mode}, show, store }
struct pmic_glink_hdr {unsigned int owner,type,opcode;};
struct completion {unsigned int done;};
struct attribute {unsigned short mode;};
struct device {void *data;};
struct kobject {int dummy;};
typedef long ssize_t;
typedef unsigned short umode_t;
struct device_attribute {struct attribute attr; ssize_t (*show)(struct device *,struct device_attribute *,char *); void *store;};
struct qcom_battmgr {
 int op13_otg_lock,op13_otg_error,lock,variant;
 bool op13_otg_pending;
 unsigned int op13_otg_opcode,op13_otg_property,op13_otg_value;
 struct completion op13_otg_ack;
};
static bool phone=true,oneplus13_otg_supply_read,oneplus13_otg_lease_enabled;
static unsigned int spins,completions,ordinary,notifications,transfers,transfer_opcode,transfer_property;
static int transfer_error,mutex_depth;
static bool op13_otg_machine(void){return phone;}
static void spin_lock_irqsave_impl(int *p,unsigned long *flags){(void)p;*flags=0;CHECK(!spins);spins++;}
static void spin_unlock_irqrestore_impl(int *p,unsigned long flags){(void)p;(void)flags;CHECK(spins==1);spins--;}
#define spin_lock_irqsave(p,f) spin_lock_irqsave_impl(p,&f)
#define spin_unlock_irqrestore(p,f) spin_unlock_irqrestore_impl(p,f)
static void complete(struct completion *c){c->done++;completions++;}
static void qcom_battmgr_notification(struct qcom_battmgr *b,const void *p,size_t n){(void)b;(void)p;(void)n;notifications++;CHECK(!spins);}
static void qcom_battmgr_sc8280xp_callback(struct qcom_battmgr *b,const void *p,size_t n){(void)b;(void)p;(void)n;ordinary++;CHECK(!spins);}
static void qcom_battmgr_sm8350_callback(struct qcom_battmgr *b,const void *p,size_t n){(void)b;(void)p;(void)n;ordinary++;CHECK(!spins);}
static void mutex_lock(int *p){(void)p;CHECK(!mutex_depth);mutex_depth++;}
static void mutex_unlock(int *p){(void)p;CHECK(mutex_depth==1);mutex_depth--;}
static void *dev_get_drvdata(struct device *d){return d->data;}
#define sysfs_emit snprintf_emit
static int snprintf_emit(char *buf,const char *format,unsigned int value){return sprintf(buf,format,value);}
static int op13_otg_transfer(struct qcom_battmgr *b,unsigned int op,unsigned int prop,unsigned int *value){
 (void)b;CHECK(mutex_depth==1);CHECK(op==OP13_OTG_GET);transfers++;transfer_opcode=op;transfer_property=prop;*value=prop==24?0:1250;return transfer_error;
}
static struct device_attribute dev_attr_oneplus13_otg_lease={{0600},NULL,NULL};
@FUNCTIONS@
static void word(unsigned char *p,unsigned int v){for(unsigned int i=0;i<4;i++)p[i]=(unsigned char)(v>>(8*i));}
static void packet(unsigned char *p,unsigned int op,unsigned int prop,unsigned int value,unsigned int result){
 word(p,32778);word(p+4,1);word(p+8,op);word(p+12,prop);word(p+16,value);word(p+20,result);
}
static void decode_tests(void){
 unsigned char bytes[25]; unsigned char *p=bytes;unsigned int out=0xabcdef01;
 unsigned int props[]={24,64};
 for(unsigned int k=0;k<2;k++){
  unsigned int prop=props[k];
  for(unsigned int value=0;value<2;value++){
   packet(p,0x32,prop,value,0);out=99;
   CHECK(op13_otg_supply_read_decode(p,24,0x32,prop,&out)==1 && out==value);
  }
  packet(p,0x32,prop,prop==24?0:1250,0);
  CHECK(op13_otg_decode(p,24,0x32,prop,&out)==0);
  CHECK(op13_otg_supply_read_decode(p,24,0x33,prop,&out)==0);
  for(unsigned int bit=0;bit<128;bit++){
   packet(p,0x32,prop,0,0);p[bit/8]^=1U<<(bit%8);out=99;
   CHECK(op13_otg_supply_read_decode(p,24,0x32,prop,&out)==0 && out==99);
  }
  for(unsigned int bit=0;bit<32;bit++){
   packet(p,0x32,prop,0,1U<<bit);out=99;
   CHECK(op13_otg_supply_read_decode(p,24,0x32,prop,&out)==-1 && out==99);
  }
  for(unsigned int n=0;n<24;n++){
   unsigned char *shortp=malloc(n?n:1);CHECK(shortp);memset(shortp,0,n?n:1);out=99;
   CHECK(op13_otg_supply_read_decode(shortp,n,0x32,prop,&out)==0 && out==99);free(shortp);
  }
  CHECK(op13_otg_supply_read_decode(p,25,0x32,prop,&out)==0);
 }
 for(unsigned int bit=1;bit<32;bit++){
  packet(p,0x32,24,1U<<bit,0);out=99;
  CHECK(op13_otg_supply_read_decode(p,24,0x32,24,&out)==-1 && out==99);
  packet(p,0x32,64,1U<<bit,0);
  CHECK(op13_otg_supply_read_decode(p,24,0x32,64,&out)==1 && out==(1U<<bit));
 }
 packet(p+1,0x32,64,0xffffffffU,0);out=99;
 CHECK(op13_otg_supply_read_decode(p+1,24,0x32,64,&out)==1 && out==0xffffffffU);
 CHECK(op13_otg_supply_read_decode(NULL,24,0x32,64,&out)==0);
 CHECK(op13_otg_supply_read_decode(p,24,0x32,64,NULL)==0);
 word(p,32778);word(p+4,2);word(p+8,7);
 for(unsigned int event=0;event<0x400;event++){
  word(p+12,event);CHECK(op13_otg_supply_notification(p,16)==((event==0x50 || event==0x51)?event:0));
 }
 CHECK(op13_otg_supply_notification(NULL,16)==0);
 for(unsigned int n=0;n<16;n++){
  unsigned char *shortp=malloc(n?n:1);CHECK(shortp);memset(shortp,0,n?n:1);
  CHECK(op13_otg_supply_notification(shortp,n)==0);free(shortp);
 }
 CHECK(op13_otg_supply_notification(p,17)==0);
}
static void callback_tests(void){
 struct qcom_battmgr b={0};unsigned char p[24];
 oneplus13_otg_supply_read=true;
 packet(p,0x32,64,1250,0);b.op13_otg_pending=true;b.op13_otg_opcode=0x32;b.op13_otg_property=64;
 qcom_battmgr_callback(p,24,&b);CHECK(!b.op13_otg_pending && completions==1 && b.op13_otg_value==1250 && !ordinary);
 qcom_battmgr_callback(p,24,&b);CHECK(completions==1 && !ordinary);
 b.op13_otg_pending=true;b.op13_otg_opcode=0x32;b.op13_otg_property=24;b.op13_otg_value=77;
 packet(p,0x32,64,1250,0);qcom_battmgr_callback(p,24,&b);CHECK(b.op13_otg_pending && completions==1);
 packet(p,0x32,24,0,1);qcom_battmgr_callback(p,24,&b);CHECK(!b.op13_otg_pending && completions==2 && b.op13_otg_value==77 && b.op13_otg_error==-EREMOTEIO);
 b.op13_otg_pending=true;b.op13_otg_opcode=0x33;b.op13_otg_property=64;
 packet(p,0x33,64,0,0);qcom_battmgr_callback(p,24,&b);CHECK(b.op13_otg_pending && completions==2);
 word(p+8,7);qcom_battmgr_callback(p,16,&b);CHECK(b.op13_otg_pending && notifications==1 && !ordinary);
 b.op13_otg_pending=false;packet(p,0x32,24,0,0);qcom_battmgr_callback(p,24,&b);CHECK(!ordinary);
 packet(p,0x32,7,0,0);qcom_battmgr_callback(p,24,&b);CHECK(ordinary==1);
 for(unsigned int n=0;n<12;n++)qcom_battmgr_callback(p,n,&b);
 CHECK(ordinary==1);qcom_battmgr_callback(NULL,24,&b);CHECK(ordinary==1);
 phone=false;packet(p,0x32,64,0,0);qcom_battmgr_callback(p,24,&b);CHECK(ordinary==2);phone=true;
 oneplus13_otg_supply_read=false;b.op13_otg_pending=true;b.op13_otg_opcode=0x32;b.op13_otg_property=64;
 qcom_battmgr_callback(p,24,&b);CHECK(b.op13_otg_pending && completions==2);
 b.op13_otg_pending=false;
}
static void sysfs_tests(void){
 struct qcom_battmgr b={0};struct device dev={&b};char buf[32];
 CHECK(!oneplus13_otg_supply_read);
 CHECK(oneplus13_otg_vbus_enable_show(&dev,NULL,buf)==-EOPNOTSUPP && !transfers);
 CHECK(oneplus13_otg_boost_current_show(&dev,NULL,buf)==-EOPNOTSUPP && !transfers);
 CHECK(!op13_otg_visible(NULL,&dev_attr_oneplus13_otg_vbus_enable.attr,0));
 CHECK(!op13_otg_visible(NULL,&dev_attr_oneplus13_otg_boost_current.attr,0));
 CHECK(!op13_otg_visible(NULL,&dev_attr_oneplus13_otg_lease.attr,0));
 oneplus13_otg_supply_read=true;phone=false;
 CHECK(oneplus13_otg_vbus_enable_show(&dev,NULL,buf)==-EOPNOTSUPP && !transfers);
 CHECK(!op13_otg_visible(NULL,&dev_attr_oneplus13_otg_vbus_enable.attr,0));
 phone=true;
 CHECK(op13_otg_visible(NULL,&dev_attr_oneplus13_otg_vbus_enable.attr,0)==0400);
 CHECK(op13_otg_visible(NULL,&dev_attr_oneplus13_otg_boost_current.attr,0)==0400);
 CHECK(!dev_attr_oneplus13_otg_vbus_enable.store && !dev_attr_oneplus13_otg_boost_current.store);
 CHECK(oneplus13_otg_vbus_enable_show(&dev,NULL,buf)>0 && !strcmp(buf,"0\n"));CHECK(transfer_opcode==0x32 && transfer_property==24);
 CHECK(oneplus13_otg_boost_current_show(&dev,NULL,buf)>0 && !strcmp(buf,"1250\n"));CHECK(transfer_opcode==0x32 && transfer_property==64);
 transfer_error=-EHOSTDOWN;memset(buf,0,sizeof(buf));
 CHECK(oneplus13_otg_boost_current_show(&dev,NULL,buf)==-EHOSTDOWN && !buf[0]);
}
int main(void){decode_tests();callback_tests();sysfs_tests();CHECK(!mutex_depth && !spins);return 0;}
'''

def function(code, name):
    signature = code.index(name + '(')
    # Match this function's signature rather than an earlier definition.
    start = code.rfind('\nstatic ', 0, signature) + 1
    brace = code.index('{', signature)
    depth = 1
    end = brace + 1
    while depth:
        depth += (code[end] == '{') - (code[end] == '}')
        end += 1
    return code[start:end]

@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc'), 'Linux GCC required')
class SupplyReadTests(unittest.TestCase):
    def test_real_decoder_callback_and_sysfs_in_release_and_sanitizers(self):
        # Use the real source pinned by the repository, supplied by a fixture
        # extracted from the old public patch context. Retrieve no network input.
        with tempfile.TemporaryDirectory(prefix='op13-supply-read-') as temporary:
            directory = Path(temporary)
            # 0023 is a standalone diff against the already-reviewed 0022 driver.
            # Reconstruct its affected functions from unchanged context plus new
            # lines; reject a function split across hunks rather than guessing.
            patch = (ROOT/'devices/oneplus13/kernel-patches/0023-oneplus13-otg-supply-read.patch').read_text()
            groups = []
            for part in patch.split('\n@@ ')[1:]:
                body = part.split('\n', 1)[1]
                lines = []
                for line in body.splitlines():
                    if line.startswith((' ', '+')):
                        lines.append(line[1:])
                groups.append('\n'.join(lines))
            changed = '\n'.join(groups)
            # Full real callback/sysfs methods are captured with expanded diff
            # context so this test can execute their exact bodies offline in CI.
            names = ['qcom_battmgr_callback', 'op13_otg_show',
                     'oneplus13_otg_vbus_enable_show', 'oneplus13_otg_boost_current_show',
                     'op13_otg_visible']
            # Declarations refer to the exact show methods, no writable methods.
            declarations = changed[changed.index('static struct device_attribute dev_attr_oneplus13_otg_vbus_enable ='):]
            declarations = declarations[:declarations.index('static struct attribute *op13_otg_attrs[]')]
            order = '\n'.join(function(changed, name) for name in names[:-1]) + '\n' + declarations + '\n' + function(changed,names[-1])
            source = directory/'harness.c'
            source.write_text(HARNESS.replace('@FUNCTIONS@', order))
            for variant, flags in [('release',['-O2']),('sanitized',['-O1','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie'])]:
                binary = directory/variant
                built = subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-parameter',*flags,'-I',str(ROOT/'tools/oneplus13/usb-draft'),str(source),'-o',str(binary)],capture_output=True,text=True,timeout=30)
                self.assertEqual(built.returncode,0,built.stderr)
                ran = subprocess.run([str(binary)],capture_output=True,text=True,timeout=10,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1'})
                self.assertEqual(ran.returncode,0,ran.stderr)

if __name__ == '__main__':
    unittest.main()
