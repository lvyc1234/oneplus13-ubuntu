/* SPDX-License-Identifier: GPL-2.0-only */
/* Experimental OnePlus 13 ultrawide C-PHY adapter. Not hardware qualified.
 * Included by phy-qcom-mipi-csi2-3ph-dphy.c in the isolated phone candidate.
 * Register data/order: pinned OnePlusOSS d50b305f, Qualcomm GPL driver.
 */
#include <linux/clk.h>
#include <linux/iopoll.h>
#include <linux/limits.h>
#include <linux/math64.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include "dodge-cphy-catalog.h"

#define DODGE_CPHY_TIMER_HZ 400000000U
#define DODGE_CPHY_IRQ_COUNT 11

struct dodge_cphy_state {
	struct phone_cphy_plan plan;
	int irq;
	bool irq_enabled;
};

static void dodge_cphy_mask_interrupts(struct mipi_csi2phy_device *phy)
{
	unsigned int i;

	for (i = 0; i < DODGE_CPHY_IRQ_COUNT; i++)
		writel(0, phy->base + 0x102c + 4 * i);
	readl(phy->base + 0x1054);
}

static irqreturn_t dodge_cphy_irq(int irq, void *data)
{
	struct mipi_csi2phy_device *phy = data;
	u32 status[DODGE_CPHY_IRQ_COUNT];
	u32 pending = 0;
	unsigned int i;

	for (i = 0; i < DODGE_CPHY_IRQ_COUNT; i++) {
		status[i] = readl(phy->base + 0x1138 + 4 * i);
		pending |= status[i];
		writel(status[i], phy->base + 0x1058 + 4 * i);
	}
	if (!pending)
		return IRQ_NONE;
	writel(1, phy->base + 0x1028);
	writel(0, phy->base + 0x1028);
	for (i = 0; i < DODGE_CPHY_IRQ_COUNT; i++)
		writel(0, phy->base + 0x1058 + 4 * i);
	return IRQ_HANDLED;
}

static int dodge_cphy_init(struct mipi_csi2phy_device *phy)
{
	struct platform_device *pdev = to_platform_device(phy->dev);
	struct dodge_cphy_state *state;
	int ret;

	state = devm_kzalloc(phy->dev, sizeof(*state), GFP_KERNEL);
	if (!state)
		return -ENOMEM;
	state->irq = platform_get_irq(pdev, 0);
	if (state->irq < 0)
		return state->irq;
	/* Clocks are off at probe; the interrupt must remain disabled. */
	ret = devm_request_irq(phy->dev, state->irq, dodge_cphy_irq,
			       IRQF_NO_AUTOEN, dev_name(phy->dev), phy);
	if (ret)
		return ret;
	phy->hw_priv = state;
	return 0;
}

static int dodge_cphy_validate(struct mipi_csi2phy_device *phy,
			       struct mipi_csi2phy_stream_cfg *cfg)
{
	struct dodge_cphy_state *state = phy->hw_priv;
	u32 trios = 0;
	unsigned int i;

	if (!state || phy->phy_mode != PHY_MODE_MIPI_CPHY ||
	    cfg->num_data_lanes != 3 || cfg->link_freq <= 0 ||
	    (u64)cfg->link_freq > U64_MAX / 2)
		return -EINVAL;
	for (i = 0; i < 3; i++) {
		if (cfg->lane_cfg.data[i].pos >= 3 ||
		    cfg->lane_cfg.data[i].pol ||
		    (trios & BIT(cfg->lane_cfg.data[i].pos)))
			return -EINVAL;
		trios |= BIT(cfg->lane_cfg.data[i].pos);
	}
	/* Heap-owned plan: keep its 3 KiB array off the kernel stack. */
	if (phone_cphy_build_plan(&state->plan, (u64)cfg->link_freq * 2,
		DODGE_CPHY_TIMER_HZ, cfg->t_prepare_ns, cfg->t_preamble_ns, trios))
		return -ERANGE;
	return 0;
}

static void dodge_cphy_reset(struct mipi_csi2phy_device *phy)
{
	/* Stock reset-enter table: assertion stays set until the final plan op. */
	writel(1, phy->base + 0x1000);
	readl(phy->base + 0x1000);
	usleep_range(1, 6);
}

