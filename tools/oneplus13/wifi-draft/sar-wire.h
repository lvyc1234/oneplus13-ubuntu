/* SPDX-License-Identifier: GPL-2.0-only */
/* Copyright (c) 2026 Codex. See PROVENANCE.md for protocol references. */
#ifndef PHONE_SAR_WIRE_H
#define PHONE_SAR_WIRE_H
#ifdef __KERNEL__
#include <linux/types.h>
typedef u32 psar_u32;
#else
#include <stddef.h>
#include <stdint.h>
typedef uint32_t psar_u32;
#endif

#define PSAR_MAX_ROWS 64
#define PSAR_MAX_SET_BYTES (20 + 24 * PSAR_MAX_ROWS)
enum psar_error { PSAR_OK, PSAR_INVALID, PSAR_UNSUPPORTED, PSAR_TRUNCATED };
struct psar_row {
	psar_u32 band, chain, modulation, value, valid;
};
struct psar_table {
	/* This is the firmware enum, never the QCA vendor attribute enum. */
	psar_u32 firmware_selection, count;
	struct psar_row rows[PSAR_MAX_ROWS];
};
struct psar_result {
	psar_u32 raw_result;
	int recognized, firmware_reported_success;
};
struct psar_capability {
	psar_u32 raw_version;
	int recognized;
};

/* Pure codecs: no transport, device, firmware loading, or power policy. */
int psar_qca_to_wmi(psar_u32 qca, psar_u32 *wmi);
int psar_encode_set(psar_u32 qca, const struct psar_row *rows, size_t count,
		    unsigned char *out, size_t capacity, size_t *written);
int psar_encode_get(unsigned char *out, size_t capacity, size_t *written);
int psar_decode_table(const unsigned char *data, size_t len,
		      struct psar_table *out);
int psar_decode_result(const unsigned char *data, size_t len,
		       struct psar_result *out);
int psar_decode_capability(const unsigned char *data, size_t len,
			   struct psar_capability *out);
#endif
