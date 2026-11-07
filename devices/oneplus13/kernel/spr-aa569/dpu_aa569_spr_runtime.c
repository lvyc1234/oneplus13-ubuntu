// SPDX-License-Identifier: GPL-2.0-only
/* Copyright (c) 2026 OpenAI */
/*
 * Opt-in, fixed AA569 SPR2 bootstrap for a reset, exclusive SM8750 DPU.
 * CPU writes occur only at probe or at a quiet PM boundary with clocks on.
 * There are no DMA buffers, live profile updates, partial updates or UDC use.
 */
#include <linux/firmware.h>
#include <linux/io.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/string.h>
#include <drm/drm_crtc.h>
#include <drm/drm_managed.h>

#include "dpu_kms.h"
#include "dpu_encoder.h"
#include "dpu_aa569_spr.h"
#include "dpu_aa569_spr_runtime.h"

struct aa569_spr_runtime {
	struct aa569_spr_plan plan;
	bool enabled;
};

static int aa569_spr_layout(const struct dpu_kms *kms)
{
	static const u32 dspp_base[] = {0x54000, 0x56000, 0x58000, 0x5a000};
	static const u32 spr_base[] = {0x15400, 0x14400, 0x13400, 0x12400};
	const struct dpu_mdss_cfg *cat = kms->catalog;
	unsigned int i;

	if (!cat || cat->dspp_count != 4)
		return -EINVAL;
	for (i = 0; i < 4; i++) {
		const struct dpu_dspp_cfg *dspp = &cat->dspp[i];

		if (!dspp->sblk || dspp->base != dspp_base[i] ||
		    dspp->sblk->spr.base != spr_base[i] ||
		    dspp->sblk->spr.len != 0x200 ||
		    dspp->sblk->spr.version != 0x20000)
			return -EINVAL;
	}
	return 0;
}

static int aa569_spr_apply(struct dpu_kms *kms, bool enable)
{
	struct aa569_spr_runtime *ctx = kms->aa569_spr;
	struct drm_encoder *encoder;
	unsigned int i, j;
	int ret;

	if (!ctx)
		return 0;
	if (!kms->aa569_spr_clocks_on)
		return -EHOSTDOWN;
	if (!kms->mmio || !kms->dev)
		return -EINVAL;
	ret = aa569_spr_layout(kms);
	if (ret)
		return ret;
	drm_for_each_encoder(encoder, kms->dev)
		if (!dpu_encoder_spr_quiescent(encoder))
			return -EBUSY;

	/* Check every pipe before the first write, including unallocated pipes.
	 * This backend owns the whole fixed-profile DPU; it never clears UDC.
	 */
	for (i = 0; i < 4; i++) {
		const struct dpu_dspp_cfg *dspp = &kms->catalog->dspp[i];
		u32 base = dspp->base + dspp->sblk->spr.base;

		if (readl(kms->mmio + base + 4) & BIT(25))
			return -EBUSY;
	}

	for (i = 0; i < 4; i++) {
		const struct dpu_dspp_cfg *dspp = &kms->catalog->dspp[i];
		u32 base = dspp->base + dspp->sblk->spr.base;

		if (!enable) {
			writel(0, kms->mmio + base + 4);
			writel(0, kms->mmio + base + 8);
			continue;
		}
		/* The checked plan's distinct paths are executed synchronously here
		 * while stopped. This is not a REGDMA implementation.
		 */
		for (j = 0; j < ctx->plan.count; j++) {
			const struct aa569_spr_write *write = &ctx->plan.writes[j];
			u32 value = write->value;

			if (write->preserve_mask)
				value |= readl(kms->mmio + base + write->offset) &
					 write->preserve_mask;
			writel(value, kms->mmio + base + write->offset);
		}
	}
	/* Complete posted writes before clocks stop or encoder state resumes.
	 * The read is a posting barrier, not a hardware-acceptance assertion.
	 */
	wmb();
	readl(kms->mmio + kms->catalog->dspp[3].base +
	      kms->catalog->dspp[3].sblk->spr.base + 4);
	ctx->enabled = enable;
	return 0;
}

