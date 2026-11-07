// SPDX-License-Identifier: GPL-2.0-only
/* Copyright (c) 2026 OpenAI */
/*
 * AA569 SPR2 profile validation and transaction planning only.
 * No MMIO, REGDMA submission, device probing or runtime callbacks exist here.
 * Packing follows the public OnePlus d50b305f SPR v2 helpers; calibration is
 * supplied privately. See README.md for provenance and missing prerequisites.
 */
#include <linux/bits.h>
#include <linux/crc32.h>
#include <linux/errno.h>
#include <linux/string.h>
#include <linux/unaligned.h>

#include "dpu_aa569_spr.h"

static int aa569_spr_validate(const struct aa569_spr_profile *profile)
{
	const u32 *w;
	u32 mask;
	int i;

	if (!profile)
		return -EINVAL;
	w = profile->words;
	for (i = 0; i < AA569_SPR_WORDS; i++) {
		if (i < 3 || i == 12 || i == 13)
			mask = GENMASK(29, 0);
		else if (i == 3)
			mask = GENMASK(9, 0);
		else if (i < 12)
			mask = 0x07ff07ff;
		else if (i < 30)
			mask = 0x0fff0fff;
		else if (i == 30)
			mask = 0x07773333;
		else if (i == 31)
			mask = GENMASK(22, 0) | BIT(24) | BIT(26);
		else if (i == 32)
			mask = BIT(0);
		else
			mask = 0;
		if (w[i] & ~mask)
			return -EINVAL;
	}

	/* Only the investigated PenTile/2DAvg/YYGM, two-LM full-frame route. */
	mask = GENMASK(22, 16) | BIT(26);
	if ((w[31] & ~mask) != 0x01009107 || w[32] != 1 || w[33])
		return -EINVAL;
	if (!(w[31] & BIT(26)) && w[30])
		return -EINVAL;
	for (i = 0; i < 4; i++)
		if (((w[30] >> (i * 4)) & 3) == 3)
			return -EINVAL;
	return 0;
}

int aa569_spr_decode(const u8 *data, size_t len, struct aa569_spr_profile *out)
{
	struct aa569_spr_profile profile;
	u32 crc;
	int i, ret;

	if (!data || !out || len != AA569_SPR_PROFILE_SIZE)
		return -EINVAL;
	if (memcmp(data, "AA569S2\0", 8) || get_unaligned_le32(data + 8) != 1 ||
	    get_unaligned_le32(data + 12) != 1440 ||
	    get_unaligned_le32(data + 16) != 3168 ||
	    get_unaligned_le32(data + 20) != 120 ||
	    get_unaligned_le32(data + 24) != AA569_SPR_WORDS ||
	    get_unaligned_le32(data + 32) || get_unaligned_le32(data + 36))
		return -EINVAL;
	crc = crc32_le(~0U, data + 40, len - 40) ^ ~0U;
	if (crc != get_unaligned_le32(data + 28))
		return -EBADMSG;
	for (i = 0; i < AA569_SPR_WORDS; i++)
		profile.words[i] = get_unaligned_le32(data + 40 + i * 4);
	ret = aa569_spr_validate(&profile);
	if (ret)
		return ret;
	*out = profile;
	return 0;
}

static void aa569_spr_add(struct aa569_spr_plan *plan, u32 offset, u32 value,
			 enum aa569_spr_write_path path)
{
	struct aa569_spr_write *write = &plan->writes[plan->count++];

	write->offset = offset;
	write->value = value;
	write->path = path;
	write->preserve_mask = offset == 4 ? BIT(25) : 0;
}

int aa569_spr_plan(const struct aa569_spr_profile *profile,
		   struct aa569_spr_plan *out)
{
	struct aa569_spr_plan plan = {};
	const u32 *w;
	int i, ret;

	if (!out)
		return -EINVAL;
	ret = aa569_spr_validate(profile);
	if (ret)
		return ret;
	w = profile->words;
	/* PBA is a separate CPU write before the vendor INIT DMA kickoff. */
	if (w[31] & BIT(26))
		aa569_spr_add(&plan, 0x7c, w[30], AA569_SPR_PBA_DIRECT);
	for (i = 0; i < 4; i++)
		aa569_spr_add(&plan, 0x24 + i * 4, w[i], AA569_SPR_INIT_REGDMA);
	for (i = 0; i < 8; i++)
		aa569_spr_add(&plan, 0x34 + i * 4, w[4 + i], AA569_SPR_INIT_REGDMA);
	aa569_spr_add(&plan, 0x70, w[12], AA569_SPR_INIT_REGDMA);
	aa569_spr_add(&plan, 0x74, w[13], AA569_SPR_INIT_REGDMA);
	for (i = 0; i < 16; i++)
		aa569_spr_add(&plan, 0xa0 + i * 4, w[14 + i], AA569_SPR_INIT_REGDMA);
	aa569_spr_add(&plan, 4, w[31], AA569_SPR_INIT_REGDMA);
	aa569_spr_add(&plan, 8, w[32], AA569_SPR_INIT_REGDMA);
	/* PU is separately owned and must not be folded into SPR_INIT. */
	aa569_spr_add(&plan, 0x20, w[33], AA569_SPR_PU_REGDMA);
	*out = plan;
	return 0;
}
