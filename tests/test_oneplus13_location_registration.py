# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual LOC registration functions; no device or QRTR is opened."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#define main oneplus13_location_session_unused_main
#include "location-session.c"
#undef main
#include <string.h>

static QmiMessage *response(guint16 message_id, QmiService service)
{
    QmiMessage *request = qmi_message_new(service, 0x27, 0x1234, message_id);
    QmiMessage *reply = qmi_message_response_new(request, QMI_PROTOCOL_ERROR_NONE);
    qmi_message_unref(request);
    return reply;
}

static QmiMessage *wire_response(const guint8 *payload, guint16 payload_len,
                                 gboolean include_result, guint8 flags)
{
    /* Complete QMUX + LOC service header, built independently of the parser. */
    guint8 header[] = { 1, 0, 0, 0x80, QMI_SERVICE_LOC, 0x27, flags,
                        0x34, 0x12, 0x21, 0, 0, 0 };
    GByteArray *wire = g_byte_array_new();
    GError *error = NULL;
    QmiMessage *reply;
    guint16 tlv_len = include_result ? 3 + payload_len : 0;
    guint16 frame_len = sizeof(header) + tlv_len - 1;
    header[1] = frame_len & 0xff;
    header[2] = frame_len >> 8;
    header[11] = tlv_len & 0xff;
    header[12] = tlv_len >> 8;
    g_byte_array_append(wire, header, sizeof(header));
    if (include_result) {
        guint8 tlv[] = { 2, payload_len & 0xff, payload_len >> 8 };
        g_byte_array_append(wire, tlv, sizeof(tlv));
        g_byte_array_append(wire, payload, payload_len);
    }
    reply = qmi_message_new_from_raw(wire, &error);
    g_assert_no_error(error);
    g_assert_nonnull(reply);
    g_assert_cmpuint(wire->len, ==, 0);
    g_byte_array_unref(wire);
    return reply;
}

static void assert_rejected(QmiMessage *reply, GQuark domain, gint code)
{
    GError *error = NULL;
    g_assert_false(registration_result(reply, &error));
    g_assert_error(error, domain, code);
    g_clear_error(&error);
    qmi_message_unref(reply);
}

static void check_builder(gboolean position_only)
{
    GError *error = NULL;
    QmiMessage *message = registration_message(0x27, 0x1234, position_only, &error);
    const guint8 *raw;
    guint16 length;
    gsize wire_length;
    /* NMEA is bit2, position is bit0; TLVs have no internal string prefix. */
    guint8 expected[] = {
        0x01, 0x08, 0x00, position_only ? 0x01 : 0x04, 0, 0, 0, 0, 0, 0, 0,
        0x10, 0x04, 0x00, 'O', 'P', '1', '3',
        0x11, 0x04, 0x00, 1, 0, 0, 0,
    };
    g_assert_no_error(error);
    g_assert_nonnull(message);
    g_assert_true(qmi_message_is_request(message));
    g_assert_cmpint(qmi_message_get_service(message), ==, QMI_SERVICE_LOC);
    g_assert_cmpuint(qmi_message_get_client_id(message), ==, 0x27);
    g_assert_cmpuint(qmi_message_get_transaction_id(message), ==, 0x1234);
    g_assert_cmpuint(qmi_message_get_message_id(message), ==, 0x21);
    raw = qmi_message_get_raw_tlv(message, 0x10, &length);
    g_assert_cmpuint(length, ==, 4);
    g_assert_cmpmem(raw, length, "OP13", 4);
    raw = qmi_message_get_raw(message, &wire_length, &error);
    g_assert_no_error(error);
    g_assert_cmpuint(wire_length, ==, 13 + sizeof(expected));
    g_assert_cmpmem(raw + 13, sizeof(expected), expected, sizeof(expected));
    qmi_message_unref(message);
}

