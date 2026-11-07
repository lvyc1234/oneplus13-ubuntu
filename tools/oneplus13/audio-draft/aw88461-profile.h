/* SPDX-License-Identifier: GPL-2.0-only */
/* Restricted legacy AW88461 ACF preflight, before the shared ACF parser. */
#ifndef ONEPLUS13_AW88461_PROFILE_H
#define ONEPLUS13_AW88461_PROFILE_H
#ifdef AW88461_HOST_TEST
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
#else
#include <linux/types.h>
#include <linux/string.h>
#endif

static u32 aw88461_le32(const u8 *p)
{
	return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}

static u32 aw88461_le16(const u8 *p)
{
	return (u32)p[0] | (u32)p[1] << 8;
}

static u8 aw88461_crc8(const u8 *p, size_t size)
{
	u8 crc = 0;
	unsigned int i;
	while (size--) {
		u8 value = *p++;
		for (i = 0; i < 8; i++) {
			crc = (crc ^ value) & 1 ? ((crc ^ 0x18) >> 1) | 0x80 : crc >> 1;
			value >>= 1;
		}
	}
	return crc;
}

static int aw88461_acf_preflight(const u8 *b, size_t len)
{
	u32 cursor = 80 + 4 * 64, seen = 0;
	unsigned int i, j;
	if (!b || len < cursor || len > 65536 || aw88461_le32(b) != 0x0a15f908 ||
	    aw88461_le32(b + 52) != 4 * 64 || aw88461_le32(b + 56) != 4 ||
	    aw88461_le32(b + 60) != 80 || aw88461_le32(b + 64) != 1 ||
	    !memchr(b + 4, 0, 24) || !memchr(b + 28, 0, 16))
		return -1;
	for (i = 0; i < 4; i++) {
		const u8 *d = b + 80 + i * 64, *p;
		u32 index = aw88461_le16(d + 20), profile = aw88461_le16(d + 26);
		u32 size = aw88461_le32(d + 32), start = aw88461_le32(d + 36);
		u32 sum = 0, controls = 0, bit, nreg;
		if (aw88461_le32(d) != 2 || index > 1 ||
		    aw88461_le16(d + 22) != 0xffff || aw88461_le16(d + 24) != 0xffff ||
		    (profile != 0 && profile != 10) || aw88461_le32(d + 28) != 4 ||
		    !memchr(d + 4, 0, 16) || start != cursor || size < 76 || size > len - cursor)
			return -1;
		bit = 1U << (index * 2 + (profile == 10));
		if (seen & bit)
			return -1;
		seen |= bit;
		p = b + start;
		if (aw88461_le32(d + 40) != aw88461_crc8(p, size) ||
		    aw88461_le32(p + 4) != 0x01000000 || aw88461_le32(p + 8) != 0 ||
		    aw88461_le32(p + 12) != 1 || aw88461_le32(p + 16) != size - 60 ||
		    memcmp(p + 24, "AW88461\0", 8) ||
		    aw88461_le32(p + 32) != 2 || aw88461_le32(p + 36) != 2)
			return -1;
		for (j = 4; j < size; j++)
			sum += p[j];
		if (sum != aw88461_le32(p) || (size - 64) % 4)
			return -1;
		nreg = aw88461_le32(p + 60);
		if (nreg != (size - 64) / 4)
			return -1;
		for (j = 0; j < nreg; j++) {
			u32 address = aw88461_le16(p + 64 + j * 4);
			u32 value = aw88461_le16(p + 66 + j * 4);
			if (address == 0xff && !value)
				continue;
			if (!((address >= 3 && address <= 0x1f) ||
			      (address >= 0x30 && address <= 0x38) ||
			      (address >= 0x3a && address <= 0x3f) ||
			      (address >= 0x50 && address <= 0x6d)))
				return -1;
			if (address == 4 || address == 5) {
				bit = 1U << (address - 4);
				if (controls & bit)
					return -1;
				controls |= bit;
			}
		}
		if (controls != 3)
			return -1;
		cursor += size;
	}
	return cursor == len && seen == 15 ? 0 : -1;
}
#endif
