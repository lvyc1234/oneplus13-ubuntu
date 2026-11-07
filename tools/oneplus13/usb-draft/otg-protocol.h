/* SPDX-License-Identifier: GPL-2.0-only */
/* OnePlusOSS d50b305f legacy USB property response, no raw hardware I/O. */
#ifndef ONEPLUS13_OTG_PROTOCOL_H
#define ONEPLUS13_OTG_PROTOCOL_H

#define OP13_OTG_AP_ENABLE 17U
#define OP13_OTG_SWITCH 18U
#define OP13_OTG_GET 0x32U
#define OP13_OTG_SET 0x33U

static inline unsigned int op13_otg_le32(const unsigned char *p)
{
	return (unsigned int)p[0] | ((unsigned int)p[1] << 8) |
	       ((unsigned int)p[2] << 16) | ((unsigned int)p[3] << 24);
}

/* 0: unrelated/malformed, 1: exact success, -1: exact firmware/value error.
 * Output is changed only by an exact successful response. No transaction ID
 * exists: the driver must poison this service epoch after any timeout.
 */
static inline int op13_otg_decode(const unsigned char *data, unsigned int len,
				unsigned int expected_opcode,
				unsigned int expected_property,
				unsigned int *value)
{
	if (!data || !value || len != 24 ||
	    (expected_opcode != OP13_OTG_GET && expected_opcode != OP13_OTG_SET) ||
	    (expected_property != OP13_OTG_AP_ENABLE &&
	     expected_property != OP13_OTG_SWITCH) ||
	    op13_otg_le32(data) != 32778 || op13_otg_le32(data + 4) != 1 ||
	    op13_otg_le32(data + 8) != expected_opcode ||
	    op13_otg_le32(data + 12) != expected_property)
		return 0;
	if (op13_otg_le32(data + 20) ||
	    (expected_opcode == OP13_OTG_GET && op13_otg_le32(data + 16) > 1))
		return -1;
	/* Vendor SET validation does not promise an echoed value. A subsequent
	 * serialized GET is mandatory before calling a mutation verified.
	 */
	*value = expected_opcode == OP13_OTG_GET ? op13_otg_le32(data + 16) : 0;
	return 1;
}

#endif
