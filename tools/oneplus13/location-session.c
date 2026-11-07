// SPDX-License-Identifier: GPL-2.0-only
/*
 * Keep one QRTR LOC client across setup, start, NMEA indications, and stop.
 * qmicli executes one request per process, which closes the QRTR client even
 * when --client-no-release-cid is used.
 */

#include <gio/gio.h>
#include <glib-unix.h>
#include <libqmi-glib.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

struct session {
	GMainLoop *loop;
	GCancellable *cancellable;
	QmiDevice *device;
	QmiClientLoc *client;
	guint timeout_id;
	guint deadline_id;
	guint nmea_count;
	guint requested_seconds;
	gboolean started;
	gboolean start_requested;
	gboolean start_pending;
	gboolean terminate_requested;
	gboolean stopped;
	gboolean stop_requested;
	gboolean position_events_only;
	gboolean lock_diagnostic;
	gboolean lock_response_ok;
	gboolean lock_indication_ok;
	guint16 start_transaction;
	/* Optional bounded engine-lock experiment: read the legacy lock, set
	 * LOCK_NONE for this session only, and restore the saved value before
	 * exit. Disabled unless --unlock-engine-lock is passed. */
	gboolean unlock_engine_lock;
	gboolean lock_changed;
	gboolean lock_get_pending;
	gboolean lock_restore_pending;
	gboolean lock_applied;
	gboolean lock_restored;
	int lock_phase;
	int restore_status;
	guint32 saved_lock_type;
	guint16 lock_transaction;
	gchar *lock_report;
};

static int exit_status = 1;
static gboolean terminate (gpointer user_data);
static gboolean unix_terminate (gpointer user_data);
static QmiMessage *lock_set_message (guint8 cid, guint16 transaction,
				    guint32 lock_type, GError **error);
static gboolean lock_response_valid (QmiMessage *message, guint8 cid,
				     guint16 transaction);
static gboolean lock_set_response_valid (QmiMessage *message, guint8 cid,
					 guint16 transaction);
static void lock_ready (QmiDevice *device, GAsyncResult *result,
			gpointer user_data);

/* libqmi 1.40 LOC client-identification TLVs, encoded with the stable raw
 * message API so Ubuntu's older libqmi runtime ABI remains usable.
 * Upstream schema: 532de37e84dac82936b056e28fb71ce4c7af60d6.
 * This helper supplies the Linux system location provider, so it registers
 * as the application framework (AFW=1), never privileged (3). OP13 is its
 * own identifier; no Android HAL identity or client-type scan is used.
 */
static QmiMessage *
registration_message (guint8 cid, guint16 transaction, gboolean position_only,
		      GError **error)
{
	QmiMessage *message = qmi_message_new (QMI_SERVICE_LOC, cid, transaction, 0x0021);
	guint64 mask = GUINT64_TO_LE (position_only ?
		QMI_LOC_EVENT_REGISTRATION_FLAG_POSITION_REPORT :
		QMI_LOC_EVENT_REGISTRATION_FLAG_NMEA);
	guint32 client_type = GUINT32_TO_LE (1);
	const guint8 client_id[] = { 'O', 'P', '1', '3' };

	if (!qmi_message_add_raw_tlv (message, 0x01, (const guint8 *)&mask,
				     sizeof(mask), error) ||
	    !qmi_message_add_raw_tlv (message, 0x10, client_id, sizeof(client_id), error) ||
	    !qmi_message_add_raw_tlv (message, 0x11, (const guint8 *)&client_type,
				     sizeof(client_type), error)) {
		qmi_message_unref (message);
		return NULL;
	}
	return message;
}

static gboolean
registration_result (QmiMessage *message, GError **error)
{
	guint16 length = 0;
	const guint8 *raw;
	guint16 result, code;

	if (!qmi_message_is_response (message) ||
	    qmi_message_get_service (message) != QMI_SERVICE_LOC ||
	    qmi_message_get_message_id (message) != 0x0021) {
		g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
				     "unexpected LOC registration response");
		return FALSE;
	}
	raw = qmi_message_get_raw_tlv (message, 0x02, &length);
	if (!raw || length != 4) {
		g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
				     "invalid LOC registration result TLV");
		return FALSE;
	}
	result = raw[0] | ((guint16)raw[1] << 8);
	code = raw[2] | ((guint16)raw[3] << 8);
	if (!result && !code)
		return TRUE;
	if (result != 1 || !code) {
		g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
				     "inconsistent LOC registration result");
		return FALSE;
	}
	g_set_error (error, QMI_PROTOCOL_ERROR, code,
		     "LOC registration protocol error %u", code);
	return FALSE;
}

/* Qualcomm location_service_v02 START (0x0022) request TLVs, taken from the
 * pinned IDL table qmiLocStartReqMsgT_data_v02:
 *   0x01 sessionId u8, 0x10 fixRecurrence u32, 0x11 horizontalAccuracyLevel u32,
 *   0x12 intermediateReportState u32, 0x13 minInterval u32.
 * Enum values: eQMI_LOC_RECURRENCE_PERIODIC=1, eQMI_LOC_ACCURACY_HIGH=3,
 * eQMI_LOC_INTERMEDIATE_REPORTS_ON=1. The same IDL documents that an omitted
 * horizontal accuracy defaults to LOW, which is what every earlier round sent.
 *
 * Ubuntu's libqmi 1.38 exposes only the session/recurrence/intermediate/interval
 * setters and no accuracy level, so the profile is encoded once here with the
 * stable raw message API. tests/test_oneplus13_location_start.py verifies the
 * exact serialized bytes with the installed libqmi and keeps a legacy-profile
 * negative control, so this stays a single documented experimental variable
 * instead of an unverifiable hand-built packet.
 */
#define OP13_LOC_START_REQUEST 0x0022
#define OP13_LOC_START_RESULT_TLV 0x02
#define OP13_LOC_START_TLV_SESSION_ID 0x01
#define OP13_LOC_START_TLV_FIX_RECURRENCE 0x10
#define OP13_LOC_START_TLV_HORIZONTAL_ACCURACY 0x11
#define OP13_LOC_START_TLV_INTERMEDIATE_REPORT 0x12
#define OP13_LOC_START_TLV_MIN_INTERVAL 0x13
#define OP13_LOC_START_SESSION_ID 1
#define OP13_LOC_START_RECURRENCE_PERIODIC 1
#define OP13_LOC_START_ACCURACY_HIGH 3
#define OP13_LOC_START_INTERMEDIATE_ON 1
#define OP13_LOC_START_MIN_INTERVAL_MS 1000

