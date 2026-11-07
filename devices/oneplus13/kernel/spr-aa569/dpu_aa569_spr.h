/* SPDX-License-Identifier: GPL-2.0-only */
/* Copyright (c) 2026 OpenAI */
#ifndef DPU_AA569_SPR_H
#define DPU_AA569_SPR_H

#include <linux/types.h>

#define AA569_SPR_WORDS 34
#define AA569_SPR_PROFILE_SIZE (40 + 4 * AA569_SPR_WORDS)
#define AA569_SPR_MAX_WRITES 34

/* Private host-generated format; not a vendor firmware or DRM userspace ABI. */
struct aa569_spr_profile {
	u32 words[AA569_SPR_WORDS];
};

enum aa569_spr_write_path {
	AA569_SPR_PBA_DIRECT,
	AA569_SPR_INIT_REGDMA,
	AA569_SPR_PU_REGDMA,
};

struct aa569_spr_write {
	u32 offset;
	u32 value;
	u32 preserve_mask;
	enum aa569_spr_write_path path;
};

struct aa569_spr_plan {
	u32 count;
	struct aa569_spr_write writes[AA569_SPR_MAX_WRITES];
};

/* Output remains unchanged on failure. Neither function accesses hardware. */
int aa569_spr_decode(const u8 *data, size_t len, struct aa569_spr_profile *out);
int aa569_spr_plan(const struct aa569_spr_profile *profile,
		   struct aa569_spr_plan *out);

#endif
