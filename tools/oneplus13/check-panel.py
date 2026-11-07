#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile real AA569 component callbacks with host-only, fault-injected I/O."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--sanitize', action='store_true')
    p.add_argument('--keep-source', type=Path)
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    fixture = json.loads((repo/'devices/oneplus13/panel-aa569-120hz.json').read_text())
    panel_dir = repo/'devices/oneplus13/kernel/panel-aa569'
    source = (panel_dir/'panel-oneplus-aa569.c').read_text()
    generated = load('panel_generator', Path(__file__).with_name('generate-panel.py')).generate(fixture)
    if (panel_dir/'aa569-data.h').read_text() != generated:
        raise ValueError('Generated public AA569 header is stale')
    pin = json.loads((repo/'devices/oneplus13/source.json').read_text())['kernel']['commit']
    if subprocess.check_output(['git', '-C', str(a.kernel_source), 'rev-parse', 'HEAD'], text=True).strip() != pin:
        raise ValueError('Kernel header source does not match pin')
    header = subprocess.check_output(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
        '-C', str(a.kernel_source), 'show', pin+':include/drm/display/drm_dsc.h']).decode()
    header = header.replace('#include <drm/display/drm_dp.h>', '')
    extract = load('display_check', Path(__file__).with_name('check-display.py')).extract_function
    callbacks = '\n'.join(extract(source, x) for x in [
        'static struct aa569 *to_aa569(', 'static int aa569_send(',
        'static int aa569_backlight_update_status(',
        'static int aa569_power_off(', 'static int aa569_power_on(',
        'static int aa569_prepare(', 'static int aa569_enable(',
        'static int aa569_disable_locked(', 'static int aa569_disable(',
        'static int aa569_unprepare('])
    prefix = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint16_t __be16;
#define __packed __attribute__((packed))
struct dp_sdp_header {u8 bytes[4];};
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define MIPI_DSI_MODE_LPM 0x800
struct drm_panel {int unused;};
struct mipi_dsi_device {unsigned long mode_flags; int dev; unsigned int channel;};
struct mipi_dsi_msg {u8 channel,type; u16 flags;size_t tx_len;const void *tx_buf;size_t rx_len;void *rx_buf;};
#define MIPI_DSI_MSG_USE_LPM 2
#define MIPI_DSI_DCS_LONG_WRITE 57
#define GFP_KERNEL 0
static int alloc_fail;
static void *kcalloc(size_t n,size_t size,int flags) {(void)flags;return alloc_fail?NULL:calloc(n,size);}
#define kfree free
struct regulator {int id,on,load;};
struct gpio_desc {int id,value;};
struct mutex {int held;};
static void mutex_lock(struct mutex *m) {if(m->held)abort();m->held=1;}
static void mutex_unlock(struct mutex *m) {if(!m->held)abort();m->held=0;}
static int verbose,op,fail_op,tx,groups,fail_group,fail_disable_id,warnings;
static u8 last_packet[3];
struct backlight_device {void *data;struct {int brightness,power;} props;};
static void *bl_get_data(struct backlight_device *b) {return b->data;}
static int backlight_get_brightness(struct backlight_device *b) {return b->props.power?0:b->props.brightness;}
#define dev_warn(dev,...) do {(void)(dev);warnings++;} while(0)
#define dev_err(dev,...) do {(void)(dev);} while(0)
static void msleep(unsigned int ms) {if(verbose)printf("W %u\n",ms);}
static int regulator_set_load(struct regulator *r,int load) {
 if(verbose)printf("L %d %d\n",r->id,load);
 if(++op==fail_op)return -EIO;
 r->load=load;return 0;
}
static int regulator_enable(struct regulator *r) {
 if(verbose)printf("E %d\n",r->id);
 if(++op==fail_op)return -EIO;
 if(r->on)abort();
 r->on=1;return 0;
}
static int regulator_set_voltage(struct regulator *r,int low,int high) {
 if(verbose)printf("V %d %d\n",r->id,low);
 if(low!=high)abort();
 return ++op==fail_op?-EIO:0;
}
static int regulator_disable(struct regulator *r) {
 if(verbose)printf("D %d\n",r->id);
 if(r->id==fail_disable_id)return -EIO;
 if(!r->on)abort();
 r->on=0;return 0;
}
static void gpiod_set_value_cansleep(struct gpio_desc *g,int value) {
 if(verbose)printf("G %d %d\n",g->id,value);
 g->value=value;
}
static int mipi_dsi_transfer_batch(struct mipi_dsi_device *d,const struct mipi_dsi_msg *m,size_t n) {
 if(!n||n>64)abort();
 if(verbose)printf("B %zu %d\n",n,!!(m[0].flags&MIPI_DSI_MSG_USE_LPM));
 for(size_t j=0;j<n;j++) {
   const u8 *b=m[j].tx_buf;++tx;
   if(m[j].tx_len==3)memcpy(last_packet,b,3);
   if(m[j].channel!=d->channel || m[j].flags!=m[0].flags || m[j].rx_len || m[j].rx_buf)abort();
   if(m[j].type!=(m[j].tx_len==1?5:m[j].tx_len==2?21:57))abort();
   if(verbose){printf("P %d ",!!(m[j].flags&MIPI_DSI_MSG_USE_LPM));
     for(size_t i=0;i<m[j].tx_len;i++)printf("%02x",b[i]);
     puts("");}
 }
 return ++groups==fail_group?-EIO:0;
}
'''
    main_c = r'''