/* Qualcomm location_service_v02 engine-lock messages:
 *   GET_ENGINE_LOCK 0x003B (empty request; indication carries lockType 0x10)
 *   SET_ENGINE_LOCK 0x003A (lockType 0x01 u32 mandatory, subType 0x10,
 *                           lockClient 0x11 u64, clientsConfig 0x12 u64 optional)
 * Legacy lock values: NONE=1, MI(AFW)=2, MT=3, ALL=4.
 *
 * r230 observed lockType=4 (LOCK_ALL) with an all-ones client mask while event
 * registration and STOP still succeeded and every START failed generically.
 * LOCK_ALL is the standing candidate for that shape, but who owns the persisted
 * lock is NOT established. This helper therefore offers an opt-in, bounded
 * cycle: read and save the lock, set NONE once, confirm by readback, run the
 * normal session, then restore the saved lockType and confirm the restore. An
 * unproven restore is reported and is never treated as clean.
 */
#define OP13_LOC_SET_ENGINE_LOCK 0x003A
#define OP13_LOC_GET_ENGINE_LOCK 0x003B
#define OP13_LOC_LOCK_TLV_TYPE 0x01
#define OP13_LOC_LOCK_RESULT_TLV 0x02
#define OP13_LOC_LOCK_NONE 1
#define OP13_LOC_LOCK_MI 2
#define OP13_LOC_LOCK_MT 3
#define OP13_LOC_LOCK_ALL 4
#define OP13_LOC_LOCK_TLV_SUB_TYPE 0x10
#define OP13_LOC_LOCK_TLV_LOCK_CLIENT 0x11
#define OP13_LOC_LOCK_TLV_CLIENTS_CONFIG 0x12

enum lock_phase {
	LOCK_PHASE_NONE,
	LOCK_PHASE_SAVE,
	LOCK_PHASE_CONFIRM_NONE,
	LOCK_PHASE_CONFIRM_RESTORE,
};

static gboolean
add_u32_tlv (QmiMessage *message, guint8 type, guint32 value, GError **error)
{
	guint32 little = GUINT32_TO_LE (value);

	return qmi_message_add_raw_tlv (message, type, (const guint8 *)&little,
				       sizeof (little), error);
}

static QmiMessage *
start_message (guint8 cid, guint16 transaction, GError **error)
{
	QmiMessage *message;
	guint8 session_id = OP13_LOC_START_SESSION_ID;

	message = qmi_message_new (QMI_SERVICE_LOC, cid, transaction,
				   OP13_LOC_START_REQUEST);
	if (!message) {
		g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_NO_SPACE,
				     "cannot allocate LOC start request");
		return NULL;
	}
	if (!qmi_message_add_raw_tlv (message, OP13_LOC_START_TLV_SESSION_ID,
				      &session_id, sizeof (session_id), error) ||
	    !add_u32_tlv (message, OP13_LOC_START_TLV_FIX_RECURRENCE,
			  OP13_LOC_START_RECURRENCE_PERIODIC, error) ||
	    !add_u32_tlv (message, OP13_LOC_START_TLV_HORIZONTAL_ACCURACY,
			  OP13_LOC_START_ACCURACY_HIGH, error) ||
	    !add_u32_tlv (message, OP13_LOC_START_TLV_INTERMEDIATE_REPORT,
			  OP13_LOC_START_INTERMEDIATE_ON, error) ||
	    !add_u32_tlv (message, OP13_LOC_START_TLV_MIN_INTERVAL,
			  OP13_LOC_START_MIN_INTERVAL_MS, error)) {
		qmi_message_unref (message);
		return NULL;
	}
	return message;
}

/* Reproduce libqmi's own response formatting so existing evidence parsers keep
 * seeing the identical "QMI protocol error (N): 'Nick'" text that the typed
 * client call used to generate. */
static void
set_protocol_error (GError **error, guint16 code)
{
	GEnumClass *klass = g_type_class_ref (QMI_TYPE_PROTOCOL_ERROR);
	GEnumValue *value = g_enum_get_value (klass, code);
	const gchar *nick = value ? value->value_nick : "Unknown";

	g_set_error (error, QMI_PROTOCOL_ERROR, code,
		     "QMI protocol error (%u): '%s'", (guint) code, nick);
	g_type_class_unref (klass);
}

/* Shared response check for the raw LOC commands this helper sends. The error
 * text for a firmware failure is byte-identical to libqmi's own formatting so
 * existing evidence parsers keep matching. */
static gboolean
command_result (QmiMessage *message, guint16 message_id, guint8 cid,
		guint16 transaction, const char *what, GError **error)
{
	guint16 length = 0;
	const guint8 *raw;
	guint16 result, code;

	if (!qmi_message_is_response (message) ||
	    qmi_message_get_service (message) != QMI_SERVICE_LOC ||
	    qmi_message_get_message_id (message) != message_id ||
	    qmi_message_get_client_id (message) != cid ||
	    qmi_message_get_transaction_id (message) != transaction) {
		g_set_error (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
			     "unexpected LOC %s response", what);
		return FALSE;
	}
	raw = qmi_message_get_raw_tlv (message, OP13_LOC_LOCK_RESULT_TLV, &length);
	if (!raw || length != 4) {
		g_set_error (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
			     "invalid LOC %s result TLV", what);
		return FALSE;
	}
	result = raw[0] | ((guint16)raw[1] << 8);
	code = raw[2] | ((guint16)raw[3] << 8);
	if (!result && !code)
		return TRUE;
	if (result != 1 || !code) {
		g_set_error (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
			     "inconsistent LOC %s result", what);
		return FALSE;
	}
	set_protocol_error (error, code);
	return FALSE;
}

static gboolean
start_result (QmiMessage *message, guint8 cid, guint16 transaction,
	      GError **error)
{
	return command_result (message, OP13_LOC_START_REQUEST, cid, transaction,
			       "start", error);
}

static gboolean
claim_stop (struct session *session)
{
	if (session->stop_requested)
		return FALSE;
	session->stop_requested = TRUE;
	if (session->timeout_id) {
		g_source_remove (session->timeout_id);
		session->timeout_id = 0;
	}
	return TRUE;
}

static gboolean
self_test_timer (gpointer user_data)
{
	(void)user_data;
	return G_SOURCE_CONTINUE;
}

