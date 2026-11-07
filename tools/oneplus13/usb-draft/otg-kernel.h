/* SPDX-License-Identifier: GPL-2.0-only */
/* Included by qcom_battmgr.c after callback and exact machine predicate. */
#include <linux/atomic.h>
#include <linux/jiffies.h>
#include <linux/ktime.h>
#include <linux/moduleparam.h>
#include <linux/pm_wakeup.h>
#include "oneplus13-otg-lease.h"

static bool oneplus13_otg_lease_enabled;
/* Rebind cannot acquire a second lease in this built-in driver. */
static atomic_t op13_otg_boot_used = ATOMIC_INIT(0);
module_param_named(oneplus13_otg_lease, oneplus13_otg_lease_enabled, bool, 0400);
MODULE_PARM_DESC(oneplus13_otg_lease,
	"Opt-in experimental AP17/SWITCH18 120-second lease; no automatic writes");

/* Caller owns battmgr->lock. No transaction ID exists. A timeout, send error,
 * firmware error or service loss forbids optional traffic, including rollback. The
 * resulting UNKNOWN state is deliberately not successful cleanup.
 */
static int op13_otg_transfer(struct qcom_battmgr *battmgr, unsigned int opcode,
			     unsigned int property, unsigned int *value)
{
	struct qcom_battmgr_property_request request = {
		.hdr.owner = cpu_to_le32(PMIC_GLINK_OWNER_BATTMGR),
		.hdr.type = cpu_to_le32(PMIC_GLINK_REQ_RESP),
		.hdr.opcode = cpu_to_le32(opcode),
		.battery = cpu_to_le32(0),
		.property = cpu_to_le32(property),
		.value = cpu_to_le32(opcode == OP13_OTG_SET ? *value : 0),
	};
	unsigned long flags;
	int ret;

	spin_lock_irqsave(&battmgr->op13_otg_lock, flags);
	if (!battmgr->service_up || battmgr->op13_otg_poisoned) {
		spin_unlock_irqrestore(&battmgr->op13_otg_lock, flags);
		return -EHOSTDOWN;
	}
	reinit_completion(&battmgr->op13_otg_ack);
	battmgr->op13_otg_error = 0;
	battmgr->op13_otg_opcode = opcode;
	battmgr->op13_otg_property = property;
	battmgr->op13_otg_pending = true;
	spin_unlock_irqrestore(&battmgr->op13_otg_lock, flags);
	ret = pmic_glink_send(battmgr->client, &request, sizeof(request));
	if (!ret && !wait_for_completion_timeout(&battmgr->op13_otg_ack, HZ))
		ret = -ETIMEDOUT;
	spin_lock_irqsave(&battmgr->op13_otg_lock, flags);
	battmgr->op13_otg_pending = false;
	if (!ret)
		ret = battmgr->op13_otg_error;
	if (ret)
		battmgr->op13_otg_poisoned = true;
	if (battmgr->op13_otg_poisoned || !battmgr->service_up)
		ret = -EHOSTDOWN;
	if (!ret)
		*value = battmgr->op13_otg_value;
	spin_unlock_irqrestore(&battmgr->op13_otg_lock, flags);
	return ret;
}

static int op13_otg_lease_get(void *context, unsigned int property,
			     unsigned int *value)
{
	return op13_otg_transfer(context, OP13_OTG_GET, property, value);
}

static int op13_otg_lease_expired(void *context)
{
	struct qcom_battmgr *battmgr = context;

	return battmgr->op13_otg_stopping ||
		ktime_get_boottime_ns() >= battmgr->op13_otg_deadline;
}

static int op13_otg_lease_set(void *context, unsigned int property,
			     unsigned int value)
{
	/* Expiry prohibits enabling; cleanup writes of the saved zero baseline
	 * remain permitted while the transport is healthy.
	 */
	if (value && op13_otg_lease_expired(context))
		return -ETIME;
	return op13_otg_transfer(context, OP13_OTG_SET, property, &value);
}

static struct op13_otg_lease_io op13_otg_io(struct qcom_battmgr *battmgr)
{
	return (struct op13_otg_lease_io) {
		.context = battmgr, .get = op13_otg_lease_get,
		.set = op13_otg_lease_set, .expired = op13_otg_lease_expired,
	};
}

static void op13_otg_awake_release(struct qcom_battmgr *battmgr)
{
	if (battmgr->op13_otg_awake) {
		__pm_relax(battmgr->op13_otg_wakeup);
		battmgr->op13_otg_awake = false;
	}
}

