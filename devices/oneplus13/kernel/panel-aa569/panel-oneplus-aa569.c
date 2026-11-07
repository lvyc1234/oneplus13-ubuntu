// SPDX-License-Identifier: GPL-2.0-only
/*
 * OnePlus 13 AA569 P3 A0019 component; not a complete display pipeline.
 * Public packets: OnePlusOSS d50b305f7da9; generated header retains all bytes.
 * Only the isolated experimental native DTB enables this component.
 * Author: Codex. No hardware Tested-by claim.
 */
#include <linux/backlight.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>
#include <video/mipi_display.h>
#include <drm/display/drm_dsc.h>
#include <drm/drm_connector.h>
#include <drm/drm_mipi_dsi.h>
#include <drm/drm_modes.h>
#include <drm/drm_panel.h>
#include "aa569-data.h"

struct aa569 {
	struct drm_panel panel;
	struct mipi_dsi_device *dsi;
	struct regulator *vddio, *vci;
	struct gpio_desc *reset, *vddr;
	struct mutex lock;
	struct drm_dsc_config dsc;
	struct backlight_device *backlight;
	bool vddio_on, vci_on, vddr_on, prepared, enabled, off_sent;
};

static struct aa569 *to_aa569(struct drm_panel *panel)
{
	return container_of(panel, struct aa569, panel);
}

static int aa569_send(struct aa569 *ctx, const struct aa569_cmd *cmd,
		      const u8 *bytes, size_t count, bool lp)
{
	struct mipi_dsi_msg *msgs;
	size_t i, first, n = 0, max = 0;
	int ret = 0;

	/* Reject a split vendor group before any transfer or power-mode change. */
	if (!count)
		return -EINVAL;
	for (i = 0; i < count; i++) {
		if (++n > 64 || (cmd[i].batch && cmd[i].delay_ms))
			return -EINVAL;
		if (!cmd[i].batch) {
			if (n > max)
				max = n;
			n = 0;
		}
	}
	if (n)
		return -EINVAL;
	msgs = kcalloc(max, sizeof(*msgs), GFP_KERNEL);
	if (!msgs)
		return -ENOMEM;
	for (first = 0, i = 0; i < count; i++) {
		n = i - first;
		msgs[n] = (struct mipi_dsi_msg) {
			.channel = ctx->dsi->channel,
			.type = cmd[i].type,
			.flags = lp ? MIPI_DSI_MSG_USE_LPM : 0,
			.tx_buf = bytes + cmd[i].offset,
			.tx_len = cmd[i].length,
		};
		if (cmd[i].batch)
			continue;
		ret = mipi_dsi_transfer_batch(ctx->dsi, msgs, n + 1);
		if (ret)
			break; /* failed DMA may have delivered data; never auto-retry */
		if (cmd[i].delay_ms)
			msleep(cmd[i].delay_ms);
		first = i + 1;
	}
	kfree(msgs);
	return ret;
}

/* Compatibility blanking leaves KMS/SPR running: only DBV0 or the already
 * qualified normal starting DBV1433. No low-DBV range, HBM or new calibration.
 */
static int aa569_backlight_update_status(struct backlight_device *backlight)
{
	struct aa569 *ctx = bl_get_data(backlight);
	int level = backlight_get_brightness(backlight);
	u8 bytes[] = { 0x51, 0, 0 };
	const struct aa569_cmd cmd = {
		.length = sizeof(bytes), .type = MIPI_DSI_DCS_LONG_WRITE,
	};
	int ret = 0;

	if (level < 0 || level > 1)
		return -EINVAL;
	if (level) {
		bytes[1] = 0x05;
		bytes[2] = 0x99;
	}
	mutex_lock(&ctx->lock);
	/* DRM can update a disabled backlight during teardown; remember props
	 * without sending a DCS transaction through a powered-down DSI host.
	 */
	if (ctx->enabled && ctx->prepared && ctx->vddio_on &&
	    ctx->vci_on && ctx->vddr_on)
		ret = aa569_send(ctx, &cmd, bytes, 1, false);
	mutex_unlock(&ctx->lock);
	return ret;
}

static const struct backlight_ops aa569_backlight_ops = {
	.update_status = aa569_backlight_update_status,
};

