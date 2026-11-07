// SPDX-License-Identifier: GPL-2.0-only
/*
 * OnePlus 13 S3910 LBP candidate, not hardware validated.
 * Wire protocol: Synaptics TouchCom core (copyright retained in its files).
 * Board timings/pins: owner-authorized stock reads and OnePlusOSS d50b305f.
 * No flash, firmware update, raw-userspace SPI or factory-test interface.
 */
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/input.h>
#include <linux/input/mt.h>
#include <linux/input/touchscreen.h>
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/regulator/consumer.h>
#include <linux/spi/spi.h>
#include <linux/workqueue.h>
#include <drm/drm_panel.h>
#include "synaptics_touchcom_func_base.h"
#include "s3910-report.h"

struct dodge_touch {
	struct syna_hw_interface hw;
	struct spi_device *spi;
	struct tcm_dev *core;
	struct tcm_buffer event;
	struct input_dev *input;
	struct touchscreen_properties props;
	struct regulator *vdd;
	struct gpio_desc *avdd, *reset;
	unsigned char *read_dummy;
	struct mutex state_lock, io_lock;
	struct work_struct start_work;
	struct drm_panel_follower follower;
	bool enabled, powered, irq_enabled, registered, follows_panel, ready;
	unsigned int max_x, max_y, count;
};

static int dodge_read(struct syna_hw_interface *hw, unsigned char *data, unsigned int len)
{
	struct dodge_touch *t = container_of(hw, struct dodge_touch, hw);
	struct spi_transfer xfer = { .rx_buf = data, .len = len };
	struct spi_message message;
	int ret;
	if (!len || len > spi_max_transfer_size(t->spi) || len > 65536)
		return -EMSGSIZE;
	/* Vendor transfers drive MOSI high during reads; RX-only sends zeros. */
	xfer.tx_buf = t->read_dummy;
	spi_message_init(&message);
	spi_message_add_tail(&xfer, &message);
	ret = spi_sync(t->spi, &message);
	if (ret < 0)
		dev_err(&t->spi->dev, "read spi_sync %d (len %u)\n", ret, len);
	/* spi-geni-qcom on this kernel never sets actual_length; response
	 * integrity is validated by the protocol core, not by length. */
	return ret ?: len;
}

static int dodge_write(struct syna_hw_interface *hw, unsigned char *data, unsigned int len)
{
	struct dodge_touch *t = container_of(hw, struct dodge_touch, hw);
	struct spi_transfer xfer = { .tx_buf = data, .len = len };
	struct spi_message message;
	int ret;
	if (!len || len > spi_max_transfer_size(t->spi) || len > 65536)
		return -EMSGSIZE;
	spi_message_init(&message);
	spi_message_add_tail(&xfer, &message);
	ret = spi_sync(t->spi, &message);
	if (ret < 0)
		dev_err(&t->spi->dev, "write spi_sync %d (len %u)\n", ret, len);
	/* spi-geni-qcom on this kernel never sets actual_length; response
	 * integrity is validated by the protocol core, not by length. */
	return ret ?: len;
}

/* All command traffic runs with the IRQ masked, outside its thread. */
static int dodge_command(struct dodge_touch *t, unsigned char cmd,
			 unsigned char *payload, unsigned int len)
{
	unsigned char status = STATUS_INVALID;
	int ret = t->core->write_message(t->core, cmd, payload, len,
					 &status, RESP_IN_POLLING);
	return ret < 0 ? ret : status == STATUS_OK ? 0 : -EPROTO;
}