static void op13_otg_restore_worker(struct work_struct *work)
{
	struct qcom_battmgr *battmgr = container_of(to_delayed_work(work),
		struct qcom_battmgr, op13_otg_restore_work);
	struct op13_otg_lease_io io = op13_otg_io(battmgr);

	mutex_lock(&battmgr->lock);
	if (battmgr->op13_otg_lease.state != OP13_OTG_LEASE_UNKNOWN)
		op13_otg_lease_restore(&battmgr->op13_otg_lease, &io);
	op13_otg_awake_release(battmgr);
	mutex_unlock(&battmgr->lock);
}

/* Registered after GLINK allocation and before sysfs group: devres removes
 * sysfs first, then drains the watchdog/restores, then frees the client.
 * Never wait for work while holding the mutex required by that work.
 */
static void op13_otg_lease_remove(void *data)
{
	struct qcom_battmgr *battmgr = data;
	struct op13_otg_lease_io io = op13_otg_io(battmgr);

	mutex_lock(&battmgr->lock);
	spin_lock_irq(&battmgr->op13_otg_lock);
	battmgr->op13_otg_stopping = true;
	spin_unlock_irq(&battmgr->op13_otg_lock);
	mutex_unlock(&battmgr->lock);
	cancel_delayed_work_sync(&battmgr->op13_otg_restore_work);
	cancel_work_sync(&battmgr->enable_work);
	mutex_lock(&battmgr->lock);
	if (battmgr->op13_otg_lease.state != OP13_OTG_LEASE_UNKNOWN)
		op13_otg_lease_restore(&battmgr->op13_otg_lease, &io);
	op13_otg_awake_release(battmgr);
	mutex_unlock(&battmgr->lock);
	if (battmgr->op13_otg_wakeup)
		wakeup_source_unregister(battmgr->op13_otg_wakeup);
}

static ssize_t oneplus13_otg_lease_show(struct device *dev,
				      struct device_attribute *attr, char *buf)
{
	struct qcom_battmgr *battmgr = dev_get_drvdata(dev);
	unsigned int state, used, ap, sw;

	mutex_lock(&battmgr->lock);
	state = battmgr->op13_otg_lease.state;
	used = battmgr->op13_otg_lease.used;
	ap = battmgr->op13_otg_lease.dirty_ap;
	sw = battmgr->op13_otg_lease.dirty_switch;
	mutex_unlock(&battmgr->lock);
	return sysfs_emit(buf, "state=%u used=%u dirty_ap=%u dirty_switch=%u\n",
			  state, used, ap, sw);
}

static ssize_t oneplus13_otg_lease_store(struct device *dev,
		struct device_attribute *attr, const char *buf, size_t count)
{
	struct qcom_battmgr *battmgr = dev_get_drvdata(dev);
	struct op13_otg_lease_io io = op13_otg_io(battmgr);
	int ret = -EINVAL;

#ifdef MODULE
	/* Target is built-in. A loadable driver cannot retain a boot-wide latch. */
	return -EOPNOTSUPP;
#endif
	if (!oneplus13_otg_lease_enabled || !op13_otg_machine())
		return -EOPNOTSUPP;
	if (!sysfs_streq(buf, "start") && !sysfs_streq(buf, "restore"))
		return -EINVAL;
	mutex_lock(&battmgr->lock);
	if (battmgr->op13_otg_stopping)
		goto out;
	if (sysfs_streq(buf, "start")) {
		if (!battmgr->op13_otg_wakeup || battmgr->op13_otg_lease.used ||
		    atomic_cmpxchg(&op13_otg_boot_used, 0, 1)) {
			ret = -EBUSY;
			goto out;
		}
		battmgr->op13_otg_deadline = ktime_get_boottime_ns() +
			(u64)OP13_OTG_LEASE_SECONDS * NSEC_PER_SEC;
		__pm_stay_awake(battmgr->op13_otg_wakeup);
		battmgr->op13_otg_awake = true;
		if (!schedule_delayed_work(&battmgr->op13_otg_restore_work,
					   OP13_OTG_LEASE_SECONDS * HZ)) {
			op13_otg_awake_release(battmgr);
			goto out;
		}
		ret = op13_otg_lease_start(&battmgr->op13_otg_lease, &io);
		if (battmgr->op13_otg_lease.state != OP13_OTG_LEASE_ACTIVE)
			op13_otg_awake_release(battmgr);
	} else {
		/* UNKNOWN may include a lost ACK; never retry that tuple. */
		ret = battmgr->op13_otg_lease.state == OP13_OTG_LEASE_UNKNOWN ?
			-EIO : op13_otg_lease_restore(&battmgr->op13_otg_lease, &io);
		op13_otg_awake_release(battmgr);
	}
out:
	mutex_unlock(&battmgr->lock);
	if (ret)
		return ret;
	return count;
}

static struct device_attribute dev_attr_oneplus13_otg_lease =
	__ATTR(oneplus13_otg_lease, 0600, oneplus13_otg_lease_show,
	       oneplus13_otg_lease_store);
