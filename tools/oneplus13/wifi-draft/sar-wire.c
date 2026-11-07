/* SPDX-License-Identifier: GPL-2.0-only */
/* Copyright (c) 2026 Codex. See PROVENANCE.md for protocol references. */
#include "sar-wire.h"

/* WMI TLV tags independently checked against the pinned firmware API enum. */
enum { ARRAY_STRUCT = 18, SET = 590, SET_ROW = 591, RESULT = 699,
	CAPABILITY = 700, GET = 708, GET_TABLE = 709, GET_ROW = 710 };

static psar_u32 get32(const unsigned char *p)
{
	return (psar_u32)p[0] | (psar_u32)p[1] << 8 |
	       (psar_u32)p[2] << 16 | (psar_u32)p[3] << 24;
}

static void put32(unsigned char *p, psar_u32 v)
{
	p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24;
}

static int header(const unsigned char *p, psar_u32 tag, psar_u32 len)
{
	return get32(p) == (tag << 16 | len);
}

int psar_qca_to_wmi(psar_u32 qca, psar_u32 *wmi)
{
	if (!wmi)
		return PSAR_INVALID;
	/* QCA NONE=5 means disable. Never silently convert it to BDF4/WMI=5. */
	if (qca <= 4) {
		*wmi = qca + 1;
		return PSAR_OK;
	}
	if (qca == 6 || qca == 7) {
		*wmi = qca + 1; /* USER=7, V2=8 in the firmware namespace. */
		return PSAR_OK;
	}
	return PSAR_UNSUPPORTED;
}

static int row_valid(const struct psar_row *r)
{
	if (r->valid & ~7u)
		return 0;
	/* Canonical wildcard fields are zero, never uninitialized wire bytes. */
	if (!(r->valid & 1) && r->band)
		return 0;
	if (!(r->valid & 2) && r->chain)
		return 0;
	if (!(r->valid & 4) && r->modulation)
		return 0;
	return r->band <= 1 && r->chain <= 1 && r->modulation <= 1;
}

static int overlap(const struct psar_row *a, const struct psar_row *b)
{
	psar_u32 both = a->valid & b->valid;
	return (!(both & 1) || a->band == b->band) &&
	       (!(both & 2) || a->chain == b->chain) &&
	       (!(both & 4) || a->modulation == b->modulation);
}

int psar_encode_set(psar_u32 qca, const struct psar_row *rows, size_t count,
		    unsigned char *out, size_t capacity, size_t *written)
{
	psar_u32 selection;
	size_t i, j, size;
	int ret = psar_qca_to_wmi(qca, &selection);
	if (ret)
		return ret;
	if (!out || !written || count > PSAR_MAX_ROWS || (count && !rows))
		return PSAR_INVALID;
	if ((selection <= 5 && count) || (selection >= 7 && !count))
		return PSAR_INVALID;
	for (i = 0; i < count; i++) {
		if (!row_valid(&rows[i]) || (selection == 8 && rows[i].value > 11))
			return PSAR_UNSUPPORTED;
		for (j = 0; j < i; j++)
			if (overlap(&rows[i], &rows[j]))
				return PSAR_INVALID;
	}
	size = 20 + count * 24;
	if (capacity < size)
		return PSAR_TRUNCATED;
	/* Validate everything before mutating the caller's output. */
	put32(out, SET << 16 | 12);
	put32(out + 4, selection);
	put32(out + 8, (psar_u32)count);
	put32(out + 12, 1); /* Single complete message, commit once. */
	put32(out + 16, ARRAY_STRUCT << 16 | (psar_u32)(count * 24));
	for (i = 0; i < count; i++) {
		unsigned char *p = out + 20 + i * 24;
		put32(p, SET_ROW << 16 | 20);
		put32(p + 4, rows[i].band);
		put32(p + 8, rows[i].chain);
		put32(p + 12, rows[i].modulation);
		put32(p + 16, rows[i].value);
		put32(p + 20, rows[i].valid);
	}
	*written = size;
	return PSAR_OK;
}

int psar_encode_get(unsigned char *out, size_t capacity, size_t *written)
{
	if (!out || !written)
		return PSAR_INVALID;
	if (capacity < 8)
		return PSAR_TRUNCATED;
	put32(out, GET << 16 | 4);
	put32(out + 4, 0);
	*written = 8;
	return PSAR_OK;
}

int psar_decode_table(const unsigned char *data, size_t len,
		      struct psar_table *out)
{
	struct psar_table tmp = {0};
	size_t i, j, expected;
	if (!data || !out)
		return PSAR_INVALID;
	if (len < 16)
		return PSAR_TRUNCATED;
	if (!header(data, GET_TABLE, 8))
		return PSAR_INVALID;
	tmp.firmware_selection = get32(data + 4);
	tmp.count = get32(data + 8);
	/* OFF is a valid observation, never permission to transmit or disable SAR.
	 * TAS firmware can report an empty legacy table; retain that distinction.
	 */
	if (!(tmp.firmware_selection <= 5 ||
	      tmp.firmware_selection == 7 || tmp.firmware_selection == 8))
		return PSAR_UNSUPPORTED;
	if (tmp.count > PSAR_MAX_ROWS ||
	    (tmp.firmware_selection <= 5 && tmp.count) ||
	    (tmp.firmware_selection >= 7 && !tmp.count))
		return PSAR_INVALID;
	expected = 16 + (size_t)tmp.count * 20;
	if (len != expected)
		return len < expected ? PSAR_TRUNCATED : PSAR_INVALID;
	if (!header(data + 12, ARRAY_STRUCT, tmp.count * 20))
		return PSAR_INVALID;
	for (i = 0; i < tmp.count; i++) {
		const unsigned char *p = data + 16 + i * 20;
		struct psar_row *r = &tmp.rows[i];
		if (!header(p, GET_ROW, 16))
			return PSAR_INVALID;
		r->band = get32(p + 4);
		r->chain = get32(p + 8);
		r->modulation = get32(p + 12);
		r->value = get32(p + 16);
		r->valid = 7; /* GET rows describe exact domains, without wildcard bits. */
		if (!row_valid(r))
			return PSAR_UNSUPPORTED;
		for (j = 0; j < i; j++)
			if (overlap(r, &tmp.rows[j]))
				return PSAR_INVALID;
	}
	*out = tmp;
	return PSAR_OK;
}

int psar_decode_result(const unsigned char *data, size_t len,
		       struct psar_result *out)
{
	struct psar_result tmp;
	if (!data || !out)
		return PSAR_INVALID;
	if (len != 8 || !header(data, RESULT, 4))
		return PSAR_INVALID;
	tmp.raw_result = get32(data + 4);
	tmp.recognized = tmp.raw_result == 0 || tmp.raw_result == 1 ||
			 tmp.raw_result == 2 || tmp.raw_result == 4 || tmp.raw_result == 8;
	tmp.firmware_reported_success = tmp.raw_result == 0;
	/* Parsing success is distinct from firmware success, and from RF acceptance. */
	*out = tmp;
	return PSAR_OK;
}

int psar_decode_capability(const unsigned char *data, size_t len,
			   struct psar_capability *out)
{
	struct psar_capability tmp;
	if (!data || !out)
		return PSAR_INVALID;
	if (len != 8 || !header(data, CAPABILITY, 4))
		return PSAR_INVALID;
	tmp.raw_version = get32(data + 4);
	tmp.recognized = tmp.raw_version <= 5;
	*out = tmp;
	return PSAR_OK;
}
