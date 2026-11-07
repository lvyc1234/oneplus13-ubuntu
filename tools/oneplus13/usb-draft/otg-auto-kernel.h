/* SPDX-License-Identifier: GPL-2.0-only */
/* Included after the checked supply callbacks. Caller owns battmgr->lock. */
#ifndef ONEPLUS13_OTG_AUTO_KERNEL_H
#define ONEPLUS13_OTG_AUTO_KERNEL_H

static bool op13_otg_auto_watchdog_early(struct qcom_battmgr *b)
{
	u64 now = ktime_get_boottime_ns();

	if (!oneplus13_otg_auto || b->op13_otg_stopping ||
	    now >= b->op13_otg_supply_deadline)
		return false;
	mod_delayed_work(system_freezable_wq, &b->op13_otg_supply_restore_work,
			 nsecs_to_jiffies(b->op13_otg_supply_deadline - now) + 1);
	return true;
}

static int op13_otg_auto_expired(void *context)
{
	struct qcom_battmgr *b = context;

	return op13_otg_supply_expired(b) ||
		(READ_ONCE(b->op13_otg_auto_starting) &&
		 (!READ_ONCE(b->op13_otg_auto_source) ||
		  READ_ONCE(b->op13_otg_auto_generation) !=
		  READ_ONCE(b->op13_otg_auto_start_generation)));
}

static struct op13_otg_supply_io op13_otg_auto_io(struct qcom_battmgr *b)
{
	struct op13_otg_supply_io io = op13_otg_supply_io(b);

	io.expired = op13_otg_auto_expired;
	return io;
}

static bool op13_otg_auto_watchdog_worker(struct qcom_battmgr *b)
{
	struct op13_otg_supply_io io = op13_otg_auto_io(b);

	if (!oneplus13_otg_auto)
		return false;
	mutex_lock(&b->lock);
	if (!op13_otg_auto_watchdog_early(b)) {
		if (b->op13_otg_supply_lease.state == OP13_OTG_SUPPLY_ARMED ||
		    b->op13_otg_supply_lease.state == OP13_OTG_SUPPLY_ACTIVE)
			op13_otg_supply_restore(&b->op13_otg_supply_lease, &io);
		op13_otg_awake_release(b);
	}
	mutex_unlock(&b->lock);
	return true;
}

static void op13_otg_auto_detach_worker(struct work_struct *work)
{
	struct qcom_battmgr *b = container_of(work, struct qcom_battmgr,
					   op13_otg_auto_work);
	struct op13_otg_supply_io io = op13_otg_auto_io(b);
	unsigned long flags;
	bool restore;

	mutex_lock(&b->lock);
	spin_lock_irqsave(&b->op13_otg_lock, flags);
	restore = b->op13_otg_auto_detach_pending;
	b->op13_otg_auto_detach_pending = false;
	spin_unlock_irqrestore(&b->op13_otg_lock, flags);
	if (restore && (b->op13_otg_supply_lease.state == OP13_OTG_SUPPLY_ACTIVE ||
			b->op13_otg_supply_lease.state == OP13_OTG_SUPPLY_ARMED)) {
		op13_otg_supply_restore(&b->op13_otg_supply_lease, &io);
		op13_otg_awake_release(b);
	}
	mutex_unlock(&b->lock);
}

static bool op13_otg_auto_notification(struct qcom_battmgr *b,
				      const void *data, size_t len)
{
	unsigned long flags;
	unsigned int source;

	if (!oneplus13_otg_auto || !op13_otg_machine() ||
	    !op13_otg_auto_decode(data, len, &source))
		return false;
	spin_lock_irqsave(&b->op13_otg_lock, flags);
	if (!b->service_up || b->op13_otg_stopping || b->op13_otg_poisoned) {
		spin_unlock_irqrestore(&b->op13_otg_lock, flags);
		return true;
	}
	b->op13_otg_auto_generation++;
	if (!b->op13_otg_auto_generation)
		b->op13_otg_poisoned = true;
	b->op13_otg_auto_source = source;
	if (!source)
		b->op13_otg_auto_detach_pending = true;
	spin_unlock_irqrestore(&b->op13_otg_lock, flags);
	if (!source)
		queue_work(system_freezable_wq, &b->op13_otg_auto_work);
	return true;
}

static void op13_otg_auto_remove(void *data)
{
	struct qcom_battmgr *b = data;
	unsigned long flags;

	spin_lock_irqsave(&b->op13_otg_lock, flags);
	b->op13_otg_stopping = true;
	spin_unlock_irqrestore(&b->op13_otg_lock, flags);
	cancel_work_sync(&b->op13_otg_auto_work);
	/* The registered supply remover drains its watchdog and restores values. */
}

