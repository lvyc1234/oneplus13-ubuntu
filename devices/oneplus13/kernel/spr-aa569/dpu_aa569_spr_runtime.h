/* SPDX-License-Identifier: GPL-2.0-only */
/* Copyright (c) 2026 OpenAI */
#ifndef DPU_AA569_SPR_RUNTIME_H
#define DPU_AA569_SPR_RUNTIME_H

struct dpu_kms;
struct drm_crtc;
struct drm_crtc_state;

int dpu_aa569_spr_load(struct dpu_kms *kms);
int dpu_aa569_spr_restore(struct dpu_kms *kms);
int dpu_aa569_spr_suspend(struct dpu_kms *kms);
int dpu_aa569_spr_check(struct drm_crtc *crtc, struct drm_crtc_state *state);

#endif
