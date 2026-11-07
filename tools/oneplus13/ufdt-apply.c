/* SPDX-License-Identifier: GPL-2.0-only */
/* Host-only libufdt harness. Inputs and output are regular host files. */
#include <stdio.h>
#include <stdlib.h>
#include <libfdt.h>
#include <libufdt.h>
#include <ufdt_overlay.h>

static void *read_file(const char *name, size_t *size)
{
	FILE *fp = fopen(name, "rb");
	long length;
	void *data;
	if (!fp || fseek(fp, 0, SEEK_END) || (length = ftell(fp)) < 40 ||
	    length > 64 * 1024 * 1024 || fseek(fp, 0, SEEK_SET))
		exit(2);
	data = malloc(length);
	if (!data || fread(data, 1, length, fp) != (size_t)length)
		exit(2);
	fclose(fp);
	*size = length;
	return data;
}

int main(int argc, char **argv)
{
	size_t base_size, overlay_size, result_size;
	void *base, *overlay, *result;
	FILE *output;
	if (argc != 4)
		return 2;
	base = read_file(argv[1], &base_size);
	overlay = read_file(argv[2], &overlay_size);
	result = ufdt_apply_overlay(base, base_size, overlay, overlay_size);
	if (!result || fdt_check_header(result))
		return 3;
	result_size = fdt_totalsize(result);
	output = fopen(argv[3], "wb");
	if (!output || fwrite(result, 1, result_size, output) != result_size)
		return 4;
	if (fclose(output))
		return 4;
	dto_free(result);
	free(base);
	free(overlay);
	return 0;
}
