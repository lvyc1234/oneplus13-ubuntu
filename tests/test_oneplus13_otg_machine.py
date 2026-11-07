# SPDX-License-Identifier: GPL-2.0-only
"""Compile the actual patched OTG identity predicate, including OF ownership."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'devices/oneplus13/kernel-patches/0022-oneplus13-otg-readonly.patch'

HARNESS = r'''
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
struct device_node { int dummy; };
static struct device_node root;
static const char *model, *chassis;
static bool present = true, dodge, sm8750, sun;
static int references;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d\n", __LINE__); exit(1); } } while (0)
static struct device_node *of_find_node_by_path(const char *path)
{ CHECK(!strcmp(path,"/")); if (!present) return NULL; references++; return &root; }
static int of_property_read_string(struct device_node *node, const char *name, const char **out)
{
 CHECK(node == &root && references == 1);
 if (!strcmp(name,"model")) *out=model;
 else { CHECK(!strcmp(name,"chassis-type")); *out=chassis; }
 return *out ? 0 : -1;
}
static bool of_device_is_compatible(struct device_node *node, const char *name)
{
 CHECK(node == &root && references == 1);
 if (!strcmp(name,"oneplus,dodge")) return dodge;
 if (!strcmp(name,"qcom,sm8750")) return sm8750;
 CHECK(!strcmp(name,"qcom,sun")); return sun;
}
static void of_node_put(struct device_node *node)
{ CHECK(node == &root && references == 1); references--; }
ACTUAL_FUNCTION
static void expect(bool wanted) { CHECK(op13_otg_machine() == wanted); CHECK(references == 0); }
int main(int argc, char **argv)
{
 const char *compiled="OnePlus 13 (AA569 native display experimental candidate)";
 const char *physical="Qualcomm Technologies, Inc. Sun MTP,dodge";
 CHECK(argc==2); chassis="handset";
 if (!strcmp(argv[1],"compiled")) {
   model=compiled; dodge=sm8750=true; expect(true);
   dodge=false; expect(false); dodge=true; sm8750=false; expect(false);
 } else if (!strcmp(argv[1],"physical")) {
   model=physical; sun=true; expect(true); sun=false; expect(false);
 } else if (!strcmp(argv[1],"wrong-board")) {
   const char *wrong[]={"Qualcomm Technologies, Inc. Sun MTP", "OnePlus 13R", "OnePlus Pad 2 Pro", "dodge", ""};
   dodge=sm8750=sun=true;
   for (unsigned int i=0;i<sizeof(wrong)/sizeof(wrong[0]);i++) { model=wrong[i]; expect(false); }
 } else if (!strcmp(argv[1],"chassis")) {
   model=physical; sun=true; chassis="tablet"; expect(false);
   chassis=""; expect(false); chassis=NULL; expect(false);
   model=compiled; dodge=sm8750=true; expect(false);
 } else if (!strcmp(argv[1],"missing")) {
   model=NULL; expect(false); model=physical; sun=true; present=false; expect(false);
 } else CHECK(false);
 return 0;
}
'''


class OtgMachineIntegrationTests(unittest.TestCase):
    def test_all_interface_and_late_reply_gates_use_exact_identity(self):
        patch = PATCH.read_text()
        additions = '\n'.join(line[1:] for line in patch.splitlines()
                              if line.startswith('+') and not line.startswith('+++'))
        self.assertEqual(additions.count('op13_otg_machine()'), 4)
        self.assertNotIn('of_machine_is_compatible', additions)
        self.assertIn('__ATTR(oneplus13_otg_ap_enable, 0400,', additions)
        self.assertIn('__ATTR(oneplus13_otg_switch, 0400,', additions)


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc'),
                     'Actual OF identity harness requires Linux GCC')
class OtgMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='op13-otg-machine-')
        cls.addClassCleanup(cls.directory.cleanup)
        additions = '\n'.join(line[1:] for line in PATCH.read_text().splitlines()
                              if line.startswith('+') and not line.startswith('+++'))
        start = additions.index('static bool op13_otg_machine(void)')
        end = additions.index('\n}', start) + 2
        actual = additions[start:end]
        source = Path(cls.directory.name) / 'identity.c'
        source.write_text(HARNESS.replace('ACTUAL_FUNCTION', actual))
        cls.binary = source.with_suffix('')
        result = subprocess.run(['gcc', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                                 '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                                 '-fno-pie', '-no-pie', str(source), '-o', str(cls.binary)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr)

    def check(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True,
                                timeout=5, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1'})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_compiled_identity_requires_both_compatibles(self): self.check('compiled')
    def test_physical_abl_identity_requires_sun(self): self.check('physical')
    def test_generic_sun_and_other_boards_rejected(self): self.check('wrong-board')
    def test_missing_or_wrong_chassis_rejected(self): self.check('chassis')
    def test_missing_model_or_root_rejected(self): self.check('missing')


if __name__ == '__main__':
    unittest.main()
