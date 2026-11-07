/* SPDX-License-Identifier: GPL-2.0-only */
/* Copyright (c) 2026 Codex. Compiled with the pinned vendor oracle. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sar-wire.h"
#include "vendor-oracle.h"

static void word(unsigned char *p, uint32_t n)
{
	p[0] = n; p[1] = n >> 8; p[2] = n >> 16; p[3] = n >> 24;
}

static void fixture(unsigned char *p, size_t count)
{
	size_t i;
	word(p, WMITLV_TAG_STRUC_wmi_sar_get_limits_event_fixed_param << 16 | 8);
	word(p + 4, WMI_SAR_FEATURE_ON_USER_DEFINED);
	word(p + 8, (uint32_t)count);
	word(p + 12, WMITLV_TAG_ARRAY_STRUC << 16 | (uint32_t)(count * 20));
	for (i = 0; i < count; i++) {
		unsigned char *r = p + 16 + 20 * i;
		word(r, WMITLV_TAG_STRUC_wmi_sar_get_limits_event_row << 16 | 16);
		word(r + 4, (uint32_t)(i >> 2));
		word(r + 8, (uint32_t)(i >> 1 & 1));
		word(r + 12, (uint32_t)(i & 1));
		word(r + 16, (uint32_t)(10 + i)); /* Synthetic, not a phone power policy. */
	}
}