int main(int argc, char **argv)
{
    GError *error = NULL;
    QmiMessage *reply;
    guint8 result[5] = {0};
    guint i;
    g_assert_cmpint(argc, ==, 2);
    if (!strcmp(argv[1], "nmea")) {
        check_builder(FALSE);
    } else if (!strcmp(argv[1], "position")) {
        check_builder(TRUE);
    } else if (!strcmp(argv[1], "success")) {
        reply = response(0x21, QMI_SERVICE_LOC);
        g_assert_true(registration_result(reply, &error));
        g_assert_no_error(error);
        qmi_message_unref(reply);
        reply = wire_response(result, 4, TRUE, 2);
        g_assert_true(registration_result(reply, &error));
        g_assert_no_error(error);
        qmi_message_unref(reply);
    } else if (!strcmp(argv[1], "protocol")) {
        const guint16 codes[] = { QMI_PROTOCOL_ERROR_INVALID_ARGUMENT,
            QMI_PROTOCOL_ERROR_NOT_SUPPORTED, QMI_PROTOCOL_ERROR_GENERAL_ERROR,
            0x1234, 0xffff };
        for (i = 0; i < G_N_ELEMENTS(codes); ++i) {
            result[0] = 1; result[1] = 0;
            result[2] = codes[i] & 0xff; result[3] = codes[i] >> 8;
            assert_rejected(wire_response(result, 4, TRUE, 2), QMI_PROTOCOL_ERROR, codes[i]);
        }
    } else if (!strcmp(argv[1], "missing")) {
        assert_rejected(wire_response(result, 0, FALSE, 2), G_IO_ERROR, G_IO_ERROR_INVALID_DATA);
    } else if (!strcmp(argv[1], "lengths")) {
        for (i = 0; i <= 5; ++i)
            if (i != 4)
                assert_rejected(wire_response(result, i, TRUE, 2), G_IO_ERROR, G_IO_ERROR_INVALID_DATA);
    } else if (!strcmp(argv[1], "inconsistent")) {
        const guint8 values[][4] = { {0,0,1,0}, {1,0,0,0}, {2,0,0,0},
                                     {2,0,1,0}, {0,1,1,0}, {0xff,0xff,0xff,0xff} };
        for (i = 0; i < G_N_ELEMENTS(values); ++i)
            assert_rejected(wire_response(values[i], 4, TRUE, 2), G_IO_ERROR, G_IO_ERROR_INVALID_DATA);
    } else if (!strcmp(argv[1], "identity")) {
        assert_rejected(response(0x22, QMI_SERVICE_LOC), G_IO_ERROR, G_IO_ERROR_INVALID_DATA);
        assert_rejected(response(0x21, QMI_SERVICE_DMS), G_IO_ERROR, G_IO_ERROR_INVALID_DATA);
        assert_rejected(qmi_message_new(QMI_SERVICE_LOC, 0x27, 0x1234, 0x21), G_IO_ERROR, G_IO_ERROR_INVALID_DATA);
        assert_rejected(wire_response(result, 4, TRUE, 4), G_IO_ERROR, G_IO_ERROR_INVALID_DATA);
    } else if (!strcmp(argv[1], "retry")) {
        result[0] = 1; result[2] = QMI_PROTOCOL_ERROR_INVALID_ARGUMENT;
        reply = wire_response(result, 4, TRUE, 2);
        g_assert_false(registration_result(reply, &error));
        g_assert_true(retry_position_events(error, FALSE));
        g_assert_false(retry_position_events(error, TRUE));
        g_clear_error(&error);
        qmi_message_unref(reply);
        reply = wire_response(result, 3, TRUE, 2);
        g_assert_false(registration_result(reply, &error));
        g_assert_false(retry_position_events(error, FALSE));
        g_clear_error(&error);
        qmi_message_unref(reply);
    } else {
        g_assert_not_reached();
    }
    return 0;
}
'''


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc')
                     and shutil.which('pkg-config'), 'Actual LOC harness requires Linux GCC')
class LocationRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'qmi-glib',
                                'qrtr-glib', 'gio-2.0'], capture_output=True, text=True,
                               check=False, timeout=10)
        if flags.returncode:
            raise unittest.SkipTest('libqmi/QRTR development packages unavailable')
        cls.directory = tempfile.TemporaryDirectory(prefix='op13-loc-registration-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'harness.c'
        source.write_text(HARNESS)
        cls.binary = Path(cls.directory.name) / 'harness'
        compiled = subprocess.run(
            ['gcc', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
             '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
             '-fno-omit-frame-pointer', '-fno-pie', '-no-pie',
             '-I', str(ROOT / 'tools/oneplus13'), str(source),
             '-o', str(cls.binary), *shlex.split(flags.stdout)],
            capture_output=True, text=True, check=False, timeout=30)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)

    def check_case(self, name):
        environment = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:halt_on_error=1',
                           UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        result = subprocess.run([str(self.binary), name], env=environment,
                                capture_output=True, text=True, check=False, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_nmea_request_has_exact_little_endian_mask_and_afw_identity(self):
        self.check_case('nmea')

    def test_position_request_preserves_client_identity_and_transaction(self):
        self.check_case('position')

    def test_success_requires_consistent_four_byte_result(self):
        self.check_case('success')

    def test_protocol_errors_preserve_full_little_endian_error_code(self):
        self.check_case('protocol')

    def test_missing_result_is_rejected(self):
        self.check_case('missing')

    def test_short_and_extra_result_lengths_are_rejected(self):
        self.check_case('lengths')

    def test_inconsistent_result_and_error_pairs_are_rejected(self):
        self.check_case('inconsistent')

    def test_wrong_service_message_request_and_indication_are_rejected(self):
        self.check_case('identity')

    def test_only_valid_invalid_argument_result_enables_single_retry(self):
        self.check_case('retry')


if __name__ == '__main__':
    unittest.main()
