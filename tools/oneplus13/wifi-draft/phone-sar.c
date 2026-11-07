// SPDX-License-Identifier: GPL-2.0-only
// Copyright (c) 2026 Codex. See PROVENANCE.md.
#include "core.h"
#include "wmi.h"
#include "phone-sar.h"
#include <linux/unaligned.h>

/* Verified from the pinned vendor enums by check-driver.py, not guessed from
 * tag IDs. SAR2/GET have no request token: never retry after an ambiguous reply.
 */
#define PHONE_SAR_SET_COMMAND 0x29006
#define PHONE_SAR_GET_COMMAND 0x29007
#define PHONE_SAR_RESULT_EVENT 0x1d01c
#define PHONE_SAR_TABLE_EVENT 0x29002
#define PHONE_SAR_TIMEOUT_MS 1000

void ath12k_phone_sar_init(struct ath12k_base *ab)
{
	struct ath12k_phone_sar *s = &ab->wmi_ab.phone_sar;

	mutex_init(&s->transaction);
	spin_lock_init(&s->lock);
	init_completion(&s->done);
}

static void phone_sar_invalidate(struct ath12k_phone_sar *s, int error)
{
	s->poisoned = true;
	s->error = error;
	s->phase = PHONE_SAR_IDLE;
	complete(&s->done);
}

void ath12k_phone_sar_attach(struct ath12k_base *ab)
{
	struct ath12k_phone_sar *s = &ab->wmi_ab.phone_sar;

	spin_lock_bh(&s->lock);
	/* Once ambiguous, this driver instance stays closed across firmware SSR.
	 * Reattaching must not make an old tokenless response eligible again.
	 */
	if (s->attached || s->phase != PHONE_SAR_IDLE)
		phone_sar_invalidate(s, -ESHUTDOWN);
	s->attached = true;
	s->generation++;
	s->capability_seen = false;
	s->flags_seen = false;
	s->result_seen = false;
	spin_unlock_bh(&s->lock);
}

void ath12k_phone_sar_detach(struct ath12k_base *ab)
{
	struct ath12k_phone_sar *s = &ab->wmi_ab.phone_sar;

	spin_lock_bh(&s->lock);
	s->attached = false;
	s->generation++;
	s->capability_seen = false;
	s->flags_seen = false;
	/* Preserve the stop across later attach; no unreviewed policy replay. */
	phone_sar_invalidate(s, -ESHUTDOWN);
	spin_unlock_bh(&s->lock);
}

void ath12k_phone_sar_capability(struct ath12k_base *ab, u16 tag, u16 len,
				const void *value)
{
	struct ath12k_phone_sar *s = &ab->wmi_ab.phone_sar;
	u32 raw;

	if (tag != 700 && tag != 1233)
		return;
	spin_lock_bh(&s->lock);
	if (!s->attached || len != sizeof(__le32)) {
		phone_sar_invalidate(s, -EPROTO);
		goto out;
	}
	raw = get_unaligned_le32(value);
	if (tag == 700) {
		if (s->capability_seen) {
			phone_sar_invalidate(s, -EPROTO);
			goto out;
		}
		s->raw_version = raw; /* Retain unknown values; never remap to v1. */
		s->capability_seen = true;
	} else {
		if (s->flags_seen) {
			phone_sar_invalidate(s, -EPROTO);
			goto out;
		}
		s->raw_flags = raw;
		s->flags_seen = true;
	}
out:
	spin_unlock_bh(&s->lock);
}

