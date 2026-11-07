/* SPDX-License-Identifier: GPL-2.0-only */
/* Offline parser only: external file is never embedded or printed. */
#define AW88461_HOST_TEST
#include <stdio.h>
#include <stdlib.h>
#include "aw88461-profile.h"
int main(int argc, char **argv)
{
    unsigned char data[65537];
    size_t size;
    FILE *input;
    if (argc != 2) return 2;
    input = fopen(argv[1], "rb");
    if (!input) return 3;
    size = fread(data, 1, sizeof(data), input);
    if (ferror(input)) { fclose(input); return 4; }
    fclose(input);
    if (aw88461_acf_preflight(data, size)) return 1;
    puts("OFFLINE_ACF_PREFLIGHT_PASS; no device access or playback");
    return 0;
}
