# SPDX-License-Identifier: GPL-2.0-only
"""Run the actual supply sysfs store with a delayed watchdog and fake boot time."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'devices/oneplus13/kernel-patches/0024-oneplus13-otg-vbus.patch'


def function(name, patch=None):
    text = '\n'.join(line[1:] for line in (patch or PATCH.read_text()).splitlines()
                     if line.startswith('+') and not line.startswith('+++'))
    start = text.index('static ', text.rfind('\n}', 0, text.index(name)) + 1)
    end = text.index('\n}', text.index(name)) + 2
    result = text[start:end]
    assert re.search(r'\b' + name + r'\(', result)
    return result


PRELUDE = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "otg-supply-lease.h"
#define CHECK(v) do { if (!(v)) { fprintf(stderr, "%d: %s\n", __LINE__, #v); exit(1); } } while(0)
#define HZ 100U
#define NSEC_PER_SEC 1000000000ULL
typedef uint64_t u64;
typedef long ssize_t;
struct wakeup_source { bool held; };
struct delayed_work { bool queued; };
struct qcom_battmgr {
  int lock;
  bool op13_otg_stopping, op13_otg_awake;
  u64 op13_otg_supply_deadline;
  struct wakeup_source *op13_otg_wakeup;
  struct delayed_work op13_otg_supply_restore_work;
  struct op13_otg_supply_lease op13_otg_supply_lease;
};
struct device { struct qcom_battmgr *data; };
struct device_attribute { int ignored; };
static u64 now;
static unsigned int gets, sets, renewals;
static int depth, op13_otg_boot_used;
static bool oneplus13_otg_supply_lease = true;
static int system_freezable_wq;
static unsigned int values[65];
static u64 ktime_get_boottime_ns(void) { return now; }
static void mutex_lock(int *p) { (void)p; CHECK(!depth); depth++; }
static void mutex_unlock(int *p) { (void)p; CHECK(depth == 1); depth--; }
static int atomic_cmpxchg(int *p, int a, int b) { int old=*p; if(old==a)*p=b; return old; }
static bool op13_otg_machine(void) { return true; }
static void *dev_get_drvdata(struct device *d) { return d->data; }
static bool sysfs_streq(const char *a, const char *b) { return !strcmp(a,b); }
static bool schedule_delayed_work(struct delayed_work *w, unsigned int delay) {
  CHECK(depth == 1 && delay == 120*HZ); w->queued=true; return true;
}
static void mod_delayed_work(int queue, struct delayed_work *w, unsigned int delay) {
  (void)queue; CHECK(depth == 1 && delay == 120*HZ); w->queued=true; renewals++;
}
static void cancel_delayed_work(struct delayed_work *w) { w->queued=false; }
static void __pm_stay_awake(struct wakeup_source *w) { w->held=true; }
static void __pm_relax(struct wakeup_source *w) { w->held=false; }
static int healthy(void *p) { (void)p; return 1; }
static int getv(void *p, unsigned int prop, unsigned int *v) {
  (void)p; CHECK(depth == 1); gets++; *v=values[prop]; return 0;
}
static int setv(void *p, unsigned int prop, unsigned int v) {
  (void)p; CHECK(depth == 1); sets++; values[prop]=v; return 0;
}
'''

TAIL = r'''
int main(int argc, char **argv) {
  CHECK(argc == 2);
  struct wakeup_source wake = {true};
  struct qcom_battmgr b = {
    .op13_otg_supply_deadline=120*NSEC_PER_SEC, .op13_otg_wakeup=&wake,
    .op13_otg_awake=true, .op13_otg_supply_restore_work={true},
    .op13_otg_supply_lease={.state=OP13_OTG_SUPPLY_ARMED, .used=1,
                          .dirty_ap=1, .dirty_switch=1}
  };
  values[17]=values[18]=1;
  struct device d = {&b};
  if (!strcmp(argv[1], "before")) now=119*NSEC_PER_SEC;
  else if (!strcmp(argv[1], "boundary")) now=120*NSEC_PER_SEC;
  else if (!strcmp(argv[1], "delayed-worker")) now=125*NSEC_PER_SEC;
  else if (!strcmp(argv[1], "stopping")) { now=10*NSEC_PER_SEC; b.op13_otg_stopping=true; }
  else if (!strcmp(argv[1], "restore-expired")) now=125*NSEC_PER_SEC;
  else CHECK(0);
  u64 saved=b.op13_otg_supply_deadline;
  bool restore=!strcmp(argv[1], "restore-expired");
  ssize_t result=oneplus13_otg_supply_lease_store(&d,NULL,restore?"restore":"start",restore?7:5);
  if (!strcmp(argv[1], "before")) {
    CHECK(result==5 && sets==2 && gets==4 && renewals==1);
    CHECK(b.op13_otg_supply_lease.state==OP13_OTG_SUPPLY_ACTIVE);
    CHECK(b.op13_otg_supply_deadline==now+120*NSEC_PER_SEC);
  } else if (restore) {
    CHECK(result==7 && sets==2 && gets==2 && !renewals);
    CHECK(b.op13_otg_supply_lease.state==OP13_OTG_SUPPLY_RESTORED);
    CHECK(!wake.held && !values[17] && !values[18]);
  } else {
    CHECK(result<0 && !sets && !gets && !renewals);
    CHECK(b.op13_otg_supply_deadline==saved);
    CHECK(b.op13_otg_supply_lease.state==OP13_OTG_SUPPLY_ARMED);
  }
  CHECK(!depth); return 0;
}
'''


def harness(patch=None):
    return (PRELUDE + function('op13_otg_supply_expired', patch)
            + '\nstatic struct op13_otg_supply_io op13_otg_supply_io(struct qcom_battmgr *b) {\n'
            'return (struct op13_otg_supply_io){b,getv,setv,healthy,op13_otg_supply_expired};\n}\n'
            + function('oneplus13_otg_supply_lease_store', patch) + TAIL)


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc'), 'Linux GCC required')
class SupplyDeadlineTests(unittest.TestCase):
    def build(self, folder, text, extra=()):
        source, binary = folder / 'case.c', folder / 'case'
        source.write_text(text)
        run = subprocess.run(['gcc', '-std=c11', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-parameter', '-Wno-sign-compare', *extra,
            '-I', str(ROOT/'tools/oneplus13/usb-draft'),
            str(source), '-o', str(binary)], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        return binary

    def test_actual_store_rejects_expired_arm_without_renewal(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for flags in (('-O2',), ('-O1', '-fsanitize=address,undefined',
                          '-fno-sanitize-recover=all', '-fno-pie', '-no-pie')):
                binary = self.build(folder, harness(), flags)
                for case in ('before', 'boundary', 'delayed-worker', 'stopping', 'restore-expired'):
                    with self.subTest(flags=flags, case=case):
                        run = subprocess.run([str(binary), case], capture_output=True, text=True,
                            timeout=5, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1'})
                        self.assertEqual(run.returncode, 0, run.stderr)

    def test_negative_control_original_store_reactivates_expired_arm(self):
        patch = PATCH.read_text().replace(
            ' != OP13_OTG_SUPPLY_ARMED ||\n+\t\t    op13_otg_supply_expired(battmgr)',
            ' != OP13_OTG_SUPPLY_ARMED')
        with tempfile.TemporaryDirectory() as temp:
            binary = self.build(Path(temp), harness(patch))
            run = subprocess.run([str(binary), 'delayed-worker'], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(run.returncode, 0, 'Old ordering must reproduce the deadline violation')


if __name__ == '__main__':
    unittest.main()