static int aa569_power_off(struct aa569 *ctx)
{
	int ret;

	gpiod_set_value_cansleep(ctx->reset, 1); /* active-low RESET asserted */
	msleep(1);
	if (ctx->vci_on) {
		ret = regulator_disable(ctx->vci);
		if (ret)
			return ret; /* keep upstream rails if VCI did not turn off */
		ctx->vci_on = false;
		ret = regulator_set_load(ctx->vci, 300);
		if (ret < 0)
			dev_warn(&ctx->dsi->dev, "VCI off load: %d\n", ret);
	}
	msleep(3);
	if (ctx->vddr_on) {
		gpiod_set_value_cansleep(ctx->vddr, 0);
		ctx->vddr_on = false;
	}
	msleep(3);
	if (ctx->vddio_on) {
		ret = regulator_disable(ctx->vddio);
		if (ret)
			return ret;
		ctx->vddio_on = false;
		ret = regulator_set_load(ctx->vddio, 8000);
		if (ret < 0)
			dev_warn(&ctx->dsi->dev, "VDDIO off load: %d\n", ret);
	}
	msleep(1);
	return 0;
}

static int aa569_power_on(struct aa569 *ctx)
{
	int ret, cleanup;

	if (ctx->vci_on || ctx->vddr_on || ctx->vddio_on)
		return -EBUSY; /* failed teardown must be retried before another enable */
	msleep(1);
	ret = regulator_set_load(ctx->vddio, 220000);
	if (ret < 0)
		return ret;
	ret = regulator_set_voltage(ctx->vddio, 1800000, 1800000);
	if (ret)
		goto fail;
	ret = regulator_enable(ctx->vddio);
	if (ret)
		goto fail;
	ctx->vddio_on = true;
	msleep(3);
	gpiod_set_value_cansleep(ctx->vddr, 1);
	ctx->vddr_on = true;
	msleep(3);
	ret = regulator_set_load(ctx->vci, 10000);
	if (ret < 0)
		goto fail;
	ret = regulator_set_voltage(ctx->vci, 3000000, 3000000);
	if (ret)
		goto fail;
	ret = regulator_enable(ctx->vci);
	if (ret)
		goto fail;
	ctx->vci_on = true;
	msleep(10);
	/* Logical 0 is physical HIGH for the required active-low reset GPIO. */
	gpiod_set_value_cansleep(ctx->reset, 0);
	msleep(10);
	gpiod_set_value_cansleep(ctx->reset, 1);
	msleep(5);
	gpiod_set_value_cansleep(ctx->reset, 0);
	msleep(30);
	return 0;
fail:
	cleanup = aa569_power_off(ctx);
	if (cleanup)
		dev_err(&ctx->dsi->dev, "power rollback failed: %d\n", cleanup);
	/* A failed enable still leaves a requested load on the disabled supply. */
	if (!ctx->vci_on)
		regulator_set_load(ctx->vci, 300);
	if (!ctx->vddio_on)
		regulator_set_load(ctx->vddio, 8000);
	return ret;
}

static int aa569_prepare(struct drm_panel *panel)
{
	struct aa569 *ctx = to_aa569(panel);
	int ret;

	mutex_lock(&ctx->lock);
	if (ctx->prepared) {
		ret = 0;
		goto out;
	}
	ret = aa569_power_on(ctx);
	if (ret)
		goto out;
	/* Last packet is display-on; defer that packet to the enable callback. */
	ret = aa569_send(ctx, aa569_on, aa569_on_bytes, ARRAY_SIZE(aa569_on) - 1, true);
	if (ret) {
		int cleanup = aa569_power_off(ctx);

		if (cleanup)
			dev_err(&ctx->dsi->dev, "prepare rollback failed: %d\n", cleanup);
		goto out;
	}
	ctx->prepared = true;
	ctx->off_sent = false;
out:
	mutex_unlock(&ctx->lock);
	return ret;
}

static int aa569_enable(struct drm_panel *panel)
{
	struct aa569 *ctx = to_aa569(panel);
	/* Fixed normal-range starting DBV1433, MSB first as in inverted stock
	 * callback. No HBM, PWM switching, low-DBV, AOD or brightness UI yet.
	 */
	static const u8 brightness[] = { 0x51, 0x05, 0x99 };
	static const struct aa569_cmd bright_cmd = {
		.length = sizeof(brightness), .type = MIPI_DSI_DCS_LONG_WRITE,
	};
	int ret;

	mutex_lock(&ctx->lock);
	if (!ctx->prepared) {
		ret = -EPERM;
		goto out;
	}
	if (ctx->enabled) {
		ret = 0;
		goto out;
	}
	ret = aa569_send(ctx, aa569_on + ARRAY_SIZE(aa569_on) - 1,
			 aa569_on_bytes, 1, true);
	if (ret)
		goto out;
	ctx->off_sent = false;
	ret = aa569_send(ctx, &bright_cmd, brightness, 1, false);
	if (ret)
		goto out;
	ctx->enabled = true;
out:
	mutex_unlock(&ctx->lock);
	return ret;
}