void ath12k_phone_sar_ready(struct ath12k_base *ab,
			    const unsigned char *data, size_t len, bool ext2)
{
	struct ath12k_phone_sar *s = &ab->wmi_ab.phone_sar;
	size_t offset = 0, end, item;
	u32 header, size, tag;

	/* SAR capability is top-level in EXT; flags are an ARRAY_STRUCT element
	 * in EXT2. Inspect those exact positions, not positional array counters.
	 */
	while (offset < len) {
		if (len - offset < 4)
			goto malformed;
		header = get_unaligned_le32(data + offset);
		size = header & 0xffff;
		tag = header >> 16;
		offset += 4;
		if (size > len - offset || size % 4)
			goto malformed;
		end = offset + size;
		if (!ext2 && tag == 700)
			ath12k_phone_sar_capability(ab, tag, size, data + offset);
		if (ext2 && tag == 18) {
			for (item = offset; item < end; item += 4 + size) {
				if (end - item < 4)
					goto malformed;
				header = get_unaligned_le32(data + item);
				size = header & 0xffff;
				if (size > end - item - 4 || size % 4)
					goto malformed;
				if (header >> 16 == 1233)
					ath12k_phone_sar_capability(ab, 1233, size,
							    data + item + 4);
			}
		}
		offset = end;
	}
	return;
malformed:
	spin_lock_bh(&s->lock);
	phone_sar_invalidate(s, -EPROTO);
	spin_unlock_bh(&s->lock);
}

bool ath12k_phone_sar_event(struct ath12k_base *ab, u32 event,
			    const unsigned char *data, size_t len)
{
	struct ath12k_phone_sar *s = &ab->wmi_ab.phone_sar;
	int ret;

	if (event != PHONE_SAR_RESULT_EVENT && event != PHONE_SAR_TABLE_EVENT)
		return false;
	spin_lock_bh(&s->lock);
	if (!s->attached || s->poisoned ||
	    (event == PHONE_SAR_RESULT_EVENT && s->phase != PHONE_SAR_RESULT) ||
	    (event == PHONE_SAR_TABLE_EVENT && s->phase != PHONE_SAR_TABLE)) {
		phone_sar_invalidate(s, -EPROTO);
		goto out;
	}
	if (event == PHONE_SAR_RESULT_EVENT) {
		ret = psar_decode_result(data, len, &s->result);
		s->result_seen = ret == PSAR_OK;
		if (ret || !s->result.recognized ||
		    !s->result.firmware_reported_success) {
			phone_sar_invalidate(s, ret ? -EPROTO : -EREMOTEIO);
			goto out;
		}
	} else {
		ret = psar_decode_table(data, len, &s->table);
		if (ret) {
			phone_sar_invalidate(s, -EPROTO);
			goto out;
		}
	}
	s->phase = PHONE_SAR_IDLE;
	s->error = 0;
	complete(&s->done);
out:
	spin_unlock_bh(&s->lock);
	return true;
}

/* Called with transaction held; event receive never needs that mutex. */
static int phone_sar_exchange(struct ath12k_base *ab, u32 command,
			      enum phone_sar_phase phase,
			      const unsigned char *data, size_t len, u32 generation)
{
	struct ath12k_phone_sar *s = &ab->wmi_ab.phone_sar;
	struct sk_buff *skb;
	int ret;

	skb = ath12k_wmi_alloc_skb(&ab->wmi_ab, len);
	if (!skb)
		return -ENOMEM;
	memcpy(skb->data, data, len);
	spin_lock_bh(&s->lock);
	if (!s->attached || s->poisoned || generation != s->generation) {
		spin_unlock_bh(&s->lock);
		dev_kfree_skb(skb);
		return -ESHUTDOWN;
	}
	reinit_completion(&s->done);
	s->phase = phase;
	s->error = -EINPROGRESS;
	spin_unlock_bh(&s->lock);
	ret = ath12k_wmi_cmd_send(&ab->wmi_ab.wmi[0], skb, command);
	if (ret) {
		dev_kfree_skb(skb);
		spin_lock_bh(&s->lock);
		/* Submission failure can be ambiguous to the target. */
		phone_sar_invalidate(s, ret);
		spin_unlock_bh(&s->lock);
		return ret;
	}
	ret = wait_for_completion_timeout(&s->done,
					msecs_to_jiffies(PHONE_SAR_TIMEOUT_MS));
	spin_lock_bh(&s->lock);
	if (!ret)
		phone_sar_invalidate(s, -ETIMEDOUT);
	if (generation != s->generation || !s->attached)
		ret = -ESHUTDOWN;
	else
		ret = s->error;
	spin_unlock_bh(&s->lock);
	return ret;
}