static int dodge_configure(struct dodge_touch *t)
{
	unsigned int size;
	unsigned char select_lbp[] = { DC_CONTROL_LBP_HBP, 2, 0 };
	unsigned char report = REPORT_TOUCH;
	unsigned char *config;
	int ret;
	ret = dodge_command(t, CMD_SET_DYNAMIC_CONFIG, select_lbp, sizeof(select_lbp));
	if (ret)
		return ret;
	size = syna_pal_le2_to_uint(t->core->app_info.max_touch_report_config_size);
	if (size < sizeof(s3910_descriptor) || size > 4096)
		return -EPROTO;
	config = kzalloc(size, GFP_KERNEL);
	if (!config)
		return -ENOMEM;
	memcpy(config, s3910_descriptor, sizeof(s3910_descriptor));
	ret = dodge_command(t, CMD_SET_TOUCH_REPORT_CONFIG, config, size);
	kfree(config);
	if (ret)
		return ret;
	ret = dodge_command(t, CMD_GET_TOUCH_REPORT_CONFIG, NULL, 0);
	if (ret)
		return ret;
	if (t->core->resp_buf.data_length < sizeof(s3910_descriptor) ||
	    memcmp(t->core->resp_buf.buf, s3910_descriptor, sizeof(s3910_descriptor)))
		return -EPROTO;
	/* Vendor HBP mode is not used. Stop raw-frame reports explicitly. */
	report = REPORT_HBP_ACTIVE_FRAME;
	ret = dodge_command(t, CMD_DISABLE_REPORT, &report, 1);
	if (ret)
		return ret;
	/* LBP selection immediately precedes standard report enable. */
	ret = dodge_command(t, CMD_SET_DYNAMIC_CONFIG, select_lbp, sizeof(select_lbp));
	if (ret)
		return ret;
	report = REPORT_TOUCH;
	return dodge_command(t, CMD_ENABLE_REPORT, &report, 1);
}

static void dodge_release_touches(struct dodge_touch *t)
{
	unsigned int i;
	if (!t->registered)
		return;
	for (i = 0; i < t->count; i++) {
		input_mt_slot(t->input, i);
		input_mt_report_slot_state(t->input, MT_TOOL_FINGER, false);
	}
	input_mt_sync_frame(t->input);
	input_sync(t->input);
}

static irqreturn_t dodge_irq(int irq, void *data)
{
	struct dodge_touch *t = data;
	struct s3910_point points[10];
	unsigned char code = 0;
	unsigned int i;
	int ret;
	mutex_lock(&t->io_lock);
	ret = syna_tcm_get_event_data(t->core, &code, &t->event);
	if (ret < 0)
		goto out;
	if (code == REPORT_IDENTIFY) {
		t->ready = false;
		dodge_release_touches(t);
		if (READ_ONCE(t->enabled))
			schedule_work(&t->start_work);
		goto out;
	}
	if (code != REPORT_TOUCH || !t->ready)
		goto out;
	ret = s3910_decode(t->event.buf, t->event.data_length, t->count,
			    t->max_x, t->max_y, points);
	if (ret) {
		dev_warn_ratelimited(&t->spi->dev, "invalid LBP touch frame\n");
		goto out;
	}
	for (i = 0; i < t->count; i++) {
		/* Palm, hover and stylus classifications are not fingers. */
		bool active = points[i].status == 1 || points[i].status == 2;
		input_mt_slot(t->input, i);
		input_mt_report_slot_state(t->input, MT_TOOL_FINGER, active);
		if (active) {
			touchscreen_report_pos(t->input, &t->props, points[i].x, points[i].y, true);
			input_report_abs(t->input, ABS_MT_TOUCH_MAJOR, points[i].major);
			input_report_abs(t->input, ABS_MT_TOUCH_MINOR, points[i].minor);
		}
	}
	input_mt_sync_frame(t->input);
	input_sync(t->input);
out:
	mutex_unlock(&t->io_lock);
	return IRQ_HANDLED;
}

static int dodge_input_setup(struct dodge_touch *t)
{
	int ret;
	if (!t->core->max_objects || t->core->max_objects > 10 ||
	    !t->core->max_x || !t->core->max_y ||
	    t->core->max_x > 65535 || t->core->max_y > 65535)
		return -EPROTO;
	if (t->registered)
		return t->count == t->core->max_objects && t->max_x == t->core->max_x &&
			t->max_y == t->core->max_y ? 0 : -EPROTO;
	t->count = t->core->max_objects;
	t->max_x = t->core->max_x;
	t->max_y = t->core->max_y;
	t->input->name = "OnePlus 13 Synaptics S3910 LBP";
	t->input->id.bustype = BUS_SPI;
	t->input->dev.parent = &t->spi->dev;
	input_set_abs_params(t->input, ABS_MT_POSITION_X, 0, t->max_x, 0, 0);
	input_set_abs_params(t->input, ABS_MT_POSITION_Y, 0, t->max_y, 0, 0);
	input_set_abs_params(t->input, ABS_MT_TOUCH_MAJOR, 0, 255, 0, 0);
	input_set_abs_params(t->input, ABS_MT_TOUCH_MINOR, 0, 255, 0, 0);
	touchscreen_parse_properties(t->input, true, &t->props);
	ret = input_mt_init_slots(t->input, t->count, INPUT_MT_DIRECT | INPUT_MT_DROP_UNUSED);
	if (ret)
		return ret;
	ret = input_register_device(t->input);
	if (!ret)
		t->registered = true;
	return ret;
}