static int aa569_disable_locked(struct aa569 *ctx)
{
	int ret;

	if (!ctx->prepared || ctx->off_sent)
		return 0;
	/* Leave the panel awake for a possible disable/enable without unprepare. */
	ret = aa569_send(ctx, aa569_off, aa569_off_bytes, ARRAY_SIZE(aa569_off) - 1, false);
	if (!ret) {
		ctx->enabled = false;
		ctx->off_sent = true;
	}
	return ret;
}

static int aa569_disable(struct drm_panel *panel)
{
	struct aa569 *ctx = to_aa569(panel);
	int ret;

	mutex_lock(&ctx->lock);
	ret = aa569_disable_locked(ctx);
	if (ret)
		dev_warn(&ctx->dsi->dev, "display-off failed, retrying at unprepare: %d\n", ret);
	/* DRM must progress to unprepare even when the link has stopped working.
	 * off_sent remains false after an error, so teardown retries the command.
	 */
	ctx->enabled = false;
	mutex_unlock(&ctx->lock);
	return 0;
}

static int aa569_unprepare(struct drm_panel *panel)
{
	struct aa569 *ctx = to_aa569(panel);
	int ret, first = 0;

	mutex_lock(&ctx->lock);
	if (ctx->prepared && !ctx->off_sent)
		first = aa569_disable_locked(ctx);
	if (ctx->prepared) {
		ret = aa569_send(ctx, aa569_off + ARRAY_SIZE(aa569_off) - 1,
				 aa569_off_bytes, 1, false);
		if (!first)
			first = ret;
	}
	ret = aa569_power_off(ctx); /* also retries incomplete prepare rollback */
	if (!ret) {
		ctx->prepared = false;
		ctx->enabled = false;
		ctx->off_sent = true;
	}
	if (first)
		dev_warn(&ctx->dsi->dev, "off command failed before rail teardown: %d\n", first);
	mutex_unlock(&ctx->lock);
	/* Successful rail teardown is the final state even after a DSI error.
	 * Returning that old error would leave DRM's prepared state stuck true.
	 */
	return ret;
}

static const struct drm_display_mode aa569_mode = {
	.clock = 599808,
	.hdisplay = 1440, .hsync_start = 1504, .hsync_end = 1512, .htotal = 1562,
	.vdisplay = 3168, .vsync_start = 3184, .vsync_end = 3188, .vtotal = 3200,
	.width_mm = 71, .height_mm = 158,
	.type = DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED,
};

static int aa569_get_modes(struct drm_panel *panel, struct drm_connector *connector)
{
	struct drm_display_mode *mode = drm_mode_duplicate(connector->dev, &aa569_mode);

	if (!mode)
		return -ENOMEM;
	drm_mode_set_name(mode);
	drm_mode_probed_add(connector, mode);
	connector->display_info.width_mm = aa569_mode.width_mm;
	connector->display_info.height_mm = aa569_mode.height_mm;
	connector->display_info.bpc = 10;
	return 1;
}

static const struct drm_panel_funcs aa569_funcs = {
	.prepare = aa569_prepare, .enable = aa569_enable, .disable = aa569_disable,
	.unprepare = aa569_unprepare, .get_modes = aa569_get_modes,
};