#define CHECK(c) do {if(!(c)){fprintf(stderr,"failed at line %d\n",__LINE__);return 1;}}while(0)
static struct regulator io={.id=1},ci={.id=2};
static struct gpio_desc rst={.id=3},dr={.id=4};
static struct mipi_dsi_device dsi;
static void fresh(struct aa569 *c) {
 memset(c,0,sizeof(*c));io.on=ci.on=io.load=ci.load=0;rst.value=1;dr.value=0;
 c->dsc=aa569_dsc;
 dsi.mode_flags=0x10;c->dsi=&dsi;c->vddio=&io;c->vci=&ci;c->reset=&rst;c->vddr=&dr;
 op=tx=groups=fail_op=fail_group=fail_disable_id=warnings=alloc_fail=0;
}
static bool off(const struct aa569 *c) {
 return !c->vci_on&&!c->vddr_on&&!c->vddio_on&&!io.on&&!ci.on&&!dr.value&&rst.value==1;
}
int main(void) {
 struct aa569 c;fresh(&c);verbose=1;
 CHECK(!aa569_prepare(&c.panel)&&c.prepared&&!c.enabled&&tx==149);
 CHECK(!aa569_enable(&c.panel)&&c.enabled&&tx==151);
 CHECK(!aa569_disable(&c.panel)&&!c.enabled&&tx==153);
 CHECK(!aa569_unprepare(&c.panel)&&off(&c)&&!c.prepared&&tx==154);
 CHECK(dsi.mode_flags==0x10);verbose=0;
 /* Each of the twelve init DMA group failures unwinds all enabled rails. */
 for(int i=1;i<=12;i++) {
   fresh(&c);fail_group=i;
   CHECK(aa569_prepare(&c.panel)==-EIO && groups==i && off(&c) && !c.prepared);
   CHECK(dsi.mode_flags==0x10 && !c.lock.held);
 }
 /* Both enable failures still permit safe teardown. */
 for(int i=13;i<=14;i++) {
   fresh(&c);CHECK(!aa569_prepare(&c.panel));fail_group=i;
   CHECK(aa569_enable(&c.panel)==-EIO);
   /* A later enable must retry the failed starting brightness. */
   CHECK(!aa569_enable(&c.panel)&&c.enabled);
   CHECK(!aa569_unprepare(&c.panel)&&off(&c)&&!c.prepared);
 }
 fresh(&c);alloc_fail=1;
 CHECK(aa569_prepare(&c.panel)==-ENOMEM&&groups==0&&off(&c)&&dsi.mode_flags==0x10);
 /* Reject a truncated pending group or delay before submission. */
 fresh(&c);CHECK(aa569_send(&c,aa569_on,aa569_on_bytes,1,true)==-EINVAL&&groups==0);
 struct aa569_cmd invalid={.length=1,.type=5,.batch=true,.delay_ms=1};
 CHECK(aa569_send(&c,&invalid,aa569_on_bytes,1,true)==-EINVAL&&groups==0);
 /* Each load/enable failure before reset restores balanced supply ownership. */
 for(int i=1;i<=6;i++) {
   fresh(&c);fail_op=i;
   CHECK(aa569_prepare(&c.panel)==-EIO&&off(&c)&&!c.prepared);
 }
 /* A disable failure retains ownership and forbids a double-enable. */
 fresh(&c);CHECK(!aa569_prepare(&c.panel));fail_disable_id=2;
 CHECK(aa569_unprepare(&c.panel)==-EIO && c.vci_on && c.vddio_on && c.vddr_on);
 CHECK(aa569_power_on(&c)==-EBUSY);
 fail_disable_id=0;CHECK(!aa569_unprepare(&c.panel)&&off(&c));
 fresh(&c);CHECK(!aa569_prepare(&c.panel));fail_disable_id=1;
 CHECK(aa569_unprepare(&c.panel)==-EIO && !c.vci_on && c.vddio_on && !c.vddr_on);
 fail_disable_id=0;CHECK(!aa569_unprepare(&c.panel)&&off(&c));
 /* DPMS disable/enable without unprepare must not leave the panel asleep. */
 fresh(&c);CHECK(!aa569_prepare(&c.panel));CHECK(!aa569_enable(&c.panel));
 CHECK(!aa569_disable(&c.panel));CHECK(!aa569_enable(&c.panel));
 CHECK(tx==155);CHECK(!aa569_unprepare(&c.panel)&&off(&c));
 /* Off-command errors still reset/power down; a retry is balanced. */
 for(int i=15;i<=17;i++) {
   fresh(&c);CHECK(!aa569_prepare(&c.panel));CHECK(!aa569_enable(&c.panel));
   fail_group=i;int first=aa569_disable(&c.panel);int last=aa569_unprepare(&c.panel);
   CHECK(!first&&!last&&warnings>0&&off(&c)&&!c.prepared&&!c.enabled);
   CHECK(!aa569_unprepare(&c.panel)&&off(&c));
 }
 /* Binary compatibility blanking must keep the prepared/enabled pipeline,
  * send only DBV0/1433, and do no transfer after panel power down. */
 fresh(&c);struct backlight_device backlight={.data=&c,.props={.brightness=1}};
 CHECK(!aa569_backlight_update_status(&backlight)&&tx==0);
 CHECK(!aa569_prepare(&c.panel)&&!aa569_enable(&c.panel));
 backlight.props.power=4;CHECK(!aa569_backlight_update_status(&backlight));
 CHECK(last_packet[0]==0x51&&last_packet[1]==0&&last_packet[2]==0&&c.enabled&&c.prepared);
 backlight.props.power=0;CHECK(!aa569_backlight_update_status(&backlight));
 CHECK(last_packet[0]==0x51&&last_packet[1]==5&&last_packet[2]==0x99);
 int before=tx;backlight.props.brightness=2;
 CHECK(aa569_backlight_update_status(&backlight)==-EINVAL&&tx==before);
 backlight.props.brightness=1;fail_group=groups+1;
 CHECK(aa569_backlight_update_status(&backlight)==-EIO&&c.enabled&&c.prepared&&!c.lock.held);
 fail_group=0;CHECK(!aa569_unprepare(&c.panel)&&off(&c));before=tx;
 CHECK(!aa569_backlight_update_status(&backlight)&&tx==before);
 puts("OK real AA569 callbacks and fault recovery; no hardware I/O");return 0;
}
'''
    combined = prefix+header+generated+extract(source, 'struct aa569 {')+';\n'+callbacks+main_c
    with tempfile.TemporaryDirectory(prefix='oneplus13-panel-') as tmp:
        c, exe = Path(tmp)/'panel.c', Path(tmp)/'panel.exe'
        c.write_text(combined, encoding='utf-8')
        if a.keep_source:
            a.keep_source.write_text(combined, encoding='utf-8')
        command = ['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror']
        if a.sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command+[str(c), '-o', str(exe)], check=True)
        output = subprocess.check_output([str(exe)], text=True).splitlines()
    packets = [line.split() for line in output if line.startswith('P ')]
    expected = [('1', v['payload']) for v in fixture['on_packets']]
    expected += [('0','510599')]+[('0',v['payload']) for v in fixture['off_packets']]
    if [(x[1],x[2]) for x in packets] != expected:
        raise ValueError('Actual C sender differs from public packet payload/order/LP-HS')
    expected_groups = []
    n = 0
    for packet in fixture['on_packets']:
        n += 1
        if packet['vendor_flags'] == 0:
            expected_groups.append((n, 1))
            n = 0
    expected_groups += [(1, 0)] * 4  # brightness and three off packets
    if [tuple(map(int, line.split()[1:])) for line in output if line.startswith('B ')] != expected_groups:
        raise ValueError('Real panel callback lost a vendor group boundary')
    power = [line for line in output if line[0] in 'WLEDGV']
    expected_power = ['W 1','L 1 220000','V 1 1800000','E 1','W 3','G 4 1','W 3',
                      'L 2 10000','V 2 3000000','E 2','W 10','G 3 0','W 10','G 3 1','W 5','G 3 0','W 30',
                      'W 120','W 20','W 120','G 3 1','W 1','D 2','L 2 300','W 3',
                      'G 4 0','W 3','D 1','L 1 8000','W 1']
    if power != expected_power or not output[-1].startswith('OK '):
        raise ValueError('C power/reset/wait order differs from stock sequence')
    result = {'scope': 'HOST_C_PANEL_COMPONENT_ONLY', 'hardware_tested': False,
              'source_commit': fixture['source_commit'], 'kernel_commit': pin,
              'panel_source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'sanitizers': a.sanitize, 'public_on_packets_match': 150,
              'public_off_packets_match': 3, 'fixed_brightness_dbv': 1433,
              'on_lp_off_and_brightness_hs': True, 'power_reset_delays_match': True,
              'init_dma_failure_groups_checked': 12, 'on_vendor_groups_match': 13,
              'allocation_and_split_group_controls': True,
              'supply_failure_controls': 8, 'disable_enable_cycle_checked': True,
              'off_command_failure_positions_checked': 3,
              'off_command_errors_do_not_wedge_framework_state': True,
              'separate_native_dtb_source_present': (repo/'devices/oneplus13/sm8750-oneplus-dodge-native.dts').exists(),
              'board_node_exercised_by_this_check': False,
              'spr_runtime_exercised_by_this_check': False}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print('Real AA569 C callbacks: packets, power, reset, LP/HS and fault controls passed.')


if __name__ == '__main__':
    main()
