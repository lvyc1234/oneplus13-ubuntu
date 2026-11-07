/* SPDX-License-Identifier: GPL-2.0-only */
/* Repeatable automatic sessions; the manual one-shot API remains unchanged. */
#ifndef ONEPLUS13_OTG_AUTO_POLICY_H
#define ONEPLUS13_OTG_AUTO_POLICY_H

#include "oneplus13-otg-supply-lease.h"

#define OP13_OTG_AUTO_WATCHDOG_SECONDS 15U

/* No low-byte masking: 0x150 is not an OTG enable notification. */
static inline int op13_otg_auto_decode(const unsigned char *p,
		unsigned int len, unsigned int *source_requested)
{
	unsigned int event;

	if (!p || !source_requested || len != 16U ||
	    op13_otg_supply_le32(p) != 32778U ||
	    op13_otg_supply_le32(p + 4) != 2U ||
	    op13_otg_supply_le32(p + 8) != 7U)
		return 0;
	event = op13_otg_supply_le32(p + 12);
	if (event != 0x50U && event != 0x51U)
		return 0;
	*source_requested = event == 0x50U;
	return 1;
}

/* Reuse is possible only after the previous cycle restored every owned value.
 * Poison is boot-wide in the transport; restarting userspace cannot clear it.
 */
static inline int op13_otg_auto_arm(struct op13_otg_supply_lease *lease,
				   const struct op13_otg_supply_io *io)
{
	if (!io->transport_healthy(io->context) ||
	    (lease->state != OP13_OTG_SUPPLY_IDLE &&
	     lease->state != OP13_OTG_SUPPLY_RESTORED) ||
	    lease->dirty_ap || lease->dirty_switch ||
	    lease->dirty_vbus || lease->dirty_current)
		return -1;
	*lease = (struct op13_otg_supply_lease) { 0 };
	return op13_otg_supply_arm(lease, io);
}

#endif
