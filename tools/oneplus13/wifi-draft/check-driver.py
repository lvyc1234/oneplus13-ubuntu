#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the actual SAR driver code with a fake transport, not a mirrored model."""
import argparse,hashlib,importlib.util,json,os,platform,re,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--references',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve()
    if out.exists():raise ValueError('Preserve failed/previous evidence')
    out.mkdir(parents=True)
    spec=importlib.util.spec_from_file_location('codec_check',HERE/'check.py');check=importlib.util.module_from_spec(spec);spec.loader.exec_module(check)
    lock=json.loads((HERE/'sources.json').read_text());name='wmi_unified.h';data=(a.references/name).read_bytes()
    if hashlib.sha256(data).hexdigest()!=lock['files'][name]['sha256']:raise ValueError('Wrong vendor ID oracle')
    text=data.decode();license_text=re.search(r'/\*.*?\*/',text,re.S)[0]
    enums=[check.enum(text,x) for x in ['WMI_GRP_START','WMI_INIT_CMDID','WMI_SERVICE_READY_EVENTID']]
    (out/'vendor-ids.h').write_text(license_text+'\n#define WMI_CMD_GRP_START_ID(g) (((g)<<12)|1)\n#define WMI_EVT_GRP_START_ID(g) (((g)<<12)|1)\n'+'\n'.join(enums),newline='\n')
    header=re.sub(r'^#include <linux/[^>]+>\n','',(HERE/'phone-sar.h').read_text(),flags=re.M)
    shim=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
typedef uint32_t u32;
typedef uint16_t u16;
typedef uint32_t __le32;
typedef pthread_mutex_t spinlock_t;
struct mutex { pthread_mutex_t lock; };
struct completion { unsigned count; };
static void mutex_init(struct mutex *m) { assert(!pthread_mutex_init(&m->lock,NULL)); }
static int mutex_trylock(struct mutex *m) { return !pthread_mutex_trylock(&m->lock); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->lock)); }
static void spin_lock_init(spinlock_t *s) { assert(!pthread_mutex_init(s,NULL)); }
static void spin_lock_bh(spinlock_t *s) { assert(!pthread_mutex_lock(s)); }
static void spin_unlock_bh(spinlock_t *s) { assert(!pthread_mutex_unlock(s)); }
static void init_completion(struct completion *c) { c->count=0; }
static void reinit_completion(struct completion *c) { c->count=0; }
static void complete(struct completion *c) { c->count++; }
/* Deterministic already-delivered response or timeout: no wall clock sleeps. */
static int wait_for_completion_timeout(struct completion *c, unsigned long ticks)
{ (void)ticks; if(!c->count)return 0; c->count--;return 1; }
static u32 get_unaligned_le32(const void *v)
{ const unsigned char *p=v;return (u32)p[0]|(u32)p[1]<<8|(u32)p[2]<<16|(u32)p[3]<<24; }
#define msecs_to_jiffies(x) (x)
#define lockdep_assert_held(x) ((void)(x))
#define GFP_KERNEL 0
#define kmalloc(n,flags) malloc(n)
#define kfree(p) free(p)
'''
    shim+=header+r'''
struct fake_pdev { unsigned unused; };
struct ath12k_wmi_base { struct ath12k_phone_sar phone_sar;struct fake_pdev wmi[1]; };
struct ath12k_base { struct ath12k_wmi_base wmi_ab;unsigned core_lock; };
struct sk_buff { unsigned char *data; };
struct sk_buff *ath12k_wmi_alloc_skb(struct ath12k_wmi_base *,size_t);
void dev_kfree_skb(struct sk_buff *);
int ath12k_wmi_cmd_send(struct fake_pdev *,struct sk_buff *,u32);
'''
    (out/'host-driver.h').write_text(shim,newline='\n')
    actual=re.sub(r'^#include [<"].*[>"]\n','',(HERE/'phone-sar.c').read_text(),flags=re.M)
    (out/'driver-under-test.c').write_text(actual,newline='\n')
    log=out/'build.log';counts={}
    for config,flags in [('release',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
        exe=out/('test-driver-'+config)
        command=['gcc','-std=c11','-Wall','-Wextra','-Werror','-pthread',*flags,'-I'+str(HERE),'-I'+str(out),str(HERE/'sar-wire.c'),str(HERE/'test-driver.c'),'-o',str(exe)]
        for argv in [command,[str(exe)]]:
            r=subprocess.run(argv,capture_output=True,timeout=60,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1'))
            with log.open('ab') as f:f.write(json.dumps(argv).encode()+b'\n'+r.stdout+r.stderr)
            if r.returncode:raise RuntimeError('Actual driver check failed; see build.log')
        m=re.search(rb'driver_groups=(\d+) actual_driver_code=yes',r.stdout)
        if not m:raise ValueError('No driver test completion')
        counts[config]=int(m[1])
    proof={'scope':'ACTUAL_DRIVER_SYNTHETIC_TRANSPORT_ONLY','machine':platform.machine(),'groups':counts,'phone_operations':0,
           'actual_driver_code_executed':True,'vendor_id_static_assertions':4,'asan_ubsan_lsan':True,'real_kernel_scheduler_races_tested':False,
           'source_files':{n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ['phone-sar.c','phone-sar.h','sar-wire.c','sar-wire.h','check-driver.py','test-driver.c']},
           'power_policy_or_rf_qualified':False,'current_wifi_qualified':False}
    (out/'verification.json').write_text(json.dumps(proof,indent=2)+'\n',newline='\n');print(json.dumps(proof,indent=2))

if __name__=='__main__':main()
