#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run actual phone PHY callbacks against bounded host MMIO, never a device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
PREFIX = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <limits.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define U64_MAX UINT64_MAX
#define BIT(x) (1U << (x))
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define PHY_MODE_MIPI_CPHY 7
#define IRQ_NONE 0
#define IRQ_HANDLED 1
typedef int irqreturn_t;
static unsigned int reads, writes, sleeps, irq_on, irq_off;
static bool powered = true;
static uint32_t registers[0x1200 / 4];
static uint32_t *checked(void *address) {
 uintptr_t a=(uintptr_t)address, b=(uintptr_t)registers;
 assert(powered && a >= b && a <= b + 0x1184 && (a-b)%4 == 0);
 return address;
}
static u32 readl(void *address) { reads++;return *checked(address); }
static void writel(u32 value, void *address) { writes++;*checked(address)=value; }
static void usleep_range(unsigned int low, unsigned int high)
{ assert(low <= high && high <= 10005);sleeps++; }
static void enable_irq(int irq) { assert(powered && irq==4 && !irq_on);irq_on=1; }
static void disable_irq(int irq) { assert(powered && irq==4 && irq_on);irq_on=0;irq_off++; }
static u64 div_u64(u64 n, unsigned int d) { return n/d; }
static unsigned long clk_get_rate(void *clock) { return *(unsigned long *)clock; }
#define readl_poll_timeout(addr,val,condition,delay,timeout) \
 ((void)(delay),(void)(timeout),(val)=readl(addr),(condition)?0:-ETIMEDOUT)
