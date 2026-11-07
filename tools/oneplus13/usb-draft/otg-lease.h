/* SPDX-License-Identifier: GPL-2.0-only */
/* Fixed AP17/SWITCH18 transaction policy. No hardware or scheduler here. */
#ifndef ONEPLUS13_OTG_LEASE_H
#define ONEPLUS13_OTG_LEASE_H

#define OP13_OTG_LEASE_SECONDS 120U
#define OP13_OTG_LEASE_IDLE 0U
#define OP13_OTG_LEASE_ACTIVE 1U
#define OP13_OTG_LEASE_RESTORED 2U
#define OP13_OTG_LEASE_UNKNOWN 3U
#define OP13_OTG_LEASE_REFUSED 4U

struct op13_otg_lease {
	unsigned int state;
	unsigned int used;
	unsigned int dirty_ap;
	unsigned int dirty_switch;
};

struct op13_otg_lease_io {
	void *context;
	int (*get)(void *, unsigned int, unsigned int *);
	int (*set)(void *, unsigned int, unsigned int);
	int (*expired)(void *);
};

static inline int op13_otg_lease_values(const struct op13_otg_lease_io *io,
				      unsigned int expected)
{
	unsigned int ap, sw;

	return io->get(io->context, OP13_OTG_AP_ENABLE, &ap) ||
		io->get(io->context, OP13_OTG_SWITCH, &sw) ||
		ap != expected || sw != expected;
}

/* Both values were observed as zero before any write. Restore only owned
 * properties, in reverse order, and require independent GET readback. A SET
 * error is uncertain: it may have reached firmware before the error/ACK loss.
 * The caller's transport must refuse further traffic after ambiguity; this
 * policy never retries a failed transaction or converts reboot to proof.
 */
static inline int op13_otg_lease_restore(struct op13_otg_lease *lease,
				       const struct op13_otg_lease_io *io)
{
	unsigned int value;
	int error = 0;

	if (lease->state == OP13_OTG_LEASE_UNKNOWN)
		return -1;
	if (!lease->dirty_ap && !lease->dirty_switch)
		return 0;
	if (lease->dirty_switch) {
		if (io->set(io->context, OP13_OTG_SWITCH, 0) ||
		    io->get(io->context, OP13_OTG_SWITCH, &value) || value)
			error = -1;
	}
	/* Continue an independent rollback only if the transport still permits
	 * it. Poisoned transport callbacks reject before sending anything.
	 */
	if (lease->dirty_ap) {
		if (io->set(io->context, OP13_OTG_AP_ENABLE, 0) ||
		    io->get(io->context, OP13_OTG_AP_ENABLE, &value) || value)
			error = -1;
	}
	/* AP and switch may be coupled in firmware. Only the final pair after
	 * all writes can prove the saved baseline; retain ownership otherwise.
	 */
	if (!error && op13_otg_lease_values(io, 0))
		error = -1;
	if (error)
		lease->state = OP13_OTG_LEASE_UNKNOWN;
	else {
		lease->dirty_ap = 0;
		lease->dirty_switch = 0;
		lease->state = OP13_OTG_LEASE_RESTORED;
	}
	return error;
}

/* Caller serializes the whole sequence and arms its kernel timer BEFORE
 * entering here. One acquisition per boot, only exact 0/0 baseline; never
 * assume a prior owner's values may be changed. GET success is not USB I/O.
 */
static inline int op13_otg_lease_start(struct op13_otg_lease *lease,
				     const struct op13_otg_lease_io *io)
{
	unsigned int ap, sw, value;

	if (lease->used)
		return -1;
	lease->used = 1;
	lease->state = OP13_OTG_LEASE_REFUSED;
	if (io->get(io->context, OP13_OTG_AP_ENABLE, &ap) ||
	    io->get(io->context, OP13_OTG_SWITCH, &sw) || ap || sw ||
	    io->expired(io->context))
		return -1;
	lease->dirty_ap = 1;
	if (io->set(io->context, OP13_OTG_AP_ENABLE, 1) ||
	    io->get(io->context, OP13_OTG_AP_ENABLE, &value) || value != 1 ||
	    io->expired(io->context))
		goto rollback;
	lease->dirty_switch = 1;
	if (io->set(io->context, OP13_OTG_SWITCH, 1) ||
	    io->get(io->context, OP13_OTG_SWITCH, &value) || value != 1 ||
	    op13_otg_lease_values(io, 1) || io->expired(io->context))
		goto rollback;
	lease->state = OP13_OTG_LEASE_ACTIVE;
	return 0;
rollback:
	op13_otg_lease_restore(lease, io);
	return -1;
}

#endif