static void dodge_power_off(struct dodge_touch *t)
{
	if (!t->powered)
		return;
	gpiod_set_value_cansleep(t->reset, 1);
	regulator_disable(t->vdd);
	gpiod_set_value_cansleep(t->avdd, 0);
	t->powered = false;
}

static void dodge_start_work(struct work_struct *work)
{
	struct dodge_touch *t = container_of(work, struct dodge_touch, start_work);
	int ret = 0;
	mutex_lock(&t->state_lock);
	if (!READ_ONCE(t->enabled))
		goto out_state;
	if (t->irq_enabled) {
		disable_irq(t->spi->irq);
		t->irq_enabled = false;
	}
	mutex_lock(&t->io_lock);
	t->ready = false;
	if (!t->powered) {
		/* Existing vendor driver uses physical HIGH, despite its legacy
		 * GPIO flags. The candidate binding uses GPIO_ACTIVE_HIGH. */
		gpiod_set_value_cansleep(t->avdd, 1);
		ret = regulator_enable(t->vdd);
		if (ret) {
			gpiod_set_value_cansleep(t->avdd, 0);
			goto out_io;
		}
		t->powered = true;
		msleep(200); /* stock power-on settling requirement */
		gpiod_set_value_cansleep(t->reset, 1);
		msleep(10);
		gpiod_set_value_cansleep(t->reset, 0);
		msleep(80); /* stock reset recovery requirement */
	}
	ret = syna_tcm_detect_device(t->core);
	if (ret != MODE_APPLICATION_FIRMWARE) {
		ret = ret < 0 ? ret : -ENODEV;
		goto fail_power;
	}
	ret = syna_tcm_get_app_info(t->core, &t->core->app_info);
	if (ret < 0)
		goto fail_power;
	ret = dodge_configure(t);
	if (ret)
		goto fail_power;
	ret = dodge_input_setup(t);
	if (ret)
		goto fail_power;
	t->ready = true;
	goto out_io;
fail_power:
	dodge_release_touches(t);
	dodge_power_off(t);
out_io:
	mutex_unlock(&t->io_lock);
	if (!ret && READ_ONCE(t->enabled)) {
		enable_irq(t->spi->irq);
		t->irq_enabled = true;
	} else if (ret) {
		dev_err(&t->spi->dev, "LBP startup failed: %d; no automatic retry\n", ret);
	}
out_state:
	mutex_unlock(&t->state_lock);
}

static void dodge_pause(struct dodge_touch *t)
{
	WRITE_ONCE(t->enabled, false);
	mutex_lock(&t->state_lock);
	if (t->irq_enabled) {
		disable_irq(t->spi->irq);
		t->irq_enabled = false;
	}
	mutex_unlock(&t->state_lock);
	/* IRQ is now quiescent. Work cannot re-enable it after enabled=false. */
	cancel_work_sync(&t->start_work);
	mutex_lock(&t->state_lock);
	mutex_lock(&t->io_lock);
	t->ready = false;
	dodge_release_touches(t);
	dodge_power_off(t);
	mutex_unlock(&t->io_lock);
	mutex_unlock(&t->state_lock);
}

static int dodge_panel_enabled(struct drm_panel_follower *follower)
{
	struct dodge_touch *t = container_of(follower, struct dodge_touch, follower);
	WRITE_ONCE(t->enabled, true);
	schedule_work(&t->start_work); /* never wait for touch traffic in the panel callback */
	return 0;
}

static int dodge_panel_disabling(struct drm_panel_follower *follower)
{
	dodge_pause(container_of(follower, struct dodge_touch, follower));
	return 0;
}

static const struct drm_panel_follower_funcs dodge_follower_funcs = {
	.panel_enabled = dodge_panel_enabled,
	.panel_disabling = dodge_panel_disabling,
};

static void dodge_cleanup(void *data)
{
	struct dodge_touch *t = data;
	dodge_pause(t);
	syna_tcm_remove_device(t->core);
	syna_tcm_buf_release(&t->event);
	mutex_destroy(&t->hw.bdata_io.io_mutex);
}