static int aa569_probe(struct mipi_dsi_device *dsi)
{
	struct device *dev = &dsi->dev;
	struct aa569 *ctx;
	const struct backlight_properties props = {
		.type = BACKLIGHT_RAW, .max_brightness = 1, .brightness = 1,
	};
	int ret;

	ctx = devm_drm_panel_alloc(dev, struct aa569, panel, &aa569_funcs,
				   DRM_MODE_CONNECTOR_DSI);
	if (IS_ERR(ctx))
		return PTR_ERR(ctx);
	ctx->dsi = dsi;
	mutex_init(&ctx->lock);
	if (!of_find_property(dev->of_node, "vddio-supply", NULL) ||
	    !of_find_property(dev->of_node, "vci-supply", NULL))
		return dev_err_probe(dev, -EINVAL, "Explicit panel supplies required\n");
	ctx->vddio = devm_regulator_get(dev, "vddio");
	if (IS_ERR(ctx->vddio))
		return dev_err_probe(dev, PTR_ERR(ctx->vddio), "VDDIO supply\n");
	ctx->vci = devm_regulator_get(dev, "vci");
	if (IS_ERR(ctx->vci))
		return dev_err_probe(dev, PTR_ERR(ctx->vci), "VCI supply\n");
	ret = regulator_is_supported_voltage(ctx->vddio, 1800000, 1800000);
	if (ret <= 0)
		return dev_err_probe(dev, ret < 0 ? ret : -EINVAL, "VDDIO must support1.8V\n");
	ret = regulator_is_supported_voltage(ctx->vci, 3000000, 3000000);
	if (ret <= 0)
		return dev_err_probe(dev, ret < 0 ? ret : -EINVAL, "VCI must support3.0V\n");
	/* Voltage constraints are also requested explicitly before each power-on. */
	ctx->reset = devm_gpiod_get(dev, "reset", GPIOD_ASIS);
	if (IS_ERR(ctx->reset))
		return dev_err_probe(dev, PTR_ERR(ctx->reset), "RESET GPIO\n");
	ctx->vddr = devm_gpiod_get(dev, "vddr", GPIOD_ASIS);
	if (IS_ERR(ctx->vddr))
		return dev_err_probe(dev, PTR_ERR(ctx->vddr), "VDDR GPIO\n");
	if (!gpiod_is_active_low(ctx->reset) || gpiod_is_active_low(ctx->vddr))
		return dev_err_probe(dev, -EINVAL, "Incorrect GPIO polarity\n");
	ret = gpiod_direction_output(ctx->reset, 1);
	if (ret)
		return dev_err_probe(dev, ret, "RESET direction\n");
	ret = gpiod_direction_output(ctx->vddr, 0);
	if (ret)
		return dev_err_probe(dev, ret, "VDDR direction\n");
	ctx->panel.prepare_prev_first = true;
	ctx->dsc = aa569_dsc;
	dsi->dsc = &ctx->dsc;
	dsi->dsc_slice_per_pkt = 2;
	dsi->lanes = 4;
	dsi->format = MIPI_DSI_FMT_RGB101010;
	dsi->mode_flags = MIPI_DSI_MODE_NO_EOT_PACKET | MIPI_DSI_CLOCK_NON_CONTINUOUS;
	dsi->hs_rate = 1363200000;
	mipi_dsi_set_drvdata(dsi, ctx);
	ctx->backlight = devm_backlight_device_register(dev, "oneplus13-aa569",
				dev, ctx, &aa569_backlight_ops, &props);
	if (IS_ERR(ctx->backlight))
		return dev_err_probe(dev, PTR_ERR(ctx->backlight), "AA569 blank control\n");
	ctx->panel.backlight = ctx->backlight;
	drm_panel_add(&ctx->panel);
	ret = mipi_dsi_attach(dsi);
	if (ret)
		drm_panel_remove(&ctx->panel);
	return ret;
}

static void aa569_remove(struct mipi_dsi_device *dsi)
{
	struct aa569 *ctx = mipi_dsi_get_drvdata(dsi);
	int ret;

	ret = aa569_unprepare(&ctx->panel);
	if (ret)
		dev_err(&dsi->dev, "teardown failed: %d\n", ret);
	mipi_dsi_detach(dsi);
	drm_panel_remove(&ctx->panel);
}

static void aa569_shutdown(struct mipi_dsi_device *dsi)
{
	struct aa569 *ctx = mipi_dsi_get_drvdata(dsi);
	int ret = aa569_unprepare(&ctx->panel);

	if (ret)
		dev_err(&dsi->dev, "shutdown failed: %d\n", ret);
}

static const struct of_device_id aa569_of_match[] = {
	{ .compatible = "oneplus,dodge-aa569" }, { }
};
MODULE_DEVICE_TABLE(of, aa569_of_match);

static struct mipi_dsi_driver aa569_driver = {
	.probe = aa569_probe, .remove = aa569_remove, .shutdown = aa569_shutdown,
	.driver = { .name = "panel-oneplus-aa569", .of_match_table = aa569_of_match },
};
module_mipi_dsi_driver(aa569_driver);
MODULE_AUTHOR("Codex");
MODULE_DESCRIPTION("Experimental AA569 panel component; DPU SPR2 integration required");
MODULE_LICENSE("GPL");
