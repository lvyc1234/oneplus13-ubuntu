// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2021-2024 Qualcomm Innovation Center, Inc. All rights reserved.
 * Copyright (c) 2016-2021 The Linux Foundation. All rights reserved.
 */

#define pr_fmt(fmt)	"[drm:%s:%d] " fmt, __func__, __LINE__
/* Verbatim _dce_dsc_initial_line_calc from OnePlusOSS d50b305f7da9e14715a25120a4ac7b1a4b8b97c3
 * vendor/qcom/opensource/display-drivers/msm/sde/sde_encoder_dce.c; host tests only. */
static int _dce_dsc_initial_line_calc(struct msm_display_dsc_info *dsc,
		int enc_ip_width,
		int dsc_cmn_mode)
{
	int max_ssm_delay, max_se_size, max_muxword_size;
	int compress_bpp_group, obuf_latency, input_ssm_out_latency;
	int base_hs_latency, chunk_bits, ob_data_width;
	int output_rate_extra_budget_bits, multi_hs_extra_budget_bits;
	int multi_hs_extra_latency,  mux_word_size;
	int ob_data_width_4comps, ob_data_width_3comps;
	int output_rate_ratio_complement, container_slice_width;
	int rtl_num_components, multi_hs_c, multi_hs_d;

	int bpc = dsc->config.bits_per_component;
	int bpp = DSC_BPP(dsc->config);
	bool native_422 = dsc->config.native_422;
	bool native_420 = dsc->config.native_420;

	/* Hardent core config */
	int multiplex_mode_enable = 0, split_panel_enable = 0;
	int rtl_max_bpc = 10, rtl_output_data_width = 64;
	int pipeline_latency = 28;

	if (dsc_cmn_mode & DSC_MODE_MULTIPLEX)
		multiplex_mode_enable = 1;
	if (dsc_cmn_mode & DSC_MODE_SPLIT_PANEL)
		split_panel_enable = 1;
	container_slice_width = (native_422 ?
			dsc->config.slice_width / 2 : dsc->config.slice_width);
	max_muxword_size = (rtl_max_bpc >= 12) ? 64 : 48;
	max_se_size = 4 * (rtl_max_bpc + 1);
	max_ssm_delay = max_se_size + max_muxword_size - 1;
	mux_word_size = (bpc >= 12) ? 64 : 48;
	compress_bpp_group = native_422 ? (2 * bpp) : bpp;
	input_ssm_out_latency = pipeline_latency + 3 * (max_ssm_delay + 2)
			* dsc->num_active_ss_per_enc;
	rtl_num_components = (native_420 || native_422) ? 4 : 3;
	ob_data_width_4comps = (rtl_output_data_width >= (2 *
			max_muxword_size)) ?
			rtl_output_data_width :
			(2 * rtl_output_data_width);
	ob_data_width_3comps = (rtl_output_data_width >= max_muxword_size) ?
			rtl_output_data_width : 2 * rtl_output_data_width;
	ob_data_width = (rtl_num_components == 4) ?
			ob_data_width_4comps : ob_data_width_3comps;
	obuf_latency = DIV_ROUND_UP((9 * ob_data_width + mux_word_size),
			compress_bpp_group) + 1;
	base_hs_latency = dsc->config.initial_xmit_delay +
		input_ssm_out_latency + obuf_latency;
	chunk_bits = 8 * dsc->config.slice_chunk_size;
	output_rate_ratio_complement = ob_data_width - compress_bpp_group;
	output_rate_extra_budget_bits =
		(output_rate_ratio_complement * chunk_bits) >>
		((ob_data_width == 128) ? 7 : 6);
	multi_hs_c = split_panel_enable * multiplex_mode_enable;
	multi_hs_d = (dsc->num_active_ss_per_enc > 1) * (ob_data_width > compress_bpp_group);
	multi_hs_extra_budget_bits = multi_hs_c ?
				chunk_bits : (multi_hs_d ? chunk_bits :
					output_rate_extra_budget_bits);
	multi_hs_extra_latency = DIV_ROUND_UP(multi_hs_extra_budget_bits,
			compress_bpp_group);
	dsc->initial_lines = DIV_ROUND_UP((base_hs_latency +
				multi_hs_extra_latency),
			container_slice_width);

	return 0;
}