static int phone_sar_begin(struct ath12k_phone_sar *s, bool v2, u32 *generation)
{
	int ret = 0;

	if (!mutex_trylock(&s->transaction))
		return -EBUSY;
	spin_lock_bh(&s->lock);
	if (!s->attached || s->poisoned)
		ret = -ESHUTDOWN;
	else if (!s->capability_seen || s->raw_version > 5 ||
		 (v2 && (s->raw_version != 1 || !s->flags_seen || s->raw_flags)))
		ret = -EOPNOTSUPP;
	else
		*generation = s->generation;
	spin_unlock_bh(&s->lock);
	if (ret)
		mutex_unlock(&s->transaction);
	return ret;
}

int ath12k_phone_sar_get(struct ath12k_base *ab, struct psar_table *out)
{
	struct ath12k_phone_sar *s = &ab->wmi_ab.phone_sar;
	unsigned char data[8];
	size_t len;
	u32 generation;
	int ret;

	lockdep_assert_held(&ab->core_lock);
	if (!out)
		return -EINVAL;
	ret = phone_sar_begin(s, false, &generation);
	if (ret)
		return ret;
	psar_encode_get(data, sizeof(data), &len);
	ret = phone_sar_exchange(ab, PHONE_SAR_GET_COMMAND, PHONE_SAR_TABLE,
				 data, len, generation);
	spin_lock_bh(&s->lock);
	if (!ret && (s->poisoned || generation != s->generation))
		ret = -ESHUTDOWN;
	if (!ret)
		*out = s->table;
	spin_unlock_bh(&s->lock);
	mutex_unlock(&s->transaction);
	return ret;
}

int ath12k_phone_sar_observe_v2(struct ath12k_base *ab,
			       const struct psar_row *rows, size_t count,
			       struct psar_result *result, struct psar_table *table)
{
	struct ath12k_phone_sar *s = &ab->wmi_ab.phone_sar;
	unsigned char *data;
	size_t len;
	u32 generation;
	int ret;

	lockdep_assert_held(&ab->core_lock);
	if (!result || !table)
		return -EINVAL;
	data = kmalloc(PSAR_MAX_SET_BYTES, GFP_KERNEL);
	if (!data)
		return -ENOMEM;
	ret = psar_encode_set(7, rows, count, data, PSAR_MAX_SET_BYTES, &len);
	if (ret) {
		ret = -EINVAL;
		goto free;
	}
	ret = phone_sar_begin(s, true, &generation);
	if (ret)
		goto free;
	spin_lock_bh(&s->lock);
	s->result_seen = false;
	spin_unlock_bh(&s->lock);
	ret = phone_sar_exchange(ab, PHONE_SAR_SET_COMMAND, PHONE_SAR_RESULT,
				 data, len, generation);
	if (ret)
		goto unlock;
	psar_encode_get(data, PSAR_MAX_SET_BYTES, &len);
	ret = phone_sar_exchange(ab, PHONE_SAR_GET_COMMAND, PHONE_SAR_TABLE,
				 data, len, generation);
unlock:
	spin_lock_bh(&s->lock);
	if (!ret && (s->poisoned || generation != s->generation))
		ret = -ESHUTDOWN;
	if (!ret && s->table.firmware_selection != 8) {
		phone_sar_invalidate(s, -EREMOTEIO);
		ret = -EREMOTEIO;
	}
	/* Preserve rejection result independently; never publish partial table. */
	if (s->result_seen)
		*result = s->result;
	if (!ret)
		*table = s->table;
	spin_unlock_bh(&s->lock);
	mutex_unlock(&s->transaction);
free:
	kfree(data);
	return ret;
}
