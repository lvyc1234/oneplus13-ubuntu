# SPDX-License-Identifier: GPL-2.0-only
"""Compile the actual LOC START builder/validator; no device or QRTR is opened.

r228 showed the modem rejecting START with QMI GeneralError(46) while the
helper sent the IDL default (accuracy omitted -> LOW). The only new variable in
this candidate is an explicit HIGH horizontal accuracy TLV, so these cases pin
the exact serialized bytes and keep the previous profile as a negative control.
"""
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

/* LOC START request TLVs as pinned by the Qualcomm IDL table
 * qmiLocStartReqMsgT_data_v02, little endian. */
static const guint8 expected[] = {
    0x01, 0x01, 0x00, 0x01,
    0x10, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00,
    0x11, 0x04, 0x00, 0x03, 0x00, 0x00, 0x00,
    0x12, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00,
    0x13, 0x04, 0x00, 0xe8, 0x03, 0x00, 0x00,
};

/* The profile every round up to r228 actually sent: the same four TLVs with
 * horizontal accuracy omitted, which the IDL documents as LOW. */
static const guint8 legacy[] = {
    0x01, 0x01, 0x00, 0x01,
    0x10, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00,
    0x12, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00,
    0x13, 0x04, 0x00, 0xe8, 0x03, 0x00, 0x00,
};

