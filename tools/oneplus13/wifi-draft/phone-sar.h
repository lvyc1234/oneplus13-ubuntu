/* SPDX-License-Identifier: GPL-2.0-only */
/* Copyright (c) 2026 Codex. */
#ifndef ATH12K_PHONE_SAR_H
#define ATH12K_PHONE_SAR_H
#include <linux/completion.h>
#include <linux/mutex.h>
#include <linux/spinlock.h>
#include "sar-wire.h"

struct ath12k_base;
enum phone_sar_phase { PHONE_SAR_IDLE, PHONE_SAR_RESULT, PHONE_SAR_TABLE };
struct ath12k_phone_sar {
	struct mutex transaction;
	spinlock_t lock;
	struct completion done;
	enum phone_sar_phase phase;
	bool attached, poisoned, capability_seen, flags_seen, result_seen;
	u32 generation, raw_version, raw_flags;
	int error;
	struct psar_result result;
	struct psar_table table;
};

/* No policy, cfg80211 callback, default limit or automatic replay is installed. */
void ath12k_phone_sar_init(struct ath12k_base *ab);
void ath12k_phone_sar_attach(struct ath12k_base *ab);
void ath12k_phone_sar_detach(struct ath12k_base *ab);
void ath12k_phone_sar_capability(struct ath12k_base *ab, u16 tag, u16 len,
				const void *value);
void ath12k_phone_sar_ready(struct ath12k_base *ab,
			    const unsigned char *data, size_t len, bool ext2);
bool ath12k_phone_sar_event(struct ath12k_base *ab, u32 event,
			    const unsigned char *data, size_t len);
/* Sleepable caller must hold ab->core_lock across the entire transaction. */
int ath12k_phone_sar_get(struct ath12k_base *ab, struct psar_table *out);
/* V2 only: returns observed result/table, NEVER effective-power qualification.
 * The GET values are half-dBm, while SET values below are profile indexes.
 * Caller must provide a separately reviewed policy; this has no production
 * caller and must not be connected to RF admission on its own.
 */
int ath12k_phone_sar_observe_v2(struct ath12k_base *ab,
			       const struct psar_row *rows, size_t count,
			       struct psar_result *result, struct psar_table *table);
#endif