int main(void)
{
	unsigned char out[PSAR_MAX_SET_BYTES], before[PSAR_MAX_SET_BYTES];
	unsigned char event[16 + PSAR_MAX_ROWS * 20 + 1], unaligned[180];
	struct psar_row rows[8];
	struct psar_table table, saved;
	struct psar_result result;
	struct psar_capability cap;
	struct sar_limit_cmd_row vendor_rows[8];
	struct sar_limit_cmd_params params;
	uint32_t mapped, qca, raw;
	size_t i, n, j;
	unsigned groups = 0;
	assert(*(unsigned char *)&(uint32_t){1} == 1);
	assert(WMITLV_TAG_STRUC_wmi_sar_limits_cmd_fixed_param == 590);
	assert(WMITLV_TAG_STRUC_wmi_sar_get_limits_cmd_fixed_param == 708);
	assert(WMI_SAR_FEATURE_ON_USER_DEFINED == 7 && WMI_SAR_FEATURE_ON_SAR_V2_0 == 8);
	for (qca = 0; qca <= 7; qca++) {
		mapped = 999;
		if (qca == 5) {
			assert(psar_qca_to_wmi(qca, &mapped) == PSAR_UNSUPPORTED && mapped == 999);
			continue;
		}
		assert(psar_qca_to_wmi(qca, &mapped) == PSAR_OK && mapped == qca + 1);
	}
	assert(psar_qca_to_wmi(8, &mapped) == PSAR_UNSUPPORTED);
	assert(psar_qca_to_wmi(6, NULL) == PSAR_INVALID); groups++;

	for (i = 0; i < 8; i++) {
		rows[i] = (struct psar_row){i >> 2, i >> 1 & 1, i & 1, 3 + i, 7};
		vendor_rows[i] = (struct sar_limit_cmd_row){rows[i].band, rows[i].chain,
			rows[i].modulation, rows[i].value, rows[i].valid};
	}
	for (qca = 0; qca <= 7; qca++) {
		if (qca == 5) continue;
		i = qca < 5 ? 0 : 8;
		assert(psar_encode_set(qca, rows, i, out, sizeof(out), &n) == PSAR_OK);
		assert(psar_qca_to_wmi(qca, &mapped) == PSAR_OK);
		params = (struct sar_limit_cmd_params){mapped, (uint32_t)i, 1, vendor_rows};
		assert(send_sar_limit_cmd_tlv(NULL, &params) == QDF_STATUS_SUCCESS);
		assert(oracle_size == n && memcmp(oracle_data, out, n) == 0);
	} groups++; /* Actual extracted vendor sender, not a second imitation. */

	memset(out, 0xa5, sizeof(out)); memcpy(before, out, sizeof(out)); n = 999;
	assert(psar_encode_set(5, NULL, 0, out, sizeof(out), &n) == PSAR_UNSUPPORTED);
	assert(psar_encode_set(6, rows, 8, out, 211, &n) == PSAR_TRUNCATED);
	assert(memcmp(out, before, sizeof(out)) == 0 && n == 999); groups++;
	assert(psar_encode_set(0, rows, 1, out, sizeof(out), &n) == PSAR_INVALID);
	assert(psar_encode_set(6, NULL, 0, out, sizeof(out), &n) == PSAR_INVALID);
	assert(psar_encode_set(6, rows, 65, out, sizeof(out), &n) == PSAR_INVALID);
	assert(psar_encode_set(6, NULL, 1, out, sizeof(out), &n) == PSAR_INVALID); groups++;
	rows[1] = rows[0];
	assert(psar_encode_set(6, rows, 2, out, sizeof(out), &n) == PSAR_INVALID);
	rows[1] = (struct psar_row){0, 0, 0, 10, 0};
	assert(psar_encode_set(6, rows, 2, out, sizeof(out), &n) == PSAR_INVALID); groups++;
	rows[0] = (struct psar_row){0, 0, 0, 12, 7};
	assert(psar_encode_set(7, rows, 1, out, sizeof(out), &n) == PSAR_UNSUPPORTED);
	rows[0].value = 11;
	assert(psar_encode_set(7, rows, 1, out, sizeof(out), &n) == PSAR_OK);
	rows[0].band = 2;
	assert(psar_encode_set(6, rows, 1, out, sizeof(out), &n) == PSAR_UNSUPPORTED);
	rows[0] = (struct psar_row){0, 1, 0, 10, 1};
	assert(psar_encode_set(6, rows, 1, out, sizeof(out), &n) == PSAR_UNSUPPORTED); groups++;

	assert(psar_encode_get(out, sizeof(out), &n) == PSAR_OK && n == 8);
	assert(!memcmp(out, &(wmi_sar_get_limits_cmd_fixed_param){
		WMITLV_TAG_STRUC_wmi_sar_get_limits_cmd_fixed_param << 16 | 4, 0}, 8));
	assert(psar_encode_get(out, 7, &n) == PSAR_TRUNCATED); groups++;
	fixture(event, 8);
	assert(psar_decode_table(event, 176, &table) == PSAR_OK && table.count == 8);
	for (i = 0; i < 8; i++) assert(table.rows[i].value == 10 + i);
	memcpy(unaligned + 1, event, 176);
	assert(psar_decode_table(unaligned + 1, 176, &table) == PSAR_OK); groups++;
	memset(&table, 0xa5, sizeof(table)); saved = table;
	for (i = 0; i < 176; i++) {
		assert(psar_decode_table(event, i, &table) != PSAR_OK);
		assert(!memcmp(&table, &saved, sizeof(table)));
	}
	assert(psar_decode_table(event, 177, &table) == PSAR_INVALID); groups++;
	word(event + 8, 65);
	assert(psar_decode_table(event, 176, &table) == PSAR_INVALID);
	word(event + 8, UINT32_MAX);
	assert(psar_decode_table(event, 176, &table) == PSAR_INVALID);
	fixture(event, 8); word(event + 12, WMITLV_TAG_ARRAY_STRUC << 16 | 140);
	assert(psar_decode_table(event, 176, &table) == PSAR_INVALID); groups++;
	fixture(event, 8); word(event + 20, 2);
	assert(psar_decode_table(event, 176, &table) == PSAR_UNSUPPORTED);
	fixture(event, 8); memcpy(event + 36, event + 16, 20);
	assert(psar_decode_table(event, 176, &table) == PSAR_INVALID);
	fixture(event, 8); word(event + 4, WMI_SAR_FEATURE_OFF);
	assert(psar_decode_table(event, 176, &table) == PSAR_INVALID); groups++;
	word(event + 8, 0);word(event + 12, WMITLV_TAG_ARRAY_STRUC << 16);
	assert(psar_decode_table(event, 16, &table) == PSAR_OK);
	assert(table.firmware_selection == WMI_SAR_FEATURE_OFF && table.count == 0);
	/* Decoder observation does not permit a SET that disables limits. */
	assert(psar_encode_set(5, NULL, 0, out, sizeof(out), &n) == PSAR_UNSUPPORTED); groups++;

	word(event, WMITLV_TAG_STRUC_wmi_sar2_result_event_fixed_param << 16 | 4);
	for (raw = 0; raw < 10; raw++) {
		word(event + 4, raw);
		assert(psar_decode_result(event, 8, &result) == PSAR_OK);
		assert(result.raw_result == raw && result.firmware_reported_success == (raw == 0));
		assert(result.recognized == (raw == 0 || raw == 1 || raw == 2 || raw == 4 || raw == 8));
	}
	assert(psar_decode_result(event, 7, &result) == PSAR_INVALID); groups++;
	word(event, WMITLV_TAG_STRUC_WMI_SAR_CAPABILITIES << 16 | 4);
	for (raw = 0; raw <= 128; raw++) {
		word(event + 4, raw);
		assert(psar_decode_capability(event, 8, &cap) == PSAR_OK);
		assert(cap.raw_version == raw && cap.recognized == (raw <= 5));
	}
	assert(psar_decode_capability(event, 9, &cap) == PSAR_INVALID); groups++;

	/* All one-byte mutations and lengths exercise the real decoder under ASan. */
	for (i = 0; i < 176; i++) {
		for (j = 0; j < 256; j++) {
			fixture(event, 8); event[i] = (unsigned char)j;
			(void)psar_decode_table(event, 176, &table);
		}
	} groups++;
	assert(groups == 15);
	printf("PHONE_SAR_WIRE_TESTS groups=%u vendor_sender_exact_bytes=yes hardware=no\n", groups);
	return 0;
}