static ssize_t oneplus13_otg_auto_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	struct qcom_battmgr *b = dev_get_drvdata(dev);
	struct op13_otg_supply_lease l;
	unsigned int generation, source;
	unsigned long flags;
	int healthy;

	(void)attr;
	mutex_lock(&b->lock);
	l = b->op13_otg_supply_lease;
	healthy = op13_otg_supply_transport_healthy(b) &&
		READ_ONCE(b->op13_otg_auto_notifications_ready);
	spin_lock_irqsave(&b->op13_otg_lock, flags);
	generation = b->op13_otg_auto_generation;
	source = b->op13_otg_auto_source;
	spin_unlock_irqrestore(&b->op13_otg_lock, flags);
	mutex_unlock(&b->lock);
	return sysfs_emit(buf,
		"state=%u generation=%u source=%u healthy=%u saved_ap=%u saved_switch=%u saved_vbus=%u saved_current=%u dirty_ap=%u dirty_switch=%u dirty_vbus=%u dirty_current=%u\n",
		l.state, generation, source, healthy, l.saved_ap, l.saved_switch,
		l.saved_vbus, l.saved_current, l.dirty_ap, l.dirty_switch,
		l.dirty_vbus, l.dirty_current);
}

static ssize_t oneplus13_otg_auto_store(struct device *dev,
		struct device_attribute *attr, const char *buf, size_t count)
{
	struct qcom_battmgr *b = dev_get_drvdata(dev);
	struct op13_otg_supply_io io = op13_otg_auto_io(b);
	unsigned long flags;
	unsigned int generation = 0;
	bool on = !strncmp(buf, "on ", 3);
	int ret = -EINVAL;

	(void)attr;
#ifdef MODULE
	return -EOPNOTSUPP;
#endif
	if (!oneplus13_otg_auto || !op13_otg_machine() ||
	    oneplus13_otg_supply_lease || oneplus13_otg_lease_enabled)
		return -EOPNOTSUPP;
	if (on && kstrtouint(buf + 3, 10, &generation))
		return -EINVAL;
	if (!on && !sysfs_streq(buf, "arm") && !sysfs_streq(buf, "keepalive") &&
	    !sysfs_streq(buf, "off") && !sysfs_streq(buf, "close"))
		return -EINVAL;
	mutex_lock(&b->lock);
	if (b->op13_otg_stopping || !io.transport_healthy(b) ||
	    !READ_ONCE(b->op13_otg_auto_notifications_ready)) {
		ret = -EHOSTDOWN;
		goto out;
	}
	if (sysfs_streq(buf, "arm")) {
		/* Auto and manual modes never share an ownership cycle. */
		if (!b->op13_otg_auto_started) {
			if (atomic_cmpxchg(&op13_otg_boot_used, 0, 1)) {
				ret = -EBUSY;
				goto out;
			}
			b->op13_otg_auto_started = true;
		}
		if (b->op13_otg_supply_lease.state != OP13_OTG_SUPPLY_IDLE &&
		    b->op13_otg_supply_lease.state != OP13_OTG_SUPPLY_RESTORED)
			goto out;
		b->op13_otg_supply_deadline = ktime_get_boottime_ns() +
			(u64)OP13_OTG_AUTO_WATCHDOG_SECONDS * NSEC_PER_SEC;
		mod_delayed_work(system_freezable_wq,
			&b->op13_otg_supply_restore_work,
			OP13_OTG_AUTO_WATCHDOG_SECONDS * HZ);
		ret = op13_otg_auto_arm(&b->op13_otg_supply_lease, &io);
	} else if (sysfs_streq(buf, "keepalive")) {
		/* Reject delayed callers before changing the previous deadline. */
		if (op13_otg_supply_expired(b) ||
		    (b->op13_otg_supply_lease.state != OP13_OTG_SUPPLY_ARMED &&
		     b->op13_otg_supply_lease.state != OP13_OTG_SUPPLY_ACTIVE))
			goto out;
		b->op13_otg_supply_deadline = ktime_get_boottime_ns() +
			(u64)OP13_OTG_AUTO_WATCHDOG_SECONDS * NSEC_PER_SEC;
		mod_delayed_work(system_freezable_wq, &b->op13_otg_supply_restore_work,
				 OP13_OTG_AUTO_WATCHDOG_SECONDS * HZ);
		ret = 0;
	} else if (on) {
		if (b->op13_otg_supply_lease.state != OP13_OTG_SUPPLY_ARMED ||
		    op13_otg_supply_expired(b))
			goto out;
		spin_lock_irqsave(&b->op13_otg_lock, flags);
		if (!generation || generation != b->op13_otg_auto_generation ||
		    !b->op13_otg_auto_source || b->op13_otg_auto_detach_pending) {
			spin_unlock_irqrestore(&b->op13_otg_lock, flags);
			goto out;
		}
		b->op13_otg_auto_start_generation = generation;
		b->op13_otg_auto_starting = true;
		spin_unlock_irqrestore(&b->op13_otg_lock, flags);
		__pm_stay_awake(b->op13_otg_wakeup);
		b->op13_otg_awake = true;
		ret = op13_otg_supply_start(&b->op13_otg_supply_lease, &io);
		WRITE_ONCE(b->op13_otg_auto_starting, false);
		if (ret)
			op13_otg_awake_release(b);
	} else {
		ret = op13_otg_supply_restore(&b->op13_otg_supply_lease, &io);
		op13_otg_awake_release(b);
	}
out:
	mutex_unlock(&b->lock);
	return ret ? ret : (ssize_t)count;
}

static struct device_attribute dev_attr_oneplus13_otg_auto =
	__ATTR(oneplus13_otg_auto, 0600, oneplus13_otg_auto_show,
	       oneplus13_otg_auto_store);
#endif
