# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the actual OTG response decoder with GCC, without hardware I/O."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "otg-protocol.h"

#define SENTINEL 0xa5b6c7d8U
#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "check failed at line %d: %s\n", __LINE__, #condition); \
        return 1; \
    } \
} while (0)

static void word(unsigned char *p, unsigned int value)
{
    unsigned int i;
    for (i = 0; i < 4; i++)
        p[i] = (unsigned char)(value >> (i * 8));
}

static void packet(unsigned char *p, unsigned int opcode,
                   unsigned int property, unsigned int value,
                   unsigned int result)
{
    word(p, 32778U);
    word(p + 4, 1U);
    word(p + 8, opcode);
    word(p + 12, property);
    word(p + 16, value);
    word(p + 20, result);
}

static int expect(const unsigned char *p, unsigned int len,
                  unsigned int opcode, unsigned int property,
                  int wanted, unsigned int wanted_value)
{
    unsigned int output = SENTINEL;
    int actual = op13_otg_decode(p, len, opcode, property, &output);
    CHECK(actual == wanted);
    CHECK(output == (wanted == 1 ? wanted_value : SENTINEL));
    return 0;
}

int main(int argc, char **argv)
{
    unsigned char bytes[25];
    unsigned char *p = bytes;
    unsigned int op, prop, val, i, bit;
    const unsigned int operations[] = { OP13_OTG_GET, OP13_OTG_SET };
    const unsigned int properties[] = { OP13_OTG_AP_ENABLE, OP13_OTG_SWITCH };
    const unsigned int invalid[] = { 0U, 1U, 16U, 19U, 0x10000011U, UINT_MAX };

    CHECK(argc == 2);
    CHECK(sizeof(unsigned int) == 4);
    packet(p, OP13_OTG_GET, OP13_OTG_AP_ENABLE, 1U, 0U);
    if (!strcmp(argv[1], "constants")) {
        CHECK(OP13_OTG_GET == 0x32U && OP13_OTG_SET == 0x33U);
        CHECK(OP13_OTG_AP_ENABLE == 17U && OP13_OTG_SWITCH == 18U);
        word(p, 0x89abcdefU);
        CHECK(op13_otg_le32(p) == 0x89abcdefU);
    } else if (!strcmp(argv[1], "success")) {
        for (op = 0; op < 2; op++)
            for (prop = 0; prop < 2; prop++)
                for (val = 0; val < 2; val++) {
                    packet(p, operations[op], properties[prop], val, 0U);
                    CHECK(!expect(p, 24, operations[op], properties[prop], 1,
                                  operations[op] == OP13_OTG_GET ? val : 0));
                }
    } else if (!strcmp(argv[1], "lengths")) {
        for (i = 0; i < 24; i++) {
            /* Deliberately short allocation: rejection must precede reads. */
            unsigned char *short_packet = malloc(i ? i : 1);
            CHECK(short_packet != NULL);
            memset(short_packet, 0xff, i ? i : 1);
            CHECK(!expect(short_packet, i, OP13_OTG_GET, OP13_OTG_AP_ENABLE, 0, 0));
            free(short_packet);
        }
        CHECK(!expect(p, 25, OP13_OTG_GET, OP13_OTG_AP_ENABLE, 0, 0));
        CHECK(!expect(p, UINT_MAX, OP13_OTG_GET, OP13_OTG_AP_ENABLE, 0, 0));
    } else if (!strcmp(argv[1], "nulls")) {
        CHECK(!expect(NULL, 24, OP13_OTG_GET, OP13_OTG_AP_ENABLE, 0, 0));
        CHECK(op13_otg_decode(p, 24, OP13_OTG_GET, OP13_OTG_AP_ENABLE, NULL) == 0);
        CHECK(op13_otg_decode(NULL, 24, 0, 0, NULL) == 0);
    } else if (!strcmp(argv[1], "pending")) {
        for (i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) {
            CHECK(!expect(p, 24, invalid[i], OP13_OTG_AP_ENABLE, 0, 0));
            CHECK(!expect(p, 24, OP13_OTG_GET, invalid[i], 0, 0));
        }
        CHECK(!expect(p, 24, OP13_OTG_SET, OP13_OTG_AP_ENABLE, 0, 0));
        CHECK(!expect(p, 24, OP13_OTG_GET, OP13_OTG_SWITCH, 0, 0));
    } else if (!strcmp(argv[1], "identity")) {
        /* Every bit of owner/type/opcode/property must match exactly. */
        for (i = 0; i < 16; i++)
            for (bit = 0; bit < 8; bit++) {
                packet(p, OP13_OTG_GET, OP13_OTG_AP_ENABLE, 1, 0);
                p[i] ^= (unsigned char)(1U << bit);
                CHECK(!expect(p, 24, OP13_OTG_GET, OP13_OTG_AP_ENABLE, 0, 0));
            }
        /* Network-byte-order owner is not an accepted little-endian owner. */
        packet(p, OP13_OTG_GET, OP13_OTG_AP_ENABLE, 1, 0);
        p[0] = 0; p[1] = 0; p[2] = 0x80; p[3] = 0x0a;
        CHECK(!expect(p, 24, OP13_OTG_GET, OP13_OTG_AP_ENABLE, 0, 0));
    } else if (!strcmp(argv[1], "errors")) {
        for (op = 0; op < 2; op++)
            for (prop = 0; prop < 2; prop++) {
                for (bit = 0; bit < 32; bit++) {
                    packet(p, operations[op], properties[prop], 0, 1U << bit);
                    CHECK(!expect(p, 24, operations[op], properties[prop], -1, 0));
                }
                for (bit = 1; bit < 32; bit++) {
                    packet(p, operations[op], properties[prop], 1U << bit, 0);
                    CHECK(!expect(p, 24, operations[op], properties[prop],
                                  operations[op] == OP13_OTG_GET ? -1 : 1, 0));
                }
                packet(p, operations[op], properties[prop], UINT_MAX, UINT_MAX);
                CHECK(!expect(p, 24, operations[op], properties[prop], -1, 0));
            }
        /* A firmware error for a different transaction remains unrelated. */
        CHECK(!expect(p, 24, 0, OP13_OTG_AP_ENABLE, 0, 0));
    } else if (!strcmp(argv[1], "unaligned")) {
        packet(bytes + 1, OP13_OTG_GET, OP13_OTG_SWITCH, 1, 0);
        CHECK(!expect(bytes + 1, 24, OP13_OTG_GET, OP13_OTG_SWITCH, 1, 1));
    } else {
        CHECK(0);
    }
    return 0;
}
'''


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc'),
                     'Actual decoder harness requires Linux GCC')
class OtgProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='op13-otg-protocol-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'harness.c'
        source.write_text(HARNESS)
        cls.binary = Path(cls.directory.name) / 'harness'
        compiled = subprocess.run(
            ['gcc', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
             '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
             '-fno-omit-frame-pointer', '-fno-pie', '-no-pie',
             '-I', str(ROOT / 'tools/oneplus13/usb-draft'),
             str(source), '-o', str(cls.binary)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)

    def check_case(self, case):
        environment = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:halt_on_error=1',
                           UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        result = subprocess.run([str(self.binary), case], env=environment,
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_vendor_constants_and_little_endian_word(self):
        self.check_case('constants')

    def test_both_boolean_values_for_each_get_set_property_pair(self):
        self.check_case('success')

    def test_every_short_length_and_extra_length_are_rejected(self):
        self.check_case('lengths')

    def test_null_inputs_are_rejected(self):
        self.check_case('nulls')

    def test_no_pending_and_different_pending_are_rejected(self):
        self.check_case('pending')

    def test_each_identity_bit_and_wrong_endianness_are_rejected(self):
        self.check_case('identity')

    def test_firmware_errors_and_non_boolean_values_preserve_output(self):
        self.check_case('errors')

    def test_unaligned_response_has_no_alignment_requirement(self):
        self.check_case('unaligned')


if __name__ == '__main__':
    unittest.main()