static gboolean
nmea_types_error_is_optional (const GError *error)
{
	return g_error_matches (error, QMI_PROTOCOL_ERROR,
				QMI_PROTOCOL_ERROR_NOT_SUPPORTED);
}

static gboolean
retry_position_events (const GError *error, gboolean already_retried)
{
	return !already_retried && g_error_matches (error, QMI_PROTOCOL_ERROR,
						 QMI_PROTOCOL_ERROR_INVALID_ARGUMENT);
}

static void
start_self_test (void)
{
	static const guint8 expected[] = {
		OP13_LOC_START_TLV_SESSION_ID, 0x01, 0x00, OP13_LOC_START_SESSION_ID,
		OP13_LOC_START_TLV_FIX_RECURRENCE, 0x04, 0x00,
			OP13_LOC_START_RECURRENCE_PERIODIC, 0x00, 0x00, 0x00,
		OP13_LOC_START_TLV_HORIZONTAL_ACCURACY, 0x04, 0x00,
			OP13_LOC_START_ACCURACY_HIGH, 0x00, 0x00, 0x00,
		OP13_LOC_START_TLV_INTERMEDIATE_REPORT, 0x04, 0x00,
			OP13_LOC_START_INTERMEDIATE_ON, 0x00, 0x00, 0x00,
		OP13_LOC_START_TLV_MIN_INTERVAL, 0x04, 0x00,
			0xe8, 0x03, 0x00, 0x00,
	};
	QmiMessage *request, *response;
	GError *error = NULL;
	gsize wire_length = 0;
	const guint8 *raw;

	request = start_message (0x27, 0x1234, &error);
	g_assert_no_error (error);
	g_assert_nonnull (request);
	g_assert_true (qmi_message_is_request (request));
	g_assert_cmpint (qmi_message_get_service (request), ==, QMI_SERVICE_LOC);
	g_assert_cmpuint (qmi_message_get_client_id (request), ==, 0x27);
	g_assert_cmpuint (qmi_message_get_transaction_id (request), ==, 0x1234);
	g_assert_cmpuint (qmi_message_get_message_id (request), ==,
			  OP13_LOC_START_REQUEST);
	raw = qmi_message_get_raw (request, &wire_length, &error);
	g_assert_no_error (error);
	g_assert_cmpuint (wire_length, ==, 13 + sizeof (expected));
	g_assert_cmpmem (raw + 13, sizeof (expected), expected, sizeof (expected));

	response = qmi_message_response_new (request, QMI_PROTOCOL_ERROR_NONE);
	g_assert_true (start_result (response, 0x27, 0x1234, &error));
	g_assert_no_error (error);
	qmi_message_unref (response);

	response = qmi_message_response_new (request,
					     QMI_PROTOCOL_ERROR_GENERAL_ERROR);
	g_assert_false (start_result (response, 0x27, 0x1234, &error));
	g_assert_error (error, QMI_PROTOCOL_ERROR,
			QMI_PROTOCOL_ERROR_GENERAL_ERROR);
	g_assert_cmpstr (error->message, ==,
			 "QMI protocol error (46): 'GeneralError'");
	g_clear_error (&error);
	qmi_message_unref (response);

	response = qmi_message_response_new (request, QMI_PROTOCOL_ERROR_NONE);
	g_assert_false (start_result (response, 0x27, 0x1235, &error));
	g_assert_error (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA);
	g_clear_error (&error);
	g_assert_false (start_result (response, 0x28, 0x1234, &error));
	g_assert_error (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA);
	g_clear_error (&error);
	qmi_message_unref (response);
	qmi_message_unref (request);
}

static void
lock_self_test (void)
{
	static const guint8 none_bytes[] = {
		OP13_LOC_LOCK_TLV_TYPE, 0x04, 0x00, OP13_LOC_LOCK_NONE, 0x00, 0x00, 0x00,
	};
	static const guint8 all_bytes[] = {
		OP13_LOC_LOCK_TLV_TYPE, 0x04, 0x00, OP13_LOC_LOCK_ALL, 0x00, 0x00, 0x00,
	};
	QmiMessage *request, *response;
	GError *error = NULL;
	gsize wire_length = 0;
	const guint8 *raw;

	request = lock_set_message (0x27, 0x1234, OP13_LOC_LOCK_NONE, &error);
	g_assert_no_error (error);
	g_assert_nonnull (request);
	g_assert_true (qmi_message_is_request (request));
	g_assert_cmpuint (qmi_message_get_message_id (request), ==, 0x3a);
	g_assert_cmpuint (qmi_message_get_transaction_id (request), ==, 0x1234);
	raw = qmi_message_get_raw (request, &wire_length, &error);
	g_assert_no_error (error);
	g_assert_cmpuint (wire_length, ==, 13 + sizeof (none_bytes));
	g_assert_cmpmem (raw + 13, sizeof (none_bytes), none_bytes, sizeof (none_bytes));
	response = qmi_message_response_new (request, QMI_PROTOCOL_ERROR_NONE);
	g_assert_true (lock_set_response_valid (response, 0x27, 0x1234));
	/* A read-only GET response must never validate a SET transaction. */
	g_assert_false (lock_response_valid (response, 0x27, 0x1234));
	g_assert_false (lock_set_response_valid (response, 0x27, 0x1235));
	qmi_message_unref (response);
	qmi_message_unref (request);

	request = lock_set_message (0x27, 0x1234, OP13_LOC_LOCK_ALL, &error);
	g_assert_no_error (error);
	raw = qmi_message_get_raw (request, &wire_length, &error);
	g_assert_no_error (error);
	g_assert_cmpuint (wire_length, ==, 13 + sizeof (all_bytes));
	g_assert_cmpmem (raw + 13, sizeof (all_bytes), all_bytes, sizeof (all_bytes));
	response = qmi_message_response_new (request,
					     QMI_PROTOCOL_ERROR_GENERAL_ERROR);
	g_assert_false (lock_set_response_valid (response, 0x27, 0x1234));
	qmi_message_unref (response);
	qmi_message_unref (request);
}

