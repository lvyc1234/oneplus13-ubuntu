// SPDX-License-Identifier: GPL-2.0-only
/* OnePlus 13 SPI infrared candidate using the standard rc-core/LIRC API.
 * Board wiring and 5 MHz encoding derived from the pinned public Oplus GPL
 * driver. No device activation is added by this source file.
 */
#include <linux/bitops.h>
#include <linux/err.h>
#include <linux/kernel.h>
#include <linux/limits.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>
#include <linux/spi/spi.h>
#include <media/rc-core.h>

#define DODGE_IR_SPI_HZ 5000000U
#define DODGE_IR_UV 3056000
#define DODGE_IR_MAX_US 500000U
#define DODGE_IR_MAX_EDGES 1024U

struct dodge_ir {
	struct spi_device *spi;
	struct regulator *supply;
	struct mutex lock;
	u32 carrier, duty;
	bool supply_enabled;
};

static int dodge_ir_carrier(struct rc_dev *rc, u32 carrier)
{
	struct dodge_ir *ir = rc->priv;

	if (carrier < 20000 || carrier > 60000)
		return -EINVAL;
	mutex_lock(&ir->lock);
	ir->carrier = carrier;
	mutex_unlock(&ir->lock);
	return 0;
}

static int dodge_ir_duty(struct rc_dev *rc, u32 duty)
{
	struct dodge_ir *ir = rc->priv;

	/* The stock transmitter uses 1/3 duty. Do not permit continuous-on. */
	if (!duty || duty > 50)
		return -EINVAL;
	mutex_lock(&ir->lock);
	ir->duty = duty;
	mutex_unlock(&ir->lock);
	return 0;
}

static int dodge_ir_encode(struct dodge_ir *ir, const unsigned int *durations,
			   unsigned int count, u8 **output, size_t *length)
{
	u64 total = 0, elapsed = 0, bit = 0, end;
	u32 phase, threshold;
	unsigned int i;
	u8 *bytes;

	if (!count || count > DODGE_IR_MAX_EDGES)
		return -EINVAL;
	for (i = 0; i < count; i++) {
		if (!durations[i])
			return -EINVAL;
		total += durations[i];
		if (total > DODGE_IR_MAX_US)
			return -EMSGSIZE;
	}
	/* At 5 MHz each microsecond is exactly five samples; append idle bits. */
	*length = DIV_ROUND_UP_ULL(total * 5, 8) + 1;
	if (*length > spi_max_transfer_size(ir->spi) ||
	    *length > spi_max_message_size(ir->spi))
		return -EMSGSIZE;
	bytes = kzalloc(*length, GFP_KERNEL);
	if (!bytes)
		return -ENOMEM;
	threshold = ir->duty ? DODGE_IR_SPI_HZ * ir->duty / 100 :
		DODGE_IR_SPI_HZ / 3;
	for (i = 0; i < count; i++) {
		elapsed += durations[i];
		end = elapsed * 5;
		phase = 0;
		if (i & 1) {
			bit = end;
			continue;
		}
		for (; bit < end; bit++) {
			if (phase < threshold)
				bytes[bit / 8] |= BIT(7 - bit % 8);
			phase += ir->carrier;
			if (phase >= DODGE_IR_SPI_HZ)
				phase -= DODGE_IR_SPI_HZ;
		}
	}
	*output = bytes;
	return 0;
}

