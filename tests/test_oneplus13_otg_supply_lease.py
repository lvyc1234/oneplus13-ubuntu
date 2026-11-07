# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the bounded USB supply lease policy without hardware access."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "tools/oneplus13/usb-draft/otg-supply-lease.h"

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "otg-supply-lease.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); return 1; } } while (0)
struct fake { unsigned int ap, sw, vbus, current; int poison, expired, fail_set_prop, fail_get_prop; char trace[512]; };
static void record(struct fake *f, char op, unsigned int p, unsigned int v) {
  char item[32]; snprintf(item, sizeof(item), "%c%u=%u;", op, p, v);
  strcat(f->trace, item);
}
static int healthy(void *p) { return !((struct fake *)p)->poison; }
static int expired(void *p) { return ((struct fake *)p)->expired; }
static int getv(void *p, unsigned int prop, unsigned int *out) {
  struct fake *f = p; unsigned int v = prop == 17 ? f->ap :
    (prop == 18 ? f->sw : (prop == 24 ? f->vbus : f->current));
  record(f, 'G', prop, v);
  if (f->fail_get_prop == (int)prop) return -1;
  *out = v; return 0;
}
static int setv(void *p, unsigned int prop, unsigned int v) {
  struct fake *f = p; record(f, 'S', prop, v);
  if (f->fail_set_prop == (int)prop) { f->poison = 1; return -1; }
  if (prop == 17) f->ap = v;
  else if (prop == 18) f->sw = v;
  else if (prop == 24) f->vbus = v;
  else f->current = v;
  return 0;
}
static struct op13_otg_supply_io io(struct fake *f) {
  struct op13_otg_supply_io v = { f, getv, setv, healthy, expired }; return v;
}
static void word(unsigned char *p, unsigned int v) {
  for (unsigned int i = 0; i < 4; i++) p[i] = (unsigned char)(v >> (8*i));
}
static void packet(unsigned char *p, unsigned int op, unsigned int prop,
                   unsigned int value, unsigned int result) {
  word(p, 32778); word(p+4, 1); word(p+8, op); word(p+12, prop);
  word(p+16, value); word(p+20, result);
}
int main(void) {
  struct fake f = {0}; struct op13_otg_supply_lease l = {0};
  struct op13_otg_supply_io x = io(&f);
  unsigned char p[24]; unsigned int value = 99;
  packet(p, 0x32, 24, 1, 0);
  CHECK(op13_otg_supply_decode(p, 24, 0x32, 24, &value) == 1 && value == 1);
  packet(p, 0x32, 24, 2, 0);
  CHECK(op13_otg_supply_decode(p, 24, 0x32, 24, &value) == -1 && value == 1);
  packet(p, 0x32, 64, 1250, 0);
  CHECK(op13_otg_supply_decode(p, 24, 0x32, 64, &value) == 1 && value == 1250);
  packet(p, 0x33, 64, 0, 0);
  CHECK(op13_otg_supply_decode(p, 24, 0x33, 64, &value) == 1 && value == 0);
  CHECK(op13_otg_supply_arm(&l, &x) == 0);
  CHECK(l.state == OP13_OTG_SUPPLY_ARMED && f.vbus == 0);
  CHECK(!strcmp(f.trace, "G17=0;G18=0;S17=1;G17=1;S18=1;G18=1;"));
  f.trace[0] = 0;
  CHECK(op13_otg_supply_start(&l, &x) == 0);
  CHECK(l.state == OP13_OTG_SUPPLY_ACTIVE && f.current == 1250 && f.vbus == 1);
  CHECK(!strcmp(f.trace, "G24=0;G64=0;S64=1250;G64=1250;S24=1;G24=1;"));
  f.trace[0] = 0;
  CHECK(op13_otg_supply_restore(&l, &x) == 0);
  CHECK(l.state == OP13_OTG_SUPPLY_RESTORED && f.current == 0 && f.vbus == 0);
  CHECK(!strcmp(f.trace, "S24=0;G24=0;S64=0;G64=0;S18=0;G18=0;S17=0;G17=0;"));

  memset(&f, 0, sizeof(f)); f.vbus = 0; f.current = 0; f.fail_set_prop = 24;
  l = (struct op13_otg_supply_lease){0}; x = io(&f);
  CHECK(op13_otg_supply_arm(&l, &x) == 0); f.trace[0] = 0;
  CHECK(op13_otg_supply_start(&l, &x) != 0);
  CHECK(l.state == OP13_OTG_SUPPLY_UNKNOWN && f.current == 1250 && f.vbus == 0);
  CHECK(strstr(f.trace, "S24=1;") != NULL && strstr(f.trace, "S64=0;") == NULL);

  memset(&f, 0, sizeof(f)); f.current = 1;
  l = (struct op13_otg_supply_lease){0}; x = io(&f);
  CHECK(op13_otg_supply_arm(&l, &x) == 0);
  CHECK(op13_otg_supply_start(&l, &x) != 0);
  CHECK(l.state == OP13_OTG_SUPPLY_ARMED && strstr(f.trace, "S24") == NULL);

  memset(&f, 0, sizeof(f)); f.expired = 1;
  l = (struct op13_otg_supply_lease){0}; x = io(&f);
  CHECK(op13_otg_supply_arm(&l, &x) != 0);
  CHECK(l.state == OP13_OTG_SUPPLY_REFUSED && strstr(f.trace, "S") == NULL);

  memset(&f, 0, sizeof(f)); f.poison = 1;
  l = (struct op13_otg_supply_lease){0}; l.state = OP13_OTG_SUPPLY_ACTIVE;
  l.dirty_vbus = l.dirty_current = l.dirty_switch = l.dirty_ap = 1; x = io(&f);
  CHECK(op13_otg_supply_restore(&l, &x) != 0);
  CHECK(l.state == OP13_OTG_SUPPLY_UNKNOWN && f.trace[0] == 0);
  puts("supply lease policy checks=9"); return 0;
}
'''


class SupplyLeasePolicyTests(unittest.TestCase):
    def test_policy_order_refusal_and_unknown_stop(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if not compiler:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "lease.c"
            binary = Path(temp) / "lease"
            source.write_text(HARNESS.replace('"otg-supply-lease.h"', f'"{HEADER.as_posix()}"'))
            built = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                                    str(source), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), "supply lease policy checks=9")


if __name__ == "__main__":
    unittest.main()