static int
configuration_self_test (void)
{
	GError *unsupported;
	GError *general;
	GError *transport;
	GError *invalid;
	struct session session = { 0 };
	struct session pending = { .start_pending = TRUE };
	guint timer_id;

	unsupported = g_error_new_literal (QMI_PROTOCOL_ERROR,
		QMI_PROTOCOL_ERROR_NOT_SUPPORTED, "unsupported configuration");
	general = g_error_new_literal (QMI_PROTOCOL_ERROR,
		QMI_PROTOCOL_ERROR_GENERAL_ERROR, "modem failure");
	transport = g_error_new_literal (G_IO_ERROR,
		QMI_PROTOCOL_ERROR_NOT_SUPPORTED, "different error domain");
	invalid = g_error_new_literal (QMI_PROTOCOL_ERROR,
		QMI_PROTOCOL_ERROR_INVALID_ARGUMENT, "unsupported NMEA event");
	g_assert_true (nmea_types_error_is_optional (unsupported));
	g_assert_false (nmea_types_error_is_optional (general));
	g_assert_false (nmea_types_error_is_optional (transport));
	g_assert_false (nmea_types_error_is_optional (NULL));
	g_assert_true (retry_position_events (invalid, FALSE));
	g_assert_false (retry_position_events (invalid, TRUE));
	g_assert_false (retry_position_events (general, FALSE));
	g_assert_false (retry_position_events (transport, FALSE));
	g_assert_false (retry_position_events (NULL, FALSE));
	g_error_free (invalid);
	g_error_free (unsupported);
	g_error_free (general);
	g_error_free (transport);
	timer_id = g_timeout_add_seconds (3600, self_test_timer, NULL);
	session.timeout_id = timer_id;
	g_assert_true (claim_stop (&session));
	g_assert_cmpuint (session.timeout_id, ==, 0);
	g_assert_null (g_main_context_find_source_by_id (NULL, timer_id));
	g_assert_false (claim_stop (&session));
	g_assert_false (terminate (&session));
	g_assert_false (terminate (&pending));
	g_assert_true (pending.terminate_requested);
	g_assert_false (pending.stop_requested);
	g_assert_true (unix_terminate (&session));
	g_assert_true (unix_terminate (&pending));
	start_self_test ();
	lock_self_test ();
	g_print ("LOCATION_SESSION_CONFIG_SELF_TEST_PASS checks=35 no_qrtr=1\n");
	return 0;
}

static void
finish (struct session *session, int status)
{
	exit_status = status;
	if (session->timeout_id) {
		g_source_remove (session->timeout_id);
		session->timeout_id = 0;
	}
	if (session->deadline_id) {
		g_source_remove (session->deadline_id);
		session->deadline_id = 0;
	}
	if (session->loop && g_main_loop_is_running (session->loop))
		g_main_loop_quit (session->loop);
}

static void
print_error (const char *operation, const GError *error)
{
	g_printerr ("LOCATION_SESSION_ERROR operation=%s error=%s\n",
		    operation, error ? error->message : "unknown");
}

/* Qualcomm location_service_v02.c GET_ENGINE_LOCK_IND 0x003b:
 * status 0x01/u32; optional lockType 0x10/u32, subType 0x11/u32,
 * lockClient 0x12/u64, clientsConfig 0x13/u64, engineLockState 0x14/u32.
 * Read-only default-DD query. Missing optional fields remain JSON null.
 */
struct lock_fields {
	guint counts[6];
	guint64 values[6];
	gboolean invalid;
};

static void
lock_field (guint8 type, const guint8 *value, gsize length, gpointer user_data)
{
	struct lock_fields *fields = user_data;
	guint index, width, i;
	if (type == 0x01)
		index = 0;
	else if (type >= 0x10 && type <= 0x14)
		index = type - 0x10 + 1;
	else
		return; /* Do not expose unknown vendor TLVs. */
	width = (index == 3 || index == 4) ? 8 : 4;
	if (++fields->counts[index] != 1 || length != width) {
		fields->invalid = TRUE;
		return;
	}
	for (i = 0; i < width; i++)
		fields->values[index] |= ((guint64)value[i]) << (8 * i);
}

/* Parse one GET_ENGINE_LOCK_IND 0x003b indication. Field order follows the
 * pinned IDL: status 0x01, lockType 0x10, subType 0x11, lockClient 0x12,
 * clientsConfig 0x13, engineLockState 0x14. A non-zero status or a malformed
 * field set is never treated as a usable reading. */
static gboolean
lock_parse (QmiMessage *message, guint8 cid, struct lock_fields *fields)
{
	if (!qmi_message_is_indication (message) ||
	    qmi_message_get_service (message) != QMI_SERVICE_LOC ||
	    qmi_message_get_message_id (message) != OP13_LOC_GET_ENGINE_LOCK ||
	    qmi_message_get_client_id (message) != cid)
		return FALSE;
	qmi_message_foreach_raw_tlv (message, lock_field, fields);
	return !fields->invalid && fields->counts[0] == 1 && fields->values[0] == 0;
}

static gchar *
lock_report (const struct lock_fields *fields)
{
	static const char *names[] = { "status", "lock_type", "subscription",
		"lock_client", "clients_config", "engine_lock_state" };
	GString *json;
	guint i;

	json = g_string_new ("{\"read_only\":true,\"default_data_subscription\":true");
	for (i = 0; i < G_N_ELEMENTS (names); i++) {
		g_string_append_printf (json, ",\"%s\":", names[i]);
		if (!fields->counts[i])
			g_string_append (json, "null");
		else if (i == 3 || i == 4)
			g_string_append_printf (json, "\"0x%016" G_GINT64_MODIFIER "x\"",
				fields->values[i]);
		else
			g_string_append_printf (json, "%" G_GUINT64_FORMAT,
				fields->values[i]);
	}
	g_string_append_c (json, '}');
	return g_string_free (json, FALSE);
}


static gchar *
lock_indication_report (QmiMessage *message, guint8 cid)
{
	struct lock_fields fields = { 0 };

	if (!lock_parse (message, cid, &fields))
		return NULL;
	return lock_report (&fields);
}

static void
lock_maybe_finish (struct session *session)
{
	if (session->lock_response_ok && session->lock_indication_ok) {
		g_print ("LOCATION_ENGINE_LOCK_RESULT %s\n", session->lock_report);
		finish (session, 0);
	}
}

static gboolean
lock_response_result (guint8 type, const guint8 *value, gsize length)
{
	return type == 0x02 && length == 4 &&
		!value[0] && !value[1] && !value[2] && !value[3];
}

struct lock_response_fields {
	guint count;
	gboolean ok;
};

static void
lock_response_field (guint8 type, const guint8 *value, gsize length, gpointer user_data)
{
	struct lock_response_fields *fields = user_data;
	if (type == 0x02) {
		fields->count++;
		fields->ok = lock_response_result (type, value, length);
	}
}

