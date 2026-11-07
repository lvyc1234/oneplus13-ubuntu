/* SPDX-License-Identifier: GPL-2.0-only */
/* One-shot, phone-only supply experiment. Transport and timer are caller-owned. */
#ifndef ONEPLUS13_OTG_SUPPLY_LEASE_H
#define ONEPLUS13_OTG_SUPPLY_LEASE_H

#define OP13_OTG_SUPPLY_CURRENT_TARGET 1250U
#define OP13_OTG_SUPPLY_WATCHDOG_SECONDS 120U
#define OP13_OTG_SUPPLY_IDLE 0U
#define OP13_OTG_SUPPLY_ACTIVE 1U
#define OP13_OTG_SUPPLY_RESTORED 2U
#define OP13_OTG_SUPPLY_UNKNOWN 3U
#define OP13_OTG_SUPPLY_REFUSED 4U
#define OP13_OTG_SUPPLY_ARMED 5U

static inline unsigned int op13_otg_supply_le32(const unsigned char *p)
{
	return (unsigned int)p[0] | ((unsigned int)p[1] << 8) |
	       ((unsigned int)p[2] << 16) | ((unsigned int)p[3] << 24);
}

static inline int op13_otg_supply_decode(const unsigned char *data,
		unsigned int len, unsigned int opcode, unsigned int property,
		unsigned int *value)
{
	if (!data || !value || len != 24 ||
	    (opcode != 0x32U && opcode != 0x33U) ||
	    (property != 24U && property != 64U) ||
	    op13_otg_supply_le32(data) != 32778U ||
	    op13_otg_supply_le32(data + 4) != 1U ||
	    op13_otg_supply_le32(data + 8) != opcode ||
	    op13_otg_supply_le32(data + 12) != property)
		return 0;
	if (op13_otg_supply_le32(data + 20) ||
	    (opcode == 0x32U && property == 24U &&
	     op13_otg_supply_le32(data + 16) > 1U))
		return -1;
	*value = opcode == 0x32U ? op13_otg_supply_le32(data + 16) : 0U;
	return 1;
}

struct op13_otg_supply_lease {
	unsigned int state;
	unsigned int used;
	unsigned int saved_ap;
	unsigned int saved_switch;
	unsigned int saved_vbus;
	unsigned int saved_current;
	unsigned int dirty_ap;
	unsigned int dirty_switch;
	unsigned int dirty_vbus;
	unsigned int dirty_current;
};

struct op13_otg_supply_io {
	void *context;
	int (*get)(void *, unsigned int, unsigned int *);
	int (*set)(void *, unsigned int, unsigned int);
	int (*transport_healthy)(void *);
	int (*expired)(void *);
};

static inline int op13_otg_supply_restore(struct op13_otg_supply_lease *lease,
					  const struct op13_otg_supply_io *io)
{
	unsigned int value;

	if (lease->state == OP13_OTG_SUPPLY_UNKNOWN)
		return -1;
	if (!lease->dirty_vbus && !lease->dirty_current &&
	    !lease->dirty_switch && !lease->dirty_ap)
		return 0;
	if (!io->transport_healthy(io->context))
		goto unknown;
	if (lease->dirty_vbus) {
		if (io->set(io->context, 24U, 0U) ||
		    !io->transport_healthy(io->context) ||
		    io->get(io->context, 24U, &value) || value != lease->saved_vbus)
			goto unknown;
		lease->dirty_vbus = 0;
	}
	if (lease->dirty_current) {
		if (!io->transport_healthy(io->context) ||
		    io->set(io->context, 64U, lease->saved_current) ||
		    !io->transport_healthy(io->context) ||
		    io->get(io->context, 64U, &value) || value != lease->saved_current)
			goto unknown;
		lease->dirty_current = 0;
	}
	if (lease->dirty_switch) {
		if (!io->transport_healthy(io->context) ||
		    io->set(io->context, 18U, lease->saved_switch) ||
		    !io->transport_healthy(io->context) ||
		    io->get(io->context, 18U, &value) || value != lease->saved_switch)
			goto unknown;
		lease->dirty_switch = 0;
	}
	if (lease->dirty_ap) {
		if (!io->transport_healthy(io->context) ||
		    io->set(io->context, 17U, lease->saved_ap) ||
		    !io->transport_healthy(io->context) ||
		    io->get(io->context, 17U, &value) || value != lease->saved_ap)
			goto unknown;
		lease->dirty_ap = 0;
	}
	lease->state = OP13_OTG_SUPPLY_RESTORED;
	return 0;

unknown:
	lease->state = OP13_OTG_SUPPLY_UNKNOWN;
	return -1;
}

/* Enable source detection before attachment; this never enables VBUS. */
static inline int op13_otg_supply_arm(struct op13_otg_supply_lease *lease,
				      const struct op13_otg_supply_io *io)
{
	unsigned int ap, sw;

	if (lease->used)
		return -1;
	lease->used = 1;
	lease->state = OP13_OTG_SUPPLY_REFUSED;
	if (!io->transport_healthy(io->context) ||
	    io->get(io->context, 17U, &ap) ||
	    io->get(io->context, 18U, &sw) || ap != 0U || sw != 0U ||
	    io->expired(io->context))
		return -1;
	lease->saved_ap = ap;
	lease->saved_switch = sw;
	lease->dirty_ap = 1U;
	if (io->set(io->context, 17U, 1U) ||
	    !io->transport_healthy(io->context) ||
	    io->get(io->context, 17U, &ap) || ap != 1U ||
	    io->expired(io->context))
		goto rollback;
	lease->dirty_switch = 1U;
	if (io->set(io->context, 18U, 1U) ||
	    !io->transport_healthy(io->context) ||
	    io->get(io->context, 18U, &sw) || sw != 1U ||
	    io->expired(io->context))
		goto rollback;
	lease->state = OP13_OTG_SUPPLY_ARMED;
	return 0;

rollback:
	op13_otg_supply_restore(lease, io);
	return -1;
}

/* Start only after arm and observed 0/0; never enable VBUS before current readback. */
static inline int op13_otg_supply_start(struct op13_otg_supply_lease *lease,
					const struct op13_otg_supply_io *io)
{
	unsigned int vbus, current_ma;

	if (lease->state != OP13_OTG_SUPPLY_ARMED ||
	    !io->transport_healthy(io->context) ||
	    io->get(io->context, 24U, &vbus) ||
	    io->get(io->context, 64U, &current_ma) ||
	    vbus != 0U || current_ma != 0U ||
	    io->expired(io->context))
		return -1;
	lease->saved_vbus = vbus;
	lease->saved_current = current_ma;
	lease->dirty_current = 1U;
	if (io->set(io->context, 64U, OP13_OTG_SUPPLY_CURRENT_TARGET) ||
	    !io->transport_healthy(io->context) ||
	    io->get(io->context, 64U, &current_ma) ||
	    current_ma != OP13_OTG_SUPPLY_CURRENT_TARGET ||
	    io->expired(io->context))
		goto rollback;
	lease->dirty_vbus = 1U;
	if (io->set(io->context, 24U, 1U) ||
	    !io->transport_healthy(io->context) ||
	    io->get(io->context, 24U, &vbus) || vbus != 1U || io->expired(io->context))
		goto rollback;
	lease->state = OP13_OTG_SUPPLY_ACTIVE;
	return 0;

rollback:
	/* A poisoned/ambiguous transport makes restoration UNKNOWN and sends no SET. */
	op13_otg_supply_restore(lease, io);
	return -1;
}

#endif