static QmiMessage *wire_response(const guint8 *payload, guint16 payload_len,
                                 gboolean include_result, guint8 flags,
                                 guint16 transaction, guint8 message_id)
{
    /* Complete QMUX + LOC service header, built independently of the parser. */
    guint8 header[] = { 1, 0, 0, 0x80, QMI_SERVICE_LOC, 0x27, flags,
                        0, 0, 0, 0, 0, 0 };
    GByteArray *wire = g_byte_array_new();
    GError *error = NULL;
    QmiMessage *reply;
    guint16 tlv_len = include_result ? 3 + payload_len : 0;
    guint16 frame_len = sizeof(header) + tlv_len - 1;
    header[1] = frame_len & 0xff;
    header[2] = frame_len >> 8;
    header[7] = transaction & 0xff;
    header[8] = transaction >> 8;
    header[9] = message_id;
    header[10] = 0;
    header[11] = tlv_len & 0xff;
    header[12] = tlv_len >> 8;
    g_byte_array_append(wire, header, sizeof(header));
    if (include_result) {
        guint8 tlv[] = { 0x02, payload_len & 0xff, payload_len >> 8 };
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

static void assert_rejected(QmiMessage *reply, GQuark domain, gint code,
                            guint16 transaction)
{
    GError *error = NULL;
    g_assert_false(start_result(reply, 0x27, transaction, &error));
    g_assert_error(error, domain, code);
    g_clear_error(&error);
    qmi_message_unref(reply);
}

static void check_profile(void)
{
    GError *error = NULL;
    QmiMessage *message;
    const guint8 *raw;
    gsize wire_length = 0;

    message = start_message(0x27, 0x1234, &error);
    g_assert_no_error(error);
    g_assert_nonnull(message);
    g_assert_true(qmi_message_is_request(message));
    g_assert_cmpint(qmi_message_get_service(message), ==, QMI_SERVICE_LOC);
    g_assert_cmpuint(qmi_message_get_client_id(message), ==, 0x27);
    g_assert_cmpuint(qmi_message_get_transaction_id(message), ==, 0x1234);
    g_assert_cmpuint(qmi_message_get_message_id(message), ==, 0x22);
    raw = qmi_message_get_raw(message, &wire_length, &error);
    g_assert_no_error(error);
    g_assert_cmpuint(wire_length, ==, 13 + sizeof(expected));
    g_assert_cmpmem(raw + 13, sizeof(expected), expected, sizeof(expected));
    qmi_message_unref(message);
}

static void check_legacy_profile(void)
{
    /* Negative control: the previous request is exactly this, and the new
     * candidate must differ from it only by the 7-byte accuracy TLV. */
    QmiMessage *message = qmi_message_new(QMI_SERVICE_LOC, 0x27, 0x1234, 0x22);
    const guint8 *raw;
    GError *error = NULL;
    guint8 session_id = 1;
    guint32 periodic = GUINT32_TO_LE(1), on = GUINT32_TO_LE(1);
    guint32 interval = GUINT32_TO_LE(1000);
    gsize wire_length = 0;

    g_assert_true(qmi_message_add_raw_tlv(message, 0x01, &session_id, 1, &error));
    g_assert_true(qmi_message_add_raw_tlv(message, 0x10, (const guint8 *)&periodic, 4, &error));
    g_assert_true(qmi_message_add_raw_tlv(message, 0x12, (const guint8 *)&on, 4, &error));
    g_assert_true(qmi_message_add_raw_tlv(message, 0x13, (const guint8 *)&interval, 4, &error));
    raw = qmi_message_get_raw(message, &wire_length, &error);
    g_assert_no_error(error);
    g_assert_cmpuint(wire_length, ==, 13 + sizeof(legacy));
    g_assert_cmpmem(raw + 13, sizeof(legacy), legacy, sizeof(legacy));
    g_assert_cmpuint(sizeof(expected), ==, sizeof(legacy) + 7);
    qmi_message_unref(message);
}

static void check_success(void)
{
    GError *error = NULL;
    QmiMessage *request = start_message(0x27, 0x1234, &error);
    QmiMessage *response;

    g_assert_no_error(error);
    response = qmi_message_response_new(request, QMI_PROTOCOL_ERROR_NONE);
    g_assert_true(start_result(response, 0x27, 0x1234, &error));
    g_assert_no_error(error);
    qmi_message_unref(response);
    /* The wire form of a successful acknowledgement, parsed independently. */
    {
        guint8 result[4] = { 0, 0, 0, 0 };
        response = wire_response(result, 4, TRUE, 2, 0x1234, 0x22);
        g_assert_true(start_result(response, 0x27, 0x1234, &error));
        g_assert_no_error(error);
        qmi_message_unref(response);
    }
    qmi_message_unref(request);
}

static void check_protocol_errors(void)
{
    const guint16 codes[] = { QMI_PROTOCOL_ERROR_GENERAL_ERROR,
        QMI_PROTOCOL_ERROR_INTERNAL, QMI_PROTOCOL_ERROR_UNKNOWN_ERROR,
        0x1234, 0xffff };
    GError *error = NULL;
    QmiMessage *request = start_message(0x27, 0x1234, &error);
    guint8 result[4] = { 1, 0, 0, 0 };
    guint i;

    g_assert_no_error(error);
    for (i = 0; i < G_N_ELEMENTS(codes); ++i) {
        QmiMessage *response;
        result[2] = codes[i] & 0xff;
        result[3] = codes[i] >> 8;
        response = wire_response(result, 4, TRUE, 2, 0x1234, 0x22);
        g_assert_false(start_result(response, 0x27, 0x1234, &error));
        g_assert_error(error, QMI_PROTOCOL_ERROR, codes[i]);
        g_clear_error(&error);
        qmi_message_unref(response);
        response = qmi_message_response_new(request, (QmiProtocolError)codes[i]);
        g_assert_false(start_result(response, 0x27, 0x1234, &error));
        g_assert_error(error, QMI_PROTOCOL_ERROR, codes[i]);
        g_clear_error(&error);
        qmi_message_unref(response);
    }
    qmi_message_unref(request);
}

static void check_general_error_text(void)
{
    /* tools/oneplus13/ram-gnss-check.py classifies a rejected START by this
     * exact text, so the raw builder must reproduce libqmi's formatting. */
    GError *error = NULL;
    guint8 result[4] = { 1, 0, 46, 0 };
    QmiMessage *response = wire_response(result, 4, TRUE, 2, 0x1234, 0x22);

    g_assert_false(start_result(response, 0x27, 0x1234, &error));
    g_assert_error(error, QMI_PROTOCOL_ERROR, QMI_PROTOCOL_ERROR_GENERAL_ERROR);
    g_assert_cmpstr(error->message, ==,
                    "QMI protocol error (46): 'GeneralError'");
    g_clear_error(&error);
    qmi_message_unref(response);
}

static void check_malformed_results(void)
{
    guint8 result[5] = { 0, 0, 0, 0, 0 };
    const guint8 inconsistent[][4] = { { 0, 0, 1, 0 }, { 1, 0, 0, 0 },
                                       { 2, 0, 0, 0 }, { 2, 0, 1, 0 },
                                       { 0xff, 0xff, 0xff, 0xff } };
    guint i;

    assert_rejected(wire_response(result, 0, FALSE, 2, 0x1234, 0x22),
                    G_IO_ERROR, G_IO_ERROR_INVALID_DATA, 0x1234);
    for (i = 0; i <= 5; ++i)
        if (i != 4)
            assert_rejected(wire_response(result, i, TRUE, 2, 0x1234, 0x22),
                            G_IO_ERROR, G_IO_ERROR_INVALID_DATA, 0x1234);
    for (i = 0; i < G_N_ELEMENTS(inconsistent); ++i)
        assert_rejected(wire_response(inconsistent[i], 4, TRUE, 2, 0x1234, 0x22),
                        G_IO_ERROR, G_IO_ERROR_INVALID_DATA, 0x1234);
}

static void check_identity(void)
{
    guint8 result[4] = { 0, 0, 0, 0 };

    /* Wrong message id, wrong transaction, indication instead of response and
     * a foreign client id must all be rejected before any result is trusted. */
    assert_rejected(wire_response(result, 4, TRUE, 2, 0x1234, 0x21),
                    G_IO_ERROR, G_IO_ERROR_INVALID_DATA, 0x1234);
    assert_rejected(wire_response(result, 4, TRUE, 2, 0x1235, 0x22),
                    G_IO_ERROR, G_IO_ERROR_INVALID_DATA, 0x1234);
    assert_rejected(wire_response(result, 4, TRUE, 4, 0x1234, 0x22),
                    G_IO_ERROR, G_IO_ERROR_INVALID_DATA, 0x1234);
}

static void check_lock_set(void)
{
    /* SET_ENGINE_LOCK 0x003a carries only the mandatory lockType TLV. */
    static const guint8 none_bytes[] = { 0x01, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00 };
    static const guint8 all_bytes[] = { 0x01, 0x04, 0x00, 0x04, 0x00, 0x00, 0x00 };
    QmiMessage *request, *response;
    GError *error = NULL;
    gsize wire_length = 0;
    const guint8 *raw;

    request = lock_set_message(0x27, 0x1234, 1, &error);
    g_assert_no_error(error);
    g_assert_nonnull(request);
    g_assert_true(qmi_message_is_request(request));
    g_assert_cmpint(qmi_message_get_service(request), ==, QMI_SERVICE_LOC);
    g_assert_cmpuint(qmi_message_get_message_id(request), ==, 0x3a);
    g_assert_cmpuint(qmi_message_get_client_id(request), ==, 0x27);
    g_assert_cmpuint(qmi_message_get_transaction_id(request), ==, 0x1234);
    raw = qmi_message_get_raw(request, &wire_length, &error);
    g_assert_no_error(error);
    g_assert_cmpuint(wire_length, ==, 13 + sizeof(none_bytes));
    g_assert_cmpmem(raw + 13, sizeof(none_bytes), none_bytes, sizeof(none_bytes));
    response = qmi_message_response_new(request, QMI_PROTOCOL_ERROR_NONE);
    g_assert_true(lock_set_response_valid(response, 0x27, 0x1234));
    /* The read-only GET validator must not accept a SET transaction. */
    g_assert_false(lock_response_valid(response, 0x27, 0x1234));
    g_assert_false(lock_set_response_valid(response, 0x27, 0x1235));
    g_assert_false(lock_set_response_valid(response, 0x28, 0x1234));
    qmi_message_unref(response);
    qmi_message_unref(request);

    request = lock_set_message(0x27, 0x1234, 4, &error);
    g_assert_no_error(error);
    raw = qmi_message_get_raw(request, &wire_length, &error);
    g_assert_no_error(error);
    g_assert_cmpuint(wire_length, ==, 13 + sizeof(all_bytes));
    g_assert_cmpmem(raw + 13, sizeof(all_bytes), all_bytes, sizeof(all_bytes));
    response = qmi_message_response_new(request, QMI_PROTOCOL_ERROR_GENERAL_ERROR);
    g_assert_false(lock_set_response_valid(response, 0x27, 0x1234));
    qmi_message_unref(response);
    qmi_message_unref(request);
}

int main(int argc, char **argv)
{
    g_assert_cmpint(argc, ==, 2);
    if (!strcmp(argv[1], "profile"))
        check_profile();
    else if (!strcmp(argv[1], "legacy"))
        check_legacy_profile();
    else if (!strcmp(argv[1], "success"))
        check_success();
    else if (!strcmp(argv[1], "protocol"))
        check_protocol_errors();
    else if (!strcmp(argv[1], "general"))
        check_general_error_text();
    else if (!strcmp(argv[1], "malformed"))
        check_malformed_results();
    else if (!strcmp(argv[1], "identity"))
        check_identity();
    else if (!strcmp(argv[1], "lock-set"))
        check_lock_set();
    else
        g_assert_not_reached();
    return 0;
}
'''


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc')
                     and shutil.which('pkg-config'), 'Actual LOC harness requires Linux GCC')
class LocationStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'qmi-glib',
                                'qrtr-glib', 'gio-2.0'], capture_output=True, text=True,
                               check=False, timeout=10)
        if flags.returncode:
            raise unittest.SkipTest('libqmi/QRTR development packages unavailable')
        cls.directory = tempfile.TemporaryDirectory(prefix='op13-loc-start-')
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
            capture_output=True, text=True, check=False, timeout=60)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)

    def check_case(self, name):
        environment = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:halt_on_error=1',
                           UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        result = subprocess.run([str(self.binary), name], env=environment,
                                capture_output=True, text=True, check=False, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_start_request_pins_high_accuracy_profile_bytes(self):
        self.check_case('profile')

    def test_previous_profile_differs_only_by_accuracy_tlv(self):
        self.check_case('legacy')

    def test_successful_acknowledgement_is_accepted(self):
        self.check_case('success')

    def test_protocol_errors_preserve_full_little_endian_code(self):
        self.check_case('protocol')

    def test_general_error_text_matches_evidence_parser_contract(self):
        self.check_case('general')

    def test_missing_short_extra_and_inconsistent_results_are_rejected(self):
        self.check_case('malformed')

    def test_wrong_message_transaction_or_indication_is_rejected(self):
        self.check_case('identity')

    def test_engine_lock_set_request_pins_type_only_tlv(self):
        self.check_case('lock-set')


if __name__ == '__main__':
    unittest.main()