static gboolean
lock_response_valid_id (QmiMessage *message, guint16 message_id, guint8 cid,
			guint16 transaction)
{
	struct lock_response_fields fields = { 0 };
	if (!qmi_message_is_response (message) ||
	    qmi_message_get_service (message) != QMI_SERVICE_LOC ||
	    qmi_message_get_message_id (message) != message_id ||
	    qmi_message_get_client_id (message) != cid ||
	    qmi_message_get_transaction_id (message) != transaction)
		return FALSE;
	qmi_message_foreach_raw_tlv (message, lock_response_field, &fields);
	return fields.count == 1 && fields.ok;
}

static gboolean
lock_response_valid (QmiMessage *message, guint8 cid, guint16 transaction)
{
	return lock_response_valid_id (message, OP13_LOC_GET_ENGINE_LOCK, cid,
				       transaction);
}

static gboolean
lock_set_response_valid (QmiMessage *message, guint8 cid, guint16 transaction)
{
	return lock_response_valid_id (message, OP13_LOC_SET_ENGINE_LOCK, cid,
				       transaction);
}

/* SET_ENGINE_LOCK 0x003a carries only the mandatory lockType TLV. subType,
 * lockClient and clientsConfig are deliberately not sent: the pinned IDL states
 * that lockClient overwrites lockType, so restoring lockType is the faithful
 * and minimal way back to the saved state. */
static QmiMessage *
lock_set_message (guint8 cid, guint16 transaction, guint32 lock_type,
		  GError **error)
{
	QmiMessage *message;

	message = qmi_message_new (QMI_SERVICE_LOC, cid, transaction,
				   OP13_LOC_SET_ENGINE_LOCK);
	if (!message) {
		g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_NO_SPACE,
				     "cannot allocate LOC engine-lock request");
		return NULL;
	}
	if (!add_u32_tlv (message, OP13_LOC_LOCK_TLV_TYPE, lock_type, error)) {
		qmi_message_unref (message);
		return NULL;
	}
	return message;
}

static void begin_session (struct session *session);
static void lock_query (struct session *session, int phase);
static void unlock_set (struct session *session, guint32 lock_type);
static void unlock_restore (struct session *session, int status);

/* Any path that ends the helper must not drop an unproven mutation: report it
 * explicitly instead of exiting quietly with a changed lock. */
static void
finish_session (struct session *session, int status)
{
	if (session->unlock_engine_lock && session->lock_changed &&
	    !session->lock_restored)
		g_printerr ("LOCATION_ENGINE_LOCK_UNRESTORED saved=%u\n",
			    (guint) session->saved_lock_type);
	finish (session, status);
}

static void
lock_received (QmiDevice *device, QmiMessage *message, gpointer user_data)
{
	struct session *session = user_data;
	struct lock_fields fields = { 0 };
	gchar *report;
	guint8 cid;

	(void)device;
	if (!session->client ||
	    !qmi_message_is_indication (message) ||
	    qmi_message_get_service (message) != QMI_SERVICE_LOC ||
	    qmi_message_get_message_id (message) != OP13_LOC_GET_ENGINE_LOCK)
		return;
	cid = qmi_client_get_cid (QMI_CLIENT (session->client));
	if (qmi_message_get_client_id (message) != cid)
		return;
	if (session->lock_indication_ok || !lock_parse (message, cid, &fields)) {
		g_printerr ("LOCATION_ENGINE_LOCK_ERROR malformed-or-duplicate-indication\n");
		finish_session (session, 1);
		return;
	}
	session->lock_indication_ok = TRUE;
	report = lock_report (&fields);

	switch (session->lock_phase) {
	case LOCK_PHASE_NONE:
		g_free (report);
		g_free (session->lock_report);
		session->lock_report = lock_indication_report (message, cid);
		lock_maybe_finish (session);
		return;
	case LOCK_PHASE_SAVE:
		session->saved_lock_type = (guint32) fields.values[1];
		g_print ("LOCATION_ENGINE_LOCK_SAVED %s\n", report);
		g_free (report);
		if (session->saved_lock_type == OP13_LOC_LOCK_NONE) {
			g_print ("LOCATION_ENGINE_UNLOCK already=none\n");
			begin_session (session);
			return;
		}
		unlock_set (session, OP13_LOC_LOCK_NONE);
		return;
	case LOCK_PHASE_CONFIRM_NONE:
		g_print ("LOCATION_ENGINE_UNLOCK_CONFIRMED %s\n", report);
		g_free (report);
		if ((guint32) fields.values[1] != OP13_LOC_LOCK_NONE) {
			g_printerr ("LOCATION_ENGINE_UNLOCK_ERROR not-applied\n");
			unlock_restore (session, 1);
			return;
		}
		session->lock_applied = TRUE;
		begin_session (session);
		return;
	case LOCK_PHASE_CONFIRM_RESTORE:
		g_print ("LOCATION_ENGINE_LOCK_RESTORED %s\n", report);
		g_free (report);
		if ((guint32) fields.values[1] != session->saved_lock_type) {
			g_printerr ("LOCATION_ENGINE_LOCK_RESTORE_ERROR mismatch saved=%u\n",
				    (guint) session->saved_lock_type);
			finish_session (session, 1);
			return;
		}
		session->lock_restored = TRUE;
		finish (session, session->restore_status);
		return;
	default:
		g_free (report);
		return;
	}
}

static void
lock_query (struct session *session, int phase)
{
	QmiMessage *input;
	QmiClient *client = QMI_CLIENT (session->client);

	session->lock_phase = phase;
	session->lock_indication_ok = FALSE;
	session->lock_transaction = qmi_client_get_next_transaction_id (client);
	input = qmi_message_new (QMI_SERVICE_LOC, qmi_client_get_cid (client),
				 session->lock_transaction,
				 OP13_LOC_GET_ENGINE_LOCK);
	if (!input) {
		g_printerr ("LOCATION_ENGINE_LOCK_ERROR query-allocation\n");
		finish_session (session, 1);
		return;
	}
	qmi_device_command_full (session->device, input, NULL, 20,
				 session->cancellable,
				 (GAsyncReadyCallback) lock_ready, session);
	qmi_message_unref (input);
}