struct mipi_csi2phy_stream_cfg {
 int64_t link_freq;
 unsigned int num_data_lanes,t_prepare_ns,t_preamble_ns;
 struct { struct { unsigned int pos,pol; } data[3]; } lane_cfg;
};
struct mipi_csi2phy_device {
 void *base,*hw_priv,*timer_clk;
 unsigned int phy_mode,timer_clk_rate;
 struct mipi_csi2phy_stream_cfg stream_cfg;
};
#include "catalog.h"
'''

SUFFIX = r'''
static void fresh(struct mipi_csi2phy_device *phy, struct dodge_cphy_state *state)
{
 memset(registers,0,sizeof(registers));memset(state,0,sizeof(*state));
 reads=writes=sleeps=irq_on=irq_off=0;state->irq=4;phy->hw_priv=state;
 phy->stream_cfg=(struct mipi_csi2phy_stream_cfg){.link_freq=1000000000,
  .num_data_lanes=3,.t_prepare_ns=1000,.t_preamble_ns=3000};
 for(unsigned int i=0;i<3;i++)phy->stream_cfg.lane_cfg.data[i].pos=i;
}
int main(void)
{
 unsigned int cases=0;unsigned long clock=400000000;
 struct dodge_cphy_state *state=calloc(1,sizeof(*state));assert(state);
 struct mipi_csi2phy_device phy={.base=registers,.phy_mode=PHY_MODE_MIPI_CPHY,
  .timer_clk=&clock,.timer_clk_rate=400000000};
 fresh(&phy,state);
 assert(dodge_cphy_validate(&phy,&phy.stream_cfg)==0 && !reads && !writes);cases++;
 for(unsigned int bad=0;bad<8;bad++) {
  fresh(&phy,state);
  switch(bad) {
  case 0:phy.stream_cfg.num_data_lanes=2;break;
  case 1:phy.stream_cfg.link_freq=0;break;
  case 2:phy.stream_cfg.lane_cfg.data[1].pos=0;break;
  case 3:phy.stream_cfg.lane_cfg.data[2].pos=3;break;
  case 4:phy.stream_cfg.lane_cfg.data[0].pol=1;break;
  case 5:phy.stream_cfg.t_prepare_ns=0;break;
  case 6:phy.stream_cfg.t_preamble_ns=0;break;
  case 7:phy.stream_cfg.link_freq=INT64_MAX;break;
  }
  assert(dodge_cphy_validate(&phy,&phy.stream_cfg)<0 && !reads && !writes);cases++;
 }
 fresh(&phy,state);clock=399999999;
 assert(dodge_cphy_lanes_enable(&phy,&phy.stream_cfg)==-ERANGE && !writes && !irq_on);cases++;
 clock=400000000;
 for(unsigned int r=0;r<ARRAY_SIZE(rates);r++) {
  fresh(&phy,state);phy.stream_cfg.link_freq=rates[r].max_symbol_rate/2;
  unsigned long minimum=0;
  assert(dodge_cphy_core_rate(&phy,&minimum)==0);
  assert(minimum==(rates[r].max_symbol_rate*228/100)/32+2);
  registers[0x1184/4]=0x80;registers[0x1014/4]=0x80;
  assert(dodge_cphy_lanes_enable(&phy,&phy.stream_cfg)==0 && irq_on);
  assert(state->irq_enabled && registers[0x1000/4]==0x0e);
  assert(registers[0x1014/4]==0xaa && state->plan.settle_count==790);
  dodge_cphy_lanes_disable(&phy,&phy.stream_cfg);
  assert(!irq_on && irq_off==1 && !state->irq_enabled);
  assert(registers[0x1014/4]==0x80 && registers[0x1000/4]==1);
  for(unsigned int i=0;i<11;i++)assert(!registers[(0x102c+4*i)/4]);
  for(unsigned int i=0;i<ARRAY_SIZE(phone_common);i++)
   assert(!registers[phone_common[i].address/4]);
  cases++;
 }
 fresh(&phy,state);
 assert(dodge_cphy_lanes_enable(&phy,&phy.stream_cfg)==-ETIMEDOUT);
 assert(irq_on && state->irq_enabled && registers[0x1000/4]!=0x0e);
 dodge_cphy_lanes_disable(&phy,&phy.stream_cfg);
 assert(!irq_on && irq_off==1 && registers[0x1000/4]==1);cases++;
 fresh(&phy,state);
 assert(dodge_cphy_irq(4,&phy)==IRQ_NONE);cases++;
 for(unsigned int i=0;i<11;i++)registers[(0x1138+4*i)/4]=BIT(i%8);
 assert(dodge_cphy_irq(4,&phy)==IRQ_HANDLED);
 for(unsigned int i=0;i<11;i++)assert(!registers[(0x1058+4*i)/4]);
 assert(!registers[0x1028/4]);cases++;
 struct phone_cphy_op invalid={.type=PHONE_WRITE,.address=0x1188};
 unsigned int old=writes;
 assert(dodge_cphy_operation(&phy,&invalid)==-EINVAL && writes==old);cases++;
 invalid.address=3;
 assert(dodge_cphy_operation(&phy,&invalid)==-EINVAL && writes==old);cases++;
 invalid.address=0;invalid.type=99;
 assert(dodge_cphy_operation(&phy,&invalid)==-EINVAL && writes==old);cases++;
 free(state);powered=false;
 printf("PHONE_CPHY_ACTUAL_MMIO_HOST_PASS cases=%u hardware=no\n",cases);
 return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    raw = (HERE / 'dodge-cphy-mmio.h').read_text()
    # Extract unmodified callback definitions, omitting kernel probe glue only.
    callbacks = raw[raw.index('#define DODGE_CPHY_TIMER_HZ'):raw.index('static const struct mipi_csi2phy_hw_ops')]
    start = callbacks.index('static int dodge_cphy_init(')
    end = callbacks.index('static int dodge_cphy_validate(')
    callbacks = callbacks[:start] + callbacks[end:]
    source = out / 'actual-phone-mmio.c'
    source.write_text(PREFIX + callbacks + SUFFIX)
    results = []
    for name, flags in [('release', ['-O2']), ('sanitized', ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer'])]:
        target = out / name
        subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', *flags, '-I', str(args.catalog.resolve()),
                        str(source), '-o', str(target)], check=True, timeout=40)
        result = subprocess.run([str(target)], check=True, capture_output=True,
                                text=True, timeout=15, env=dict(os.environ, ASAN_OPTIONS='detect_leaks=1:abort_on_error=1'))
        results.append({'mode': name, 'result': result.stdout.strip()})
    report = {'scope': 'ACTUAL_PHONE_PHY_CALLBACKS_HOST_MMIO_ONLY', 'results': results,
              'source_sha256': hashlib.sha256(raw.encode()).hexdigest(),
              'phone_operations': 0, 'hardware_qualified': False,
              'kernel_probe_power_clock_framework_exercised': False}
    (out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