static int dodge_ir_tx(struct rc_dev *rc, unsigned int *durations,
		       unsigned int count)
{
	struct dodge_ir *ir = rc->priv;
	struct spi_transfer transfer = { .speed_hz = DODGE_IR_SPI_HZ,
		.bits_per_word = 8 };
	u8 *bytes = NULL;
	size_t length;
	int ret, cleanup;

	mutex_lock(&ir->lock);
	if (ir->supply_enabled) {
		ret = -EBUSY;
		goto unlock;
	}
	ret = dodge_ir_encode(ir, durations, count, &bytes, &length);
	if (ret)
		goto unlock;
	/* Shared-rail constraints must accept this vote; no constraint widening. */
	ret = regulator_set_voltage(ir->supply, DODGE_IR_UV, DODGE_IR_UV);
	if (ret)
		goto free;
	ret = regulator_enable(ir->supply);
	if (ret)
		goto release_vote;
	ir->supply_enabled = true;
	ret = regulator_get_voltage(ir->supply);
	if (ret != DODGE_IR_UV) {
		ret = ret < 0 ? ret : -ERANGE;
		goto power_off;
	}
	transfer.tx_buf = bytes;
	transfer.len = length;
	ret = spi_sync_transfer(ir->spi, &transfer, 1);
	/* A reported different sample rate invalidates the generated waveform. */
	if (!ret && transfer.effective_speed_hz &&
	    transfer.effective_speed_hz != DODGE_IR_SPI_HZ)
		ret = -ERANGE;

power_off:
	cleanup = regulator_disable(ir->supply);
	if (cleanup) {
		if (!ret)
			ret = cleanup;
		/* Retain the vote and refuse further transmission after failed off. */
		goto free;
	}
	ir->supply_enabled = false;
release_vote:
	cleanup = regulator_set_voltage(ir->supply, 0, INT_MAX);
	if (!ret && cleanup)
		ret = cleanup;
free:
	kfree(bytes);
unlock:
	mutex_unlock(&ir->lock);
	return ret ?: count;
}

static void dodge_ir_release(void *data)
{
	struct dodge_ir *ir = data;
	int ret;

	/* rc-core is unregistered before this devres action, so no TX can race. */
	if (ir->supply_enabled) {
		ret = regulator_disable(ir->supply);
		if (ret) {
			dev_err(&ir->spi->dev, "IR supply could not be disabled: %d\n", ret);
			return;
		}
		ir->supply_enabled = false;
	}
	regulator_set_voltage(ir->supply, 0, INT_MAX);
}

static int dodge_ir_probe(struct spi_device *spi)
{
	struct device *dev = &spi->dev;
	struct dodge_ir *ir;
	struct rc_dev *rc;
	int ret;

	if (spi->max_speed_hz != DODGE_IR_SPI_HZ || spi->mode != SPI_MODE_0 ||
	    !of_find_property(dev->of_node, "irda_regulator-supply", NULL))
		return dev_err_probe(dev, -EINVAL, "Exact SPI and supply mapping required\n");
	ir = devm_kzalloc(dev, sizeof(*ir), GFP_KERNEL);
	if (!ir)
		return -ENOMEM;
	mutex_init(&ir->lock);
	ir->spi = spi;
	ir->carrier = 38000;
	/* duty=0 means the source-derived exact one-third default. */
	ir->supply = devm_regulator_get(dev, "irda_regulator");
	if (IS_ERR(ir->supply))
		return PTR_ERR(ir->supply);
	ret = regulator_is_supported_voltage(ir->supply, DODGE_IR_UV, DODGE_IR_UV);
	if (ret <= 0)
		return dev_err_probe(dev, ret ?: -ERANGE, "3.056 V supply constraint unavailable\n");
	ret = devm_add_action_or_reset(dev, dodge_ir_release, ir);
	if (ret)
		return ret;
	rc = devm_rc_allocate_device(dev, RC_DRIVER_IR_RAW_TX);
	if (!rc)
		return -ENOMEM;
	rc->priv = ir;
	rc->device_name = "OnePlus 13 experimental SPI infrared";
	rc->driver_name = "ir-dodge-spi";
	rc->tx_ir = dodge_ir_tx;
	rc->s_tx_carrier = dodge_ir_carrier;
	rc->s_tx_duty_cycle = dodge_ir_duty;
	return devm_rc_register_device(dev, rc);
}

static const struct of_device_id dodge_ir_match[] = {
	{ .compatible = "oneplus,dodge-ir-experimental" },
	{}
};
MODULE_DEVICE_TABLE(of, dodge_ir_match);

static struct spi_driver dodge_ir_driver = {
	.probe = dodge_ir_probe,
	.driver = { .name = "ir-dodge-spi", .of_match_table = dodge_ir_match },
};
module_spi_driver(dodge_ir_driver);
MODULE_DESCRIPTION("OnePlus 13 bounded SPI infrared candidate");
MODULE_AUTHOR("Codex");
MODULE_LICENSE("GPL");
