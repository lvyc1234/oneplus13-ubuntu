#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual patched ath12k helpers with fault-injected host boundaries.

This tests request ownership and batching, not firmware/event scheduling.
Full kernel compilation and bounded hardware acceptance remain required.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import resource
import signal


def function(source, name):
    import re
    match = re.search(r'\n(?:static )?[^;{}]*\b' + name + r'\([^;{}]*\)\n\{', source)
    if not match:
        raise ValueError('Missing actual implementation: ' + name)
    start = match.start() + 1
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


SHIM = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define ATH12K_HW_WCN7860_HW20 20
#define WMI_HOST_HW_MODE_DBS_OR_SBS 4
#define WMI_HOST_WLAN_5GHZ_CAP 2
#define ATH12K_FLUSH_TIMEOUT 20
#define lockdep_assert_wiphy(x) ((void)(x))
#define spin_lock_bh(x) ((void)(x))
#define spin_unlock_bh(x) ((void)(x))
#define lockdep_assert_held(x) ((void)(x))
#define fallthrough __attribute__((fallthrough))
#define ath12k_warn(...) ((void)0)
enum {ATH12K_SCAN_IDLE,ATH12K_SCAN_STARTING,ATH12K_SCAN_RUNNING,ATH12K_SCAN_ABORTING};
struct ath12k_svc_ext_mac_phy_info {
 u32 phy_id, hw_mode_config_type, supported_bands;
 struct {u32 low_5ghz_freq, high_5ghz_freq;} hw_freq_range;
};
struct ath12k_base {
 int hw_rev, num_radios;
 struct {bool single_pdev_only;} *hw_params;
 struct {int preferred_hw_mode; struct {
  struct ath12k_svc_ext_mac_phy_info mac_phy_info[4];
 } svc_ext_info;} wmi_ab;
};
struct ieee80211_hw {void *wiphy;};
struct ath12k_wmi_scan_req_arg {
 u32 num_chan, min_rest_time; unsigned char ssids_and_flags[64];
 struct {void *ptr;} extraie;
 u32 chan_list[];
};
struct ath12k {
 struct ath12k_base *ab;
 struct {int num_radio; struct ieee80211_hw *hw;} *ah;
 int data_lock;
 struct {int tx_empty_waitq, num_tx_pending;} dp;
 struct {struct ath12k_wmi_scan_req_arg *range_req;
 u32 range_next, range_split_freq; bool range_tx_paused; int range_trace_count;
 int state,completed,vdev_clean_wk; bool is_roc,roc_notify,finish_queued;
 struct {bool is_up;} *arvif;} scan;
};
static struct ieee80211_hw hw;
static int alloc_fail, allocations, frees, stopped, wakes, pauses, waits;
static int start_rc, starts, observed_n; static u32 observed[32];
static int stops,queued;
static void complete_all(int *c) {(*c)++;}
static void wiphy_work_queue(void *w,int *work) {(void)w;(void)work;queued++;}
static void ieee80211_remain_on_channel_expired(struct ieee80211_hw *h) {(void)h;}
static int ath12k_scan_stop(struct ath12k *ar) {(void)ar;stops++;return 0;}
static struct ieee80211_hw *ath12k_ar_to_hw(struct ath12k *ar) {(void)ar; return &hw;}
static void *allocate(size_t n) {if (alloc_fail) return NULL; allocations++; return calloc(1,n);}
#define kzalloc_flex(obj,member,n) allocate(sizeof(obj)+sizeof((obj).member[0])*(n))
static void kfree(void *p) {if(p) frees++; free(p);}
static int ieee80211_queue_stopped(struct ieee80211_hw *h,int q) {(void)h;(void)q;return stopped;}
static void ieee80211_stop_queues(struct ieee80211_hw *h) {(void)h;stopped=1;pauses++;}
static void ieee80211_wake_queues(struct ieee80211_hw *h) {(void)h;stopped=0;wakes++;}
static void synchronize_net(void) {}
static void msleep(u32 n) {assert(n==7); waits++;}
#define atomic_read(p) (*(p))
#define atomic_inc_return(p) (++(*(p)))
#define ath12k_info(...) ((void)0)
#define wait_event_timeout(q,condition,timeout) ((void)(q),(void)(timeout),(condition)?1:0)
static int ath12k_start_scan(struct ath12k *ar,struct ath12k_wmi_scan_req_arg *arg) {
 assert(ar->scan.range_req); assert(arg!=ar->scan.range_req);
 assert(!memcmp(arg->ssids_and_flags, ar->scan.range_req->ssids_and_flags,64));
 assert(arg->extraie.ptr==ar->scan.range_req->extraie.ptr);
 for(u32 i=0;i<arg->num_chan;i++) observed[observed_n++]=arg->chan_list[i];
 starts++;return start_rc;
}
'''

MAIN = r'''
int main(void) {
 struct ath12k_base ab={0}; struct ath12k ar={.ab=&ab};
 typeof(*ab.hw_params) params={.single_pdev_only=true}; ab.hw_params=&params;
 typeof(*ar.ah) ah={.num_radio=1,.hw=&hw}; ar.ah=&ah;
 typeof(*ar.scan.arvif) vif={0}; ar.scan.arvif=&vif;
 ab.hw_rev=20;ab.num_radios=1;ab.wmi_ab.preferred_hw_mode=4;
 for(int i=0;i<2;i++) {
  struct ath12k_svc_ext_mac_phy_info *p=&ab.wmi_ab.svc_ext_info.mac_phy_info[i];
  p->phy_id=i;p->hw_mode_config_type=4;p->supported_bands=2;
  p->hw_freq_range.low_5ghz_freq=5000;p->hw_freq_range.high_5ghz_freq=i?5885:5490;
 }
 assert(ath12k_mac_scan_split_freq(&ar)==5490);
 ab.hw_rev=19;assert(!ath12k_mac_scan_split_freq(&ar));ab.hw_rev=20;
 ab.num_radios=2;assert(!ath12k_mac_scan_split_freq(&ar));ab.num_radios=1;
 ah.num_radio=2;assert(!ath12k_mac_scan_split_freq(&ar));ah.num_radio=1;
 params.single_pdev_only=false;assert(!ath12k_mac_scan_split_freq(&ar));params.single_pdev_only=true;
 ab.wmi_ab.preferred_hw_mode=3;assert(!ath12k_mac_scan_split_freq(&ar));ab.wmi_ab.preferred_hw_mode=4;
 ab.wmi_ab.svc_ext_info.mac_phy_info[1].hw_freq_range.low_5ghz_freq=5500;
 assert(!ath12k_mac_scan_split_freq(&ar));
 ab.wmi_ab.svc_ext_info.mac_phy_info[1].hw_freq_range.low_5ghz_freq=5000;
 const u32 freqs[]={2412,2437,5180,5490,5500,5885,5955,2412};
 const u32 counts[]={2,2,2,1,1};
 assert(ath12k_mac_scan_range_count(freqs,8,0)==8);
 assert(ath12k_mac_scan_range_count(freqs,0,5490)==0);
 for(int associated=0;associated<2;associated++) {
  struct ath12k_wmi_scan_req_arg *req=calloc(1,sizeof(*req)+sizeof(freqs));
  memcpy(req->chan_list,freqs,sizeof(freqs));req->num_chan=8;req->min_rest_time=7;
  memset(req->ssids_and_flags,0x5a,64);req->extraie.ptr=malloc(4);
  ar.scan.range_req=req;ar.scan.range_split_freq=5490;vif.is_up=associated;
  observed_n=starts=0;int batch=0;
  while(ar.scan.range_next<8) {
   u32 before=ar.scan.range_next;
   alloc_fail=1;assert(ath12k_mac_scan_range_start(&ar)==-ENOMEM);
   assert(ar.scan.range_next==before);alloc_fail=0;
   assert(!ath12k_mac_scan_range_start(&ar));
   assert(ar.scan.range_next-before==(associated?1:counts[batch]));batch++;
   ath12k_mac_scan_range_tx_resume(&ar);
  }
  assert(observed_n==8&&!memcmp(observed,freqs,sizeof(freqs)));
  assert(!memcmp(req->chan_list,freqs,sizeof(freqs)));
  assert(ath12k_mac_scan_range_start(&ar)==-EINVAL);
  int prior=frees;ath12k_mac_scan_range_free(&ar);
  assert(frees==prior+2&&!ar.scan.range_req&&!ar.scan.range_next&&!ar.scan.range_split_freq);
  ath12k_mac_scan_range_free(&ar);assert(frees==prior+2);
 }
 struct ath12k_wmi_scan_req_arg *req=calloc(1,sizeof(*req)+4);
 req->num_chan=1;req->min_rest_time=7;req->chan_list[0]=5240;
 ar.scan.range_req=req;ar.scan.range_split_freq=5490;vif.is_up=true;
 stopped=1;assert(ath12k_mac_scan_range_start(&ar)==-EBUSY);
 assert(!ar.scan.range_tx_paused);stopped=0;ar.scan.range_next=0;
 ar.dp.num_tx_pending=1;assert(ath12k_mac_scan_range_start(&ar)==-ETIMEDOUT);
 assert(!ar.scan.range_tx_paused&&!stopped);ar.dp.num_tx_pending=0;ar.scan.range_next=0;
 start_rc=-EIO;assert(ath12k_mac_scan_range_start(&ar)==-EIO);
 assert(!ar.scan.range_tx_paused&&!stopped);
 ath12k_mac_scan_range_free(&ar);assert(pauses==wakes&&waits>0);
 req=calloc(1,sizeof(*req)+8);req->num_chan=2;
 ar.scan.range_req=req;ar.scan.state=ATH12K_SCAN_RUNNING;ar.scan.range_next=1;
 __ath12k_mac_scan_finish(&ar);
 assert(queued==1&&!ar.scan.finish_queued&&ar.scan.completed==1);
 ar.scan.range_next=2;__ath12k_mac_scan_finish(&ar);
 assert(queued==2&&ar.scan.finish_queued&&ar.scan.completed==2);
 ath12k_scan_abort(&ar);assert(stops==0&&ar.scan.state==ATH12K_SCAN_RUNNING);
 ar.scan.finish_queued=false;ath12k_scan_abort(&ar);
 assert(stops==1&&ar.scan.state==ATH12K_SCAN_ABORTING);
 ath12k_mac_scan_range_free(&ar);
 return 0;
}
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--kernel-source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    source=(args.kernel_source/'drivers/net/wireless/ath/ath12k/mac.c').read_text()
    names=['ath12k_mac_scan_split_freq','ath12k_mac_scan_range_class',
           'ath12k_mac_scan_range_count','ath12k_mac_scan_range_tx_resume',
           'ath12k_mac_scan_range_tx_pause','ath12k_mac_scan_range_free','ath12k_mac_scan_range_start',
           '__ath12k_mac_scan_finish','ath12k_scan_abort']
    with tempfile.TemporaryDirectory(prefix='ath12k-scan-check-') as tmp:
        path=Path(tmp); unit=path/'check.c'
        actual=SHIM+'\n'+'\n'.join(function(source,n) for n in names)+'\n'+MAIN
        unit.write_text(actual)
        for label,flags in [('release',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
            binary=path/label
            subprocess.run(['gcc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-sign-compare',*flags,str(unit),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True,timeout=10)
        negative=actual.replace('\t\tif (ar->scan.finish_queued)\n\t\t\tbreak;','',1)
        if negative==actual:raise ValueError('Missing completion-race guard')
        unit.write_text(negative)
        binary=path/'negative'
        subprocess.run(['gcc','-std=gnu11','-O2',str(unit),'-o',str(binary)],check=True)
        result=subprocess.run([str(binary)],timeout=10,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                              preexec_fn=lambda:resource.setrlimit(resource.RLIMIT_CORE,(0,0)))
        if result.returncode!=-signal.SIGABRT:raise ValueError('Completion-race negative control failed to detect regression')
    report={'scope':'ACTUAL_ATH12K_SCAN_HELPERS_HOST_FAULT_INJECTION','pass':True,
            'release_and_asan_ubsan':True,'functions_compiled':names,
            'completion_race_negative_control_rejected':True,
            'checks':['hardware/mode isolation','malformed frequency ranges','channel/SSID/IE order',
                      'associated single-channel batches','allocation failure','owned request cleanup',
                      'TX busy/drain timeout/start failure and resume','final-completion versus timeout race'],
            'firmware_or_event_scheduler_validated':False,'hardware_acceptance':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
