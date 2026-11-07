#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual backported CCI clock/timing functions; no phone access."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile


def load_inventory(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    lock = json.loads((root / 'devices/oneplus13/kernel-patches/cci-upstream-source.json').read_text())
    source = json.loads((root / 'devices/oneplus13/source.json').read_text())
    if lock['kernel_pin'] != source['kernel']['commit'] or lock['hardware_qualified'] is not False:
        raise ValueError('CCI inventory has an unexpected kernel or hardware claim')
    if lock['patch_file'] != '0021-cci-clock-and-stretch.patch' or set(lock['generated_files']) != {
            'drivers/i2c/busses/i2c-qcom-cci.c', 'drivers/i2c/busses/Kconfig'}:
        raise ValueError('CCI inventory has unexpected source paths')
    patch = root / 'devices/oneplus13/kernel-patches' / lock['patch_file']
    if hashlib.sha256(patch.read_bytes()).hexdigest() != lock['patch_sha256']:
        raise ValueError('CCI backport patch differs from reviewed inventory')
    return lock


def validate_source(kernel, root=None):
    lock = load_inventory(root)
    for name, expected in lock['generated_files'].items():
        if hashlib.sha256((kernel / name).read_bytes()).hexdigest() != expected:
            raise ValueError('CCI generated source differs: ' + name)
    return lock


def validate_patch_roundtrip(kernel, lock, root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    patch = root / 'devices/oneplus13/kernel-patches' / lock['patch_file']
    with tempfile.TemporaryDirectory(prefix='cci-patch-roundtrip-') as temporary:
        scratch = Path(temporary)
        for name in lock['generated_files']:
            target = scratch / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((kernel / name).read_bytes())
        subprocess.run(['git', 'apply', '--reverse', str(patch)], cwd=scratch,
                       check=True, capture_output=True, timeout=20)
        for name, expected in lock['base_files'].items():
            if hashlib.sha256((scratch / name).read_bytes()).hexdigest() != expected:
                raise ValueError('CCI reconstructed pinned base differs: ' + name)
        subprocess.run(['git', 'apply', str(patch)], cwd=scratch,
                       check=True, capture_output=True, timeout=20)
        for name, expected in lock['generated_files'].items():
            if hashlib.sha256((scratch / name).read_bytes()).hexdigest() != expected:
                raise ValueError('CCI patch roundtrip differs: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    lock = validate_source(args.kernel_source)
    validate_patch_roundtrip(args.kernel_source, lock)
    source = (args.kernel_source / 'drivers/i2c/busses/i2c-qcom-cci.c').read_text()
    helper = importlib.util.spec_from_file_location('cci_extract', Path(__file__).with_name('check-display.py'))
    module = importlib.util.module_from_spec(helper)
    helper.loader.exec_module(module)
    extract = module.extract_function
    declarations = source[source.index('#define CCI_HW_VERSION'):source.index('static irqreturn_t cci_isr(')]
    table = source[source.index('static const unsigned long cci_clk_rates'):source.index('static int cci_init(')]
    functions = '\n'.join(extract(source, sig) for sig in (
        'static int cci_init(', 'static int cci_get_required_rate(', 'static int cci_set_core_rate('))
    prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define __iomem
struct device { void *data; };
struct i2c_adapter { int unused; };
struct i2c_adapter_quirks { int unused; };
struct completion { int unused; };
struct clk_bulk_data { int unused; };
struct clk { unsigned long rate; bool rate19, rate37; };
#define dev_err(dev, ...) ((void)(dev))
#define dev_warn(dev, ...) ((void)(dev))
static unsigned int writes, opp_calls;
static int opp_error;
static unsigned long clk_get_rate(struct clk *clk) { return clk->rate; }
static long clk_round_rate(struct clk *clk, unsigned long rate)
{
 if (clk->rate19 && rate <= 19200000UL) return 19200000;
 if (clk->rate37 && rate <= 37500000UL) return 37500000;
 return -EINVAL;
}
static void writel(u32 value, void *addr) { *(u32 *)addr = value; writes++; }
'''
    stubs = r'''
static int dev_pm_opp_set_rate(struct device *dev, unsigned long rate)
{
 struct cci *cci = dev->data; opp_calls++;
 if (opp_error) return opp_error;
 cci->cci_clk->rate = rate; return 0;
}
'''
    tests = r'''
int main(void)
{
 u32 regs[0x1000 / sizeof(u32)] = {0};
 struct clk clk = {.rate = 30000000, .rate37 = true};
 struct device dev = {0};
 struct cci_data data = {.num_masters = 2, .max_mode = I2C_MODE_FAST_PLUS};
 struct cci cci = {.dev=&dev, .base=regs, .data=&data, .cci_clk=&clk};
 unsigned long rate=0; unsigned int before;
 dev.data=&cci; cci.master[0].cci=&cci;
 /* SM8750 has only 37.5 MHz: reject a rounded 19.2 MHz request. */
 assert(!cci_get_required_rate(&cci,&rate) && rate==37500000);
 assert(!cci_set_core_rate(&cci) && clk.rate==37500000);
 assert(!cci_init(&cci));
 assert(regs[CCI_I2C_Mm_SCL_CTL(0)/4]==((201U<<16)|174));
 assert(regs[CCI_I2C_Mm_MISC_CTL(0)/4]==(BIT(8)|(6U<<4)|3));
 cci.master[0].mode=I2C_MODE_FAST;
 assert(!cci_init(&cci));
 assert(regs[CCI_I2C_Mm_SCL_CTL(0)/4]==((38U<<16)|56));
 cci.master[0].mode=I2C_MODE_FAST_PLUS;
 assert(!cci_init(&cci));
 assert(regs[CCI_I2C_Mm_MISC_CTL(0)/4]==(BIT(8)|(3U<<4)|3));
 /* Legacy 19.2 MHz Standard/Fast retain their original timings. */
 clk.rate19=true; cci.master[0].mode=I2C_MODE_STANDARD;
 assert(!cci_get_required_rate(&cci,&rate) && rate==19200000);
 assert(!cci_set_core_rate(&cci) && !cci_init(&cci));
 assert(regs[CCI_I2C_Mm_SCL_CTL(0)/4]==((78U<<16)|114));
 cci.master[0].mode=I2C_MODE_FAST;
 assert(!cci_init(&cci));
 assert(regs[CCI_I2C_Mm_SCL_CTL(0)/4]==((20U<<16)|28));
 /* One Fast+ master forces the shared clock to 37.5 MHz. */
 cci.master[1].cci=&cci; cci.master[1].mode=I2C_MODE_FAST_PLUS;
 assert(!cci_get_required_rate(&cci,&rate) && rate==37500000);
 assert(!cci_set_core_rate(&cci) && !cci_init(&cci));
 assert(regs[CCI_I2C_Mm_MISC_CTL(1)/4] & BIT(8));
 /* Unknown clocks and unsupported modes fail before timing writes. */
 clk.rate=30000000; before=writes;
 assert(cci_init(&cci)==-EOPNOTSUPP && writes==before+1);
 clk.rate19=false; clk.rate37=false; before=opp_calls;
 assert(cci_set_core_rate(&cci)==-EOPNOTSUPP && opp_calls==before);
 clk.rate37=true; data.max_mode=I2C_MODE_FAST;
 assert(cci_get_required_rate(&cci,&rate)==-EOPNOTSUPP);
 data.max_mode=I2C_MODE_FAST_PLUS; opp_error=-EIO;
 assert(cci_set_core_rate(&cci)==-EIO);
 puts("CCI clock selection/timings/stretch: offline controls passed");
 return 0;
}
'''
    combined = prefix + declarations + stubs + table + functions + tests
    with tempfile.TemporaryDirectory(prefix='oneplus13-cci-') as temporary:
        path = Path(temporary) / 'cci.c'
        executable = Path(temporary) / 'cci'
        path.write_text(combined, encoding='utf-8')
        # Kernel builds disable sign-compare for these unchanged upstream loops.
        command = ['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare']
        if args.sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command + [str(path), '-o', str(executable)], check=True)
        subprocess.run([str(executable)], check=True)
    report = {'scope':'OFFLINE_ACTUAL_CCI_C_FUNCTIONS', 'hardware_tested':False,
        'pass':True, 'patch_sha256':lock['patch_sha256'],
        'kernel_commit':lock['kernel_pin'], 'sanitizers':args.sanitize,
        'pinned_base_patch_roundtrip':True,
        'generated_files':lock['generated_files'], 'sm8750_required_rate_hz':37500000,
        'unknown_rates_rejected':True, 'legacy_19_2mhz_controls':True,
        'mixed_master_modes_checked':True, 'opp_failure_propagated':True,
        'clock_stretch_bit_checked':True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