int dpu_aa569_spr_restore(struct dpu_kms *kms)
{
	return aa569_spr_apply(kms, true);
}

int dpu_aa569_spr_suspend(struct dpu_kms *kms)
{
	return aa569_spr_apply(kms, false);
}

/* Match both the compiled board and the exact ABL-rewritten physical phone.
 * Generic Sun compatibility alone does not identify this panel or board.
 * The independent DPU opt-in, compatible, reset and catalog gates remain.
 */
static bool aa569_spr_machine(void)
{
	struct device_node *root;
	const char *model, *chassis;
	bool match = false;

	root = of_find_node_by_path("/");
	if (!root)
		return false;
	if (of_property_read_string(root, "model", &model) ||
	    of_property_read_string(root, "chassis-type", &chassis) ||
	    strcmp(chassis, "handset"))
		goto out;
	if (!strcmp(model, "OnePlus 13 (AA569 native display experimental candidate)"))
		match = of_device_is_compatible(root, "oneplus,dodge") &&
			of_device_is_compatible(root, "qcom,sm8750");
	else if (!strcmp(model, "Qualcomm Technologies, Inc. Sun MTP,dodge"))
		match = of_device_is_compatible(root, "qcom,sun");
out:
	of_node_put(root);
	return match;
}

int dpu_aa569_spr_load(struct dpu_kms *kms)
{
	struct device *dev = &kms->pdev->dev;
	struct aa569_spr_profile profile;
	struct aa569_spr_runtime *ctx;
	const struct firmware *fw;
	int ret;

	if (!of_property_read_bool(dev->of_node, "oneplus,aa569-static-spr"))
		return 0;
	if (!aa569_spr_machine() ||
	    !of_device_is_compatible(dev->of_node, "qcom,sm8750-dpu") ||
	    !dev->parent || !dev->parent->of_node ||
	    !of_property_read_bool(dev->parent->of_node, "resets"))
		return dev_err_probe(dev, -EINVAL, "AA569 SPR requires reset OnePlus13 SM8750\n");
	ret = aa569_spr_layout(kms);
	if (ret)
		return dev_err_probe(dev, ret, "AA569 SPR catalog layout\n");
	ret = request_firmware_direct(&fw,
		"qcom/sm8750/oneplus/dodge/aa569-spr-v1.bin", dev);
	if (ret)
		return dev_err_probe(dev, ret, "Private AA569 SPR profile required\n");
	ret = aa569_spr_decode(fw->data, fw->size, &profile);
	release_firmware(fw);
	if (ret)
		return dev_err_probe(dev, ret, "Invalid AA569 SPR profile\n");
	ctx = drmm_kzalloc(kms->dev, sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;
	ret = aa569_spr_plan(&profile, &ctx->plan);
	if (ret)
		return ret;
	kms->aa569_spr = ctx;
	return 0;
}

int dpu_aa569_spr_check(struct drm_crtc *crtc, struct drm_crtc_state *state)
{
	struct msm_drm_private *priv = crtc->dev->dev_private;
	struct dpu_kms *kms = to_dpu_kms(priv->kms);
	const struct drm_display_mode *mode = &state->adjusted_mode;
	struct drm_encoder *encoder;
	unsigned int count = 0;

	if (!kms->aa569_spr || !state->enable)
		return 0;
	if (state->self_refresh_active || mode->hdisplay != 1440 ||
	    mode->vdisplay != 3168 || mode->htotal != 1562 ||
	    mode->vtotal != 3200 || mode->clock != 599808 || mode->flags)
		return -EINVAL;
	drm_for_each_encoder_mask(encoder, crtc->dev, state->encoder_mask) {
		if (!dpu_encoder_aa569_format(encoder))
			return -EINVAL;
		count++;
	}
	return count == 1 ? 0 : -EINVAL;
}