static void
lock_ready (QmiDevice *device, GAsyncResult *result, gpointer user_data)
{
	struct session *session = user_data;
	GError *error = NULL;
	QmiMessage *output = qmi_device_command_full_finish (device, result, &error);

	if (!output || !lock_response_valid (output,
		qmi_client_get_cid (QMI_CLIENT (session->client)),
		session->lock_transaction)) {
		g_printerr ("LOCATION_ENGINE_LOCK_ERROR rejected-or-invalid-response\n");
		g_clear_error (&error);
		if (output)
			qmi_message_unref (output);
		finish_session (session, 1);
		return;
	}
	qmi_message_unref (output);
	session->lock_response_ok = TRUE;
	if (session->lock_phase == LOCK_PHASE_NONE)
		lock_maybe_finish (session);
}

static void
unlock_set_ready (QmiDevice *device, GAsyncResult *result, gpointer user_data)
{
	struct session *session = user_data;
	GError *error = NULL;
	QmiMessage *output = qmi_device_command_full_finish (device, result, &error);

	if (!output || !lock_set_response_valid (output,
		qmi_client_get_cid (QMI_CLIENT (session->client)),
		session->lock_transaction)) {
		print_error ("set-engine-lock", error);
		g_clear_error (&error);
		if (output)
			qmi_message_unref (output);
		g_printerr ("LOCATION_ENGINE_LOCK_SET_ERROR rejected-or-invalid-response\n");
		finish_session (session, 1);
		return;
	}
	qmi_message_unref (output);
	/* The SET is applied: from here the saved value must be restored even if
	 * the confirmation query fails. */
	session->lock_changed = TRUE;
	lock_query (session, session->lock_restore_pending ?
		    LOCK_PHASE_CONFIRM_RESTORE : LOCK_PHASE_CONFIRM_NONE);
}

static void
unlock_set (struct session *session, guint32 lock_type)
{
	QmiMessage *input;
	GError *error = NULL;
	QmiClient *client = QMI_CLIENT (session->client);

	session->lock_transaction = qmi_client_get_next_transaction_id (client);
	input = lock_set_message (qmi_client_get_cid (client),
				  session->lock_transaction, lock_type, &error);
	if (!input) {
		print_error ("build-set-engine-lock", error);
		g_clear_error (&error);
		finish_session (session, 1);
		return;
	}
	qmi_device_command_full (session->device, input, NULL, 20,
				 session->cancellable,
				 (GAsyncReadyCallback) unlock_set_ready, session);
	qmi_message_unref (input);
}

static void
unlock_restore (struct session *session, int status)
{
	session->restore_status = status;
	session->lock_restore_pending = TRUE;
	unlock_set (session, session->saved_lock_type);
}

static void
nmea_received (QmiClientLoc *client,
	       QmiIndicationLocNmeaOutput *output,
	       gpointer user_data)
{
	struct session *session = user_data;
	const char *nmea = NULL;

	(void)client;
	if (qmi_indication_loc_nmea_output_get_nmea_string (output, &nmea, NULL) &&
	    nmea) {
		session->nmea_count++;
		g_print ("NMEA %s\n", nmea);
		fflush (stdout);
	}
}

static gboolean
request_stop (gpointer user_data);

static void
position_received (QmiClientLoc *client,
		   QmiIndicationLocPositionReportOutput *output, gpointer user_data)
{
	QmiLocSessionStatus status;
	QmiLocTechnologyUsed technology;
	gdouble latitude, longitude;

	(void)client;
	(void)user_data;
	/* A fused/network estimate or an intermediate report cannot qualify GNSS. */
	if (!qmi_indication_loc_position_report_output_get_session_status (output, &status, NULL) ||
	    status != QMI_LOC_SESSION_STATUS_SUCCESS ||
	    !qmi_indication_loc_position_report_output_get_technology_used (output, &technology, NULL) ||
	    !(technology & QMI_LOC_TECHNOLOGY_USED_SATELLITE) ||
	    !qmi_indication_loc_position_report_output_get_latitude (output, &latitude, NULL) ||
	    !qmi_indication_loc_position_report_output_get_longitude (output, &longitude, NULL) ||
	    !isfinite (latitude) || !isfinite (longitude) ||
	    latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180)
		return;
	g_print ("LOCATION_SESSION_POSITION {\"status\":\"success\",\"satellite\":true,"
		 "\"latitude\":%.9f,\"longitude\":%.9f}\n", latitude, longitude);
	fflush (stdout);
}

static void register_events (struct session *session);

static void
stop_ready (QmiClientLoc *client, GAsyncResult *result, gpointer user_data)
{
	struct session *session = user_data;
	QmiMessageLocStopOutput *output;
	GError *error = NULL;

	output = qmi_client_loc_stop_finish (client, result, &error);
	if (!output) {
		print_error ("stop", error);
		g_clear_error (&error);
		finish_session (session, 1);
		return;
	}
	if (!qmi_message_loc_stop_output_get_result (output, &error)) {
		print_error ("stop-result", error);
		g_clear_error (&error);
		qmi_message_loc_stop_output_unref (output);
		finish_session (session, 1);
		return;
	}
	qmi_message_loc_stop_output_unref (output);
	session->stopped = TRUE;
	g_print ("LOCATION_SESSION_STOPPED nmea=%u\n", session->nmea_count);
	if (session->unlock_engine_lock && session->lock_changed &&
	    !session->lock_restored)
		unlock_restore (session, session->started ? 0 : 1);
	else
		finish (session, session->started ? 0 : 1);
}

static gboolean
request_stop (gpointer user_data)
{
	struct session *session = user_data;
	QmiMessageLocStopInput *input;

	if (!claim_stop (session))
		return G_SOURCE_REMOVE;
	input = qmi_message_loc_stop_input_new ();
	qmi_message_loc_stop_input_set_session_id (input, 1, NULL);
	qmi_client_loc_stop (session->client, input, 20,
			     session->cancellable,
			     (GAsyncReadyCallback) stop_ready, session);
	qmi_message_loc_stop_input_unref (input);
	return G_SOURCE_REMOVE;
}

static void
start_ready (QmiDevice *device, GAsyncResult *result, gpointer user_data)
{
	struct session *session = user_data;
	QmiMessage *output;
	GError *error = NULL;

	output = qmi_device_command_full_finish (device, result, &error);
	session->start_pending = FALSE;
	if (!output) {
		print_error ("start", error);
		g_clear_error (&error);
		/* A lost reply does not prove that the modem rejected start. */
		request_stop (session);
		return;
	}
	if (!start_result (output, qmi_client_get_cid (QMI_CLIENT (session->client)),
			   session->start_transaction, &error)) {
		print_error ("start-result", error);
		g_clear_error (&error);
		qmi_message_unref (output);
		request_stop (session);
		return;
	}
	qmi_message_unref (output);
	session->started = TRUE;
	g_print ("LOCATION_SESSION_STARTED seconds=%u\n", session->requested_seconds);
	fflush (stdout);
	if (session->terminate_requested)
		request_stop (session);
	else if (!session->stop_requested)
		session->timeout_id =
			g_timeout_add_seconds (session->requested_seconds, request_stop,
					       session);
}