static int dodge_cphy_core_rate(struct mipi_csi2phy_device *phy,
				unsigned long *rate)
{
	u64 symbol_rate, minimum;

	if (phy->stream_cfg.link_freq <= 0)
		return -EINVAL;
	symbol_rate = (u64)phy->stream_cfg.link_freq * 2;
	if (phone_cphy_rate_index(symbol_rate) < 0)
		return -ERANGE;
	/* dodge-ultrawide selects divisor32 in cam_csiphy_soc.c. The stock
	 * selector adds one after integer division and then uses strictly >.
	 * OPP find_freq_ceil uses >=, so include both integer boundary steps.
	 */
	minimum = div_u64(div_u64(symbol_rate * 228, 100), 32) + 2;
	if (minimum > ULONG_MAX)
		return -ERANGE;
	*rate = minimum;
	return 0;
}

static int dodge_cphy_operation(void *context, const struct phone_cphy_op *op)
{
	struct mipi_csi2phy_device *phy = context;
	u32 value;

	if (op->address > 0x1184 || op->address % 4)
		return -EINVAL;
	switch (op->type) {
	case PHONE_POLL:
		return readl_poll_timeout(phy->base + op->address, value,
			(value & op->mask) == op->value, op->delay_us, op->timeout_us);
	case PHONE_UPDATE:
		value = readl(phy->base + op->address);
		value = (value & ~op->mask) | (op->value & op->mask);
		break;
	case PHONE_WRITE:
		value = op->value;
		break;
	default:
		return -EINVAL;
	}
	writel(value, phy->base + op->address);
	if (op->delay_us)
		usleep_range(op->delay_us, op->delay_us + 5);
	return 0;
}

static void dodge_cphy_lanes_disable(struct mipi_csi2phy_device *phy,
				    struct mipi_csi2phy_stream_cfg *cfg)
{
	struct dodge_cphy_state *state = phy->hw_priv;
	u32 value;
	unsigned int i;

	/* Keep MMIO powered until the last interrupt handler has returned. */
	dodge_cphy_mask_interrupts(phy);
	if (state->irq_enabled) {
		disable_irq(state->irq);
		state->irq_enabled = false;
	}
	for (i = 0; i < ARRAY_SIZE(phone_common); i++)
		writel(0, phy->base + phone_common[i].address);
	value = readl(phy->base + 0x1014);
	writel(value & ~0x2aU, phy->base + 0x1014);
	dodge_cphy_reset(phy);
}

static int dodge_cphy_lanes_enable(struct mipi_csi2phy_device *phy,
				   struct mipi_csi2phy_stream_cfg *cfg)
{
	struct dodge_cphy_state *state = phy->hw_priv;
	int ret;

	ret = dodge_cphy_validate(phy, cfg);
	if (ret)
		return ret;
	if (phy->timer_clk_rate != DODGE_CPHY_TIMER_HZ ||
	    clk_get_rate(phy->timer_clk) != DODGE_CPHY_TIMER_HZ)
		return -ERANGE;
	dodge_cphy_mask_interrupts(phy);
	dodge_cphy_irq(state->irq, phy);
	enable_irq(state->irq);
	state->irq_enabled = true;
	/* The generic core calls lanes_disable on any failure, before clock-off. */
	return phone_cphy_execute(&state->plan, dodge_cphy_operation, phy);
}

static const struct mipi_csi2phy_hw_ops dodge_cphy_ops = {
	.init = dodge_cphy_init,
	.validate = dodge_cphy_validate,
	.core_rate = dodge_cphy_core_rate,
	.hw_version_read = phy_qcom_mipi_csi2_hw_version_read,
	.reset = dodge_cphy_reset,
	.lanes_enable = dodge_cphy_lanes_enable,
	.lanes_disable = dodge_cphy_lanes_disable,
};

/* The clock/domain/supply API matches the staged SM8750 generic provider.
 * An enabled board graph and exact regulator votes still require review.
 */
const struct mipi_csi2phy_soc_cfg mipi_csi2_cphy_dodge_ultrawide = {
	.ops = &dodge_cphy_ops,
	.fixed_timer_rate = DODGE_CPHY_TIMER_HZ,
	.minimum_reg_size = 0x1188,
	.reg_info = { .common_regs_offset = 0x1000, .common_status_offset = 0x138 },
	.supply_names = (const char **)x1e_supplies,
	.num_supplies = ARRAY_SIZE(x1e_supplies),
	.clk_names = (const char **)x1e_clks,
	.num_clk = ARRAY_SIZE(x1e_clks),
	.genpds = x1e_genpds,
	.num_genpds = ARRAY_SIZE(x1e_genpds),
};