static int dodge_probe(struct spi_device *spi)
{
	struct device *dev = &spi->dev;
	struct dodge_touch *t;
	int ret;
	if (spi->irq <= 0 || !spi->max_speed_hz || spi->max_speed_hz > 19000000)
		return -EINVAL;
	t = devm_kzalloc(dev, sizeof(*t), GFP_KERNEL);
	if (!t)
		return -ENOMEM;
	t->spi = spi;
	mutex_init(&t->state_lock);
	mutex_init(&t->io_lock);
	INIT_WORK(&t->start_work, dodge_start_work);
	t->input = devm_input_allocate_device(dev);
	if (!t->input)
		return -ENOMEM;
	t->read_dummy = devm_kmalloc(dev, 65536, GFP_KERNEL);
	if (!t->read_dummy)
		return -ENOMEM;
	memset(t->read_dummy, 0xff, 65536);
	t->vdd = devm_regulator_get(dev, "vdd");
	if (IS_ERR(t->vdd))
		return dev_err_probe(dev, PTR_ERR(t->vdd), "missing touch VDD\n");
	t->reset = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(t->reset))
		return dev_err_probe(dev, PTR_ERR(t->reset), "missing touch reset\n");
	t->avdd = devm_gpiod_get(dev, "avdd", GPIOD_OUT_LOW);
	if (IS_ERR(t->avdd))
		return dev_err_probe(dev, PTR_ERR(t->avdd), "missing touch AVDD enable\n");
	spi->mode = SPI_MODE_0;
	spi->bits_per_word = 8;
	ret = spi_setup(spi);
	if (ret)
		return ret;
	t->hw.pdev = spi;
	t->hw.bdata_io.type = 1;
	t->hw.bdata_io.frequency_hz = spi->max_speed_hz;
	t->hw.bdata_io.rd_chunk_size = min_t(size_t, 65536, spi_max_transfer_size(spi));
	t->hw.bdata_io.wr_chunk_size = t->hw.bdata_io.rd_chunk_size;
	mutex_init(&t->hw.bdata_io.io_mutex);
	t->hw.ops_read_data = dodge_read;
	t->hw.ops_write_data = dodge_write;
	/* The outer state machine masks IRQs. The protocol core must not mask
	 * its own interrupt thread or race input/open against command responses. */
	t->hw.ops_enable_irq = NULL;
	syna_tcm_buf_init(&t->event);
	ret = syna_tcm_allocate_device(&t->core, &t->hw, RESP_IN_POLLING);
	if (ret < 0) {
		syna_tcm_buf_release(&t->event);
		mutex_destroy(&t->hw.bdata_io.io_mutex);
		return ret;
	}
	ret = devm_request_threaded_irq(dev, spi->irq, NULL, dodge_irq,
					 IRQF_ONESHOT | IRQF_NO_AUTOEN, dev_name(dev), t);
	if (ret) {
		syna_tcm_remove_device(t->core);
		syna_tcm_buf_release(&t->event);
		mutex_destroy(&t->hw.bdata_io.io_mutex);
		return ret;
	}
	ret = devm_add_action_or_reset(dev, dodge_cleanup, t);
	if (ret)
		return ret;
	spi_set_drvdata(spi, t);
	t->follows_panel = drm_is_panel_follower(dev);
	if (t->follows_panel) {
		t->follower.funcs = &dodge_follower_funcs;
		return devm_drm_panel_add_follower(dev, &t->follower);
	}
	WRITE_ONCE(t->enabled, true);
	schedule_work(&t->start_work);
	return 0;
}

static void dodge_shutdown(struct spi_device *spi)
{
	dodge_pause(spi_get_drvdata(spi));
}

static const struct of_device_id dodge_match[] = {
	{ .compatible = "oneplus,dodge-s3910-lbp" }, { }
};
MODULE_DEVICE_TABLE(of, dodge_match);

static struct spi_driver dodge_driver = {
	.driver = { .name = "dodge-s3910", .of_match_table = dodge_match },
	.probe = dodge_probe,
	.shutdown = dodge_shutdown,
};
module_spi_driver(dodge_driver);
MODULE_AUTHOR("Codex");
MODULE_DESCRIPTION("Experimental OnePlus 13 S3910 LBP touchscreen");
MODULE_LICENSE("GPL");