static void
start_dispatch (struct session *session)
{
	QmiMessage *input;
	GError *error = NULL;
	QmiClient *client = QMI_CLIENT (session->client);

	session->start_transaction = qmi_client_get_next_transaction_id (client);
	input = start_message (qmi_client_get_cid (client),
			       session->start_transaction, &error);
	if (!input) {
		print_error ("build-start", error);
		g_clear_error (&error);
		finish (session, 1);
		return;
	}
	session->start_requested = TRUE;
	session->start_pending = TRUE;
	g_print ("LOCATION_SESSION_START_PROFILE accuracy=high recurrence=periodic "
		 "intermediate=on min_interval_ms=%u session=%u\n",
		 (guint) OP13_LOC_START_MIN_INTERVAL_MS,
		 (guint) OP13_LOC_START_SESSION_ID);
	fflush (stdout);
	qmi_device_command_full (session->device, input, NULL, 20,
				 session->cancellable,
				 (GAsyncReadyCallback) start_ready, session);
	qmi_message_unref (input);
}

static void
register_events_ready (QmiDevice *device, GAsyncResult *result,
		       gpointer user_data)
{
	struct session *session = user_data;
	QmiMessage *output;
	GError *error = NULL;

	output = qmi_device_command_full_finish (device, result, &error);
	if (!output) {
		print_error ("register-events", error);
		g_clear_error (&error);
		finish (session, 1);
		return;
	}
	if (!registration_result (output, &error)) {
		if (retry_position_events (error, session->position_events_only)) {
			/* Some firmware rejects the optional NMEA event. Use the
			 * standard LOC position report on this same client, once. */
			session->position_events_only = TRUE;
			g_print ("LOCATION_SESSION_POSITION_EVENTS reason=nmea-invalid-argument\n");
			g_clear_error (&error);
			qmi_message_unref (output);
			register_events (session);
			return;
		}
		print_error ("register-events-result", error);
		g_clear_error (&error);
		qmi_message_unref (output);
		finish (session, 1);
		return;
	}
	qmi_message_unref (output);

	start_dispatch (session);
}

static void
register_events (struct session *session)
{
	QmiMessage *input;
	GError *error = NULL;
	QmiClient *client = QMI_CLIENT (session->client);

	input = registration_message (qmi_client_get_cid (client),
		qmi_client_get_next_transaction_id (client),
		session->position_events_only, &error);
	if (!input) {
		print_error ("build-register-events", error);
		g_clear_error (&error);
		finish (session, 1);
		return;
	}
	g_print ("LOCATION_SESSION_CLIENT_ID type=afw string=OP13\n");
	qmi_device_command_full (
		session->device, input, NULL, 20, session->cancellable,
		(GAsyncReadyCallback) register_events_ready, session);
	qmi_message_unref (input);
}

static void
set_nmea_ready (QmiClientLoc *client, GAsyncResult *result, gpointer user_data)
{
	struct session *session = user_data;
	QmiMessageLocSetNmeaTypesOutput *output;
	GError *error = NULL;

	output = qmi_client_loc_set_nmea_types_finish (client, result, &error);
	if (!output) {
		print_error ("set-nmea", error);
		g_clear_error (&error);
		finish (session, 1);
		return;
	}
	if (!qmi_message_loc_set_nmea_types_output_get_result (output, &error)) {
		if (!nmea_types_error_is_optional (error)) {
			print_error ("set-nmea-result", error);
			g_clear_error (&error);
			qmi_message_loc_set_nmea_types_output_unref (output);
			finish (session, 1);
			return;
		}
		/* Some LOC implementations reject this optional configuration.
		 * Keep their default sentence selection, but still require real
		 * NMEA fixes and a successful start/stop for acceptance. */
		g_print ("LOCATION_SESSION_NMEA_DEFAULTS reason=not-supported\n");
		g_clear_error (&error);
	}
	qmi_message_loc_set_nmea_types_output_unref (output);

	register_events (session);
}

static void
begin_session (struct session *session)
{
	QmiMessageLocSetNmeaTypesInput *input;

	g_signal_connect (session->client, "nmea",
			  G_CALLBACK (nmea_received), session);
	g_signal_connect (session->client, "position-report",
			  G_CALLBACK (position_received), session);
	input = qmi_message_loc_set_nmea_types_input_new ();
	qmi_message_loc_set_nmea_types_input_set_nmea_types (
		input, QMI_LOC_NMEA_TYPE_GGA | QMI_LOC_NMEA_TYPE_RMC, NULL);
	qmi_client_loc_set_nmea_types (
		session->client, input, 20, session->cancellable,
		(GAsyncReadyCallback) set_nmea_ready, session);
	qmi_message_loc_set_nmea_types_input_unref (input);
}

static void
client_ready (QmiDevice *device, GAsyncResult *result, gpointer user_data)
{
	struct session *session = user_data;
	GError *error = NULL;

	session->client = QMI_CLIENT_LOC (
		qmi_device_allocate_client_finish (device, result, &error));
	if (!session->client) {
		print_error ("allocate-client", error);
		g_clear_error (&error);
		finish (session, 1);
		return;
	}
	if (session->lock_diagnostic) {
		QmiMessage *input = qmi_message_new (QMI_SERVICE_LOC,
			qmi_client_get_cid (QMI_CLIENT (session->client)), 1, 0x003b);
		g_signal_connect (session->device, QMI_DEVICE_SIGNAL_INDICATION,
			G_CALLBACK (lock_received), session);
		qmi_device_command_full (session->device, input, NULL, 20,
			session->cancellable, (GAsyncReadyCallback) lock_ready, session);
		qmi_message_unref (input);
		return;
	}
	if (session->unlock_engine_lock) {
		g_signal_connect (session->device, QMI_DEVICE_SIGNAL_INDICATION,
			G_CALLBACK (lock_received), session);
		lock_query (session, LOCK_PHASE_SAVE);
		return;
	}
	begin_session (session);
}

