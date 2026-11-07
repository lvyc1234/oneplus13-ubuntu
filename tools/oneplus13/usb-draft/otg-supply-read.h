/* SPDX-License-Identifier: GPL-2.0-only */
/* Separate GET-only probe; this header grants no supply setters. */
#ifndef ONEPLUS13_OTG_SUPPLY_READ_H
#define ONEPLUS13_OTG_SUPPLY_READ_H

/* Included after the preserved AP17/SW18 protocol header. */

#define OP13_OTG_VBUS_ENABLE 24U
#define OP13_OTG_BOOST_CURRENT 64U

/* Exact legacy GET reply. Current is preserved as uint32, without guessing
 * its valid hardware range or whether the firmware implements this GET.
 * Negative firmware answers never alter the caller's output.
 */
static inline int op13_otg_supply_read_decode(const unsigned char *data,
		unsigned int len, unsigned int opcode, unsigned int property,
		unsigned int *value)
{
	if (!data || !value || len != 24 || opcode != OP13_OTG_GET ||
	    (property != OP13_OTG_VBUS_ENABLE && property != OP13_OTG_BOOST_CURRENT) ||
	    op13_otg_le32(data) != 32778 || op13_otg_le32(data + 4) != 1 ||
	    op13_otg_le32(data + 8) != OP13_OTG_GET ||
	    op13_otg_le32(data + 12) != property)
		return 0;
	if (op13_otg_le32(data + 20) ||
	    (property == OP13_OTG_VBUS_ENABLE && op13_otg_le32(data + 16) > 1))
		return -1;
	*value = op13_otg_le32(data + 16);
	return 1;
}

/* Reception-order evidence only: the wire provides no remote timestamp. */
static inline unsigned int op13_otg_supply_notification(
		const unsigned char *data, unsigned int len)
{
	unsigned int event;

	if (!data || len != 16 || op13_otg_le32(data) != 32778 ||
	    op13_otg_le32(data + 4) != 2 || op13_otg_le32(data + 8) != 7)
		return 0;
	event = op13_otg_le32(data + 12);
	return event == 0x50 || event == 0x51 ? event : 0;
}

#endif
