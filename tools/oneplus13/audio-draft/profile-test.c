/* SPDX-License-Identifier: GPL-2.0-only */
#define AW88461_HOST_TEST
#include "aw88461-profile.h"
#include <assert.h>
#include <stdio.h>
#undef assert
#define assert(c) do { if (!(c)) { fprintf(stderr, "Failed line %d: %s\n", __LINE__, #c); return 1; } } while (0)

#define SIZE (336 + 4 * 76)
static u8 original[SIZE], buffer[SIZE + 1];
static void put32(u8 *p, u32 v)
{
	p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24;
}

static void repair(unsigned int section)
{
	u8 *d = buffer + 80 + section * 64;
	u8 *p = buffer + 336 + section * 76;
	u32 sum = 0;
	unsigned int j;
	for (j = 4; j < 76; j++) sum += p[j];
	put32(p, sum);
	put32(d + 40, aw88461_crc8(p, 76));
}

int main(int argc, char **argv)
{
	unsigned int i, j, rejected = 0;
	put32(buffer, 0x0a15f908); put32(buffer + 52, 256);
	put32(buffer + 56, 4); put32(buffer + 60, 80); put32(buffer + 64, 1);
	for (i = 0; i < 4; i++) {
		u8 *d = buffer + 80 + 64 * i, *p = buffer + 336 + 76 * i;
		put32(d, 2); d[20] = i / 2; memset(d + 22, 0xff, 4); d[26] = i % 2 ? 10 : 0;
		put32(d + 28, 4); put32(d + 32, 76); put32(d + 36, 336 + 76 * i);
		put32(p + 4, 0x01000000); put32(p + 12, 1); put32(p + 16, 16);
		memcpy(p + 24, "AW88461", 8); put32(p + 32, 2); put32(p + 36, 2); put32(p + 60, 3);
		p[64] = 4; p[68] = 5; p[72] = 0xff;
		repair(i);
	}
	assert(!aw88461_acf_preflight(buffer, SIZE));
	memcpy(original, buffer, SIZE);
	for (i = 0; i < SIZE; i++) {
		assert(aw88461_acf_preflight(buffer, i)); rejected++;
	}
	assert(aw88461_acf_preflight(NULL, SIZE));
	assert(aw88461_acf_preflight(buffer, 65537));
	assert(aw88461_acf_preflight(buffer, SIZE + 1));
	for (i = 336; i < SIZE; i++) {
		buffer[i] ^= 1;
		assert(aw88461_acf_preflight(buffer, SIZE)); rejected++;
		buffer[i] ^= 1;
	}
	for (i = 0; i < 4; i++) {
		unsigned int fields[] = {0, 20, 22, 24, 26, 28, 32, 36, 40};
		for (j = 0; j < sizeof(fields)/sizeof(fields[0]); j++) {
			memcpy(buffer, original, SIZE); buffer[80 + i * 64 + fields[j]] ^= 1;
			assert(aw88461_acf_preflight(buffer, SIZE)); rejected++;
		}
	}
	/* Semantic malformed inner headers with valid CRC/checksum. */
	for (i = 0; i < 4; i++) {
		unsigned int fields[] = {4, 8, 12, 16, 24, 32, 36, 60, 64, 72};
		for (j = 0; j < sizeof(fields)/sizeof(fields[0]); j++) {
			memcpy(buffer, original, SIZE); put32(buffer + 336 + i * 76 + fields[j], 0xffffffff);
			repair(i); assert(aw88461_acf_preflight(buffer, SIZE)); rejected++;
		}
	}
	memcpy(buffer + 1, original, SIZE);
	assert(!aw88461_acf_preflight(buffer + 1, SIZE));
	printf("AW88461 synthetic profile preflight passed: %u bad inputs rejected, unaligned valid input accepted.\n", rejected + 3);
	if (argc == 2) {
		u8 file_data[65537];
		FILE *input = fopen(argv[1], "rb");
		size_t size;
		assert(input);
		size = fread(file_data, 1, sizeof(file_data), input);
		assert(!ferror(input) && feof(input));
		fclose(input);
		assert(!aw88461_acf_preflight(file_data, size));
		puts("Host-only reference file passes restricted preflight; owner version and hardware remain unverified.");
	}
	return 0;
}