static void
device_open_ready (QmiDevice *device, GAsyncResult *result, gpointer user_data)
{
	struct session *session = user_data;
	GError *error = NULL;

	if (!qmi_device_open_finish (device, result, &error)) {
		print_error ("open-device", error);
		g_clear_error (&error);
		finish (session, 1);
		return;
	}
	qmi_device_allocate_client (
		device, QMI_SERVICE_LOC, QMI_CID_NONE, 20,
		session->cancellable, (GAsyncReadyCallback) client_ready, session);
}

static void
device_ready (GObject *source, GAsyncResult *result, gpointer user_data)
{
	struct session *session = user_data;
	GError *error = NULL;

	(void)source;
	session->device = qmi_device_new_from_node_finish (result, &error);
	if (!session->device) {
		print_error ("create-device", error);
		g_clear_error (&error);
		finish (session, 1);
		return;
	}
	qmi_device_open (session->device,
			 QMI_DEVICE_OPEN_FLAGS_EXPECT_INDICATIONS,
			 20, session->cancellable,
			 (GAsyncReadyCallback) device_open_ready, session);
}

static void
bus_ready (GObject *source, GAsyncResult *result, gpointer user_data)
{
	struct session *session = user_data;
	QrtrBus *bus;
	QrtrNode *node;
	GList *nodes, *services;
	GError *error = NULL;

	(void)source;
	bus = qrtr_bus_new_finish (result, &error);
	if (!bus) {
		print_error ("open-qrtr", error);
		g_clear_error (&error);
		finish (session, 1);
		return;
	}
	/* Enumerate the actual LOC endpoints before selecting the existing node.
	 * libqmi's QRTR backend selects the highest-version service port. Keep
	 * this evidence so a rejected mask is not diagnosed by guessing a node. */
	for (nodes = qrtr_bus_peek_nodes (bus); nodes; nodes = nodes->next) {
		QrtrNode *candidate = QRTR_NODE (nodes->data);
		for (services = qrtr_node_peek_service_info_list (candidate);
		     services; services = services->next) {
			QrtrNodeServiceInfo *info = services->data;
			if (qrtr_node_service_info_get_service (info) == QMI_SERVICE_LOC)
				g_print ("LOCATION_SESSION_LOC_SERVICE node=%u port=%u version=%u instance=%u selected_snapshot=%u\n",
					 qrtr_node_get_id (candidate),
					 qrtr_node_service_info_get_port (info),
					 qrtr_node_service_info_get_version (info),
					 qrtr_node_service_info_get_instance (info),
					 qrtr_node_get_id (candidate) == 0 &&
					 qrtr_node_lookup_port (candidate, QMI_SERVICE_LOC) ==
					 (gint32) qrtr_node_service_info_get_port (info));
		}
	}
	fflush (stdout);
	node = qrtr_bus_peek_node (bus, 0);
	if (!node) {
		g_printerr ("LOCATION_SESSION_ERROR operation=find-node\n");
		g_object_unref (bus);
		finish (session, 1);
		return;
	}
	qmi_device_new_from_node (node, session->cancellable,
				  (GAsyncReadyCallback) device_ready, session);
	g_object_unref (bus);
}

static gboolean
terminate (gpointer user_data)
{
	struct session *session = user_data;
	session->terminate_requested = TRUE;
	if (session->stop_requested)
		return G_SOURCE_REMOVE;
	/* Let the bounded start callback settle before sending stop. This avoids
	 * accepting stop while a later start acknowledgement is still pending. */
	if (session->start_pending)
		return G_SOURCE_REMOVE;
	if (session->client && session->start_requested && !session->stopped)
		request_stop (session);
	else
		finish (session, 1);
	return G_SOURCE_REMOVE;
}

static gboolean
unix_terminate (gpointer user_data)
{
	terminate (user_data);
	/* Keep the handler installed so a repeated SIGTERM cannot interrupt
	 * an in-flight LOC stop by reverting to the default signal action. */
	return G_SOURCE_CONTINUE;
}

static gboolean
deadline_expired (gpointer user_data)
{
	struct session *session = user_data;
	session->deadline_id = 0;
	return terminate (session);
}

int
main (int argc, char **argv)
{
	struct session session = { 0 };
	unsigned int seconds = 45;

	if (argc == 2 && g_str_equal (argv[1], "--self-test"))
		return configuration_self_test ();
	if (argc == 2 && g_str_equal (argv[1], "--get-engine-lock"))
		session.lock_diagnostic = TRUE;
	else if (argc == 2 && g_str_equal (argv[1], "--unlock-engine-lock"))
		session.unlock_engine_lock = TRUE;
	else if (argc == 4 && g_str_equal (argv[1], "--unlock-engine-lock") &&
		 g_str_equal (argv[2], "--seconds")) {
		session.unlock_engine_lock = TRUE;
		seconds = (unsigned int) g_ascii_strtoull (argv[3], NULL, 10);
	} else if (argc == 3 && g_str_equal (argv[1], "--seconds"))
		seconds = (unsigned int) g_ascii_strtoull (argv[2], NULL, 10);
	else if (argc != 1) {
		g_printerr ("usage: %s [--seconds SECONDS|--get-engine-lock|"
			    "--unlock-engine-lock [--seconds SECONDS]]\n", argv[0]);
		return 2;
	}
	if (seconds < 5 || seconds > 180)
		return 2;

	session.requested_seconds = seconds;
	session.loop = g_main_loop_new (NULL, FALSE);
	session.cancellable = g_cancellable_new ();
	g_unix_signal_add (SIGINT, unix_terminate, &session);
	g_unix_signal_add (SIGTERM, unix_terminate, &session);
	/* Allow pending start (20s) and stop (20s) after this soft deadline.
	 * The Python wrapper reserves 95 seconds beyond collection time. */
	/* The unlock cycle adds a SET plus two readback queries around the
	 * normal start/stop budget. */
	session.deadline_id = g_timeout_add_seconds (
		session.lock_diagnostic ? 35 :
		seconds + (session.unlock_engine_lock ? 90 : 45),
		deadline_expired, &session);
	qrtr_bus_new (5000, session.cancellable,
		      (GAsyncReadyCallback) bus_ready, &session);
	g_main_loop_run (session.loop);
	if (!session.start_requested)
		g_print ("LOCATION_SESSION_NO_SESSION start_requested=0\n");

	if (session.device)
		qmi_device_close_async (session.device, 5, session.cancellable,
					NULL, NULL);
	if (session.client)
		g_object_unref (session.client);
	if (session.device)
		g_object_unref (session.device);
	g_free (session.lock_report);
	g_object_unref (session.cancellable);
	g_main_loop_unref (session.loop);
	return exit_status;
}
