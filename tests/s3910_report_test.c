/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <string.h>
#include "../devices/oneplus13/kernel/synaptics-dodge/s3910-report.h"

int main(void)
{
	struct s3910_point points[10];
	/* Reordered slots: lifted slot1, finger at (300,600) in slot0. */
	unsigned char frame[] = { 1,0,0,0,0,0,0,0, 0,1,44,1,88,2,12,9 };
	unsigned char mutated[sizeof(frame)];
	unsigned int i;
	assert(!s3910_decode(frame, sizeof(frame), 2, 1000, 2000, points));
	assert(points[0].status == 1 && points[0].x == 300 && points[0].y == 600);
	assert(points[0].major == 12 && points[0].minor == 9 && !points[1].status);
	for (i = 0; i < sizeof(frame); i++)
		assert(s3910_decode(frame, i, 2, 1000, 2000, points) < 0);
	assert(s3910_decode(frame, sizeof(frame), 11, 1000, 2000, points) < 0);
	memcpy(mutated, frame, sizeof(frame));
	mutated[8] = 1; /* duplicate index must never overwrite a valid contact */
	assert(s3910_decode(mutated, sizeof(mutated), 2, 1000, 2000, points) < 0);
	mutated[8] = 2;
	assert(s3910_decode(mutated, sizeof(mutated), 2, 1000, 2000, points) < 0);
	assert(s3910_decode(frame, sizeof(frame), 2, 299, 2000, points) < 0);
	assert(s3910_decode(frame, sizeof(frame), 2, 1000, 599, points) < 0);
	return 0;
}
