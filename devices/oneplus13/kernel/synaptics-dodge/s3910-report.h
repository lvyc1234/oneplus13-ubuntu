/* SPDX-License-Identifier: GPL-2.0-only */
/* Fixed, byte-aligned descriptor negotiated in RAM; no vendor bytecode parser. */
#ifndef DODGE_S3910_REPORT_H
#define DODGE_S3910_REPORT_H
#ifdef __KERNEL__
#include <linux/types.h>
typedef u8 s3910_u8;
typedef u16 s3910_u16;
#else
#include <stdint.h>
#include <stddef.h>
typedef uint8_t s3910_u8;
typedef uint16_t s3910_u16;
#endif

struct s3910_point {
	s3910_u16 x, y;
	s3910_u8 status, major, minor;
};

/* foreach object; index8, class8, x16, y16, major8, minor8; end foreach/end */
static const s3910_u8 s3910_descriptor[] = {
	0x02, 0x06, 8, 0x07, 8, 0x08, 16, 0x09, 16,
	0x0b, 8, 0x0c, 8, 0x03, 0x00,
};

static inline int s3910_decode(const s3910_u8 *data, size_t size, unsigned int count,
			      unsigned int max_x, unsigned int max_y,
			      struct s3910_point *points)
{
	unsigned int seen = 0, i;
	if (!data || !points || !count || count > 10 || size != count * 8)
		return -22;
	for (i = 0; i < count; i++, data += 8) {
		unsigned int slot = data[0];
		unsigned int x = data[2] | data[3] << 8;
		unsigned int y = data[4] | data[5] << 8;
		if (slot >= count || (seen & (1U << slot)) || data[1] > 9 ||
		    (data[1] && (x > max_x || y > max_y)))
			return -22;
		seen |= 1U << slot;
		points[slot] = (struct s3910_point) {
			.x = x, .y = y, .status = data[1],
			.major = data[6], .minor = data[7],
		};
	}
	return 0;
}
#endif
