# AA569 SPR2 profile and transaction planner

This GPL-2.0-only component is compiled into the isolated phone msm module.
The decoder/planner remains free of hardware access. The optional runtime
backend requires oneplus,aa569-static-spr on the SM8750 DPU, a OnePlus13 root,
MDSS reset and a locally supplied private profile. Default/touch DTBs have no
opt-in and do not access SPR. The restricted runtime uses CPU writes only at
quiet probe/PM boundaries; no DMA buffers/submission, live profile updates,
partial update or UDC/fingerprint features are implemented. This is an
experimental bring-up backend, not hardware-accepted display support (debtD10).
Host tests use synthetic coefficients. Private profiles must not be uploaded.

The private v1 format is176 bytes, little endian: magic AA569S2 followed by NUL,
eight u32 header fields (version1, width1440, height3168, refresh120, wordcount34,
IEEE CRC32 of the payload, flags0, reserved0), followed by34 fixed u32 words.
Words are cfg17[4], cfg16[8], cfg13, cfg10, cfg15[16], PBA, opmode, two-LM mode1
and full-frame PU origin0. There are no arbitrary offsets, addresses or writes.
CRC detects accidental corruption, not authenticity or matching hardware.

The decoder checks exact size/magic/geometry/version, CRC, every field mask,
the investigated PenTile/2DAvg/YYGM control route and PBA consistency. It rejects
UDC/reserved bits. The planner retains the separate PBA direct, INIT REGDMA
and PU REGDMA paths, with bit25 preserved only at SPR_CONFIG. It does not
write the legacy cfg14 register0x54. Failed calls leave outputs unchanged.

Packing is derived from OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750
at d50b305f7da9e14715a25120a4ac7b1a4b8b97c3, display-drivers/msm/sde/
sde_hw_reg_dma_v1_color_proc.c (reg_dmav1_setup_spr_init_cfgv2 and its helpers).
The original helpers have already been checked independently against private
Android ARM64 conversion. No vendor binary, XML or private coefficient is here.

The runtime reserves DSPP independently of CTM/gamma, checks all four UDC
bits before writing any pipe, rejects busy/faulted encoders and unpowered
access, clears SPR before clocks stop and restores before encoder configuration.
It restricts atomic modes to single DSI0 command120Hz AA569, 2LM/2DSC/1INTF,
rejecting video/DP/writeback/clone/self-refresh. SPR flush bit8 is re-armed for
every commit without rewriting live coefficients. The required MDSS reset now
propagates assert/deassert failures instead of claiming success.

Stock SDE base0xae00000 and upstream MDP base0xae01000 differ. Per-DSPP SPR
sub-block offsets are15400/14400/13400/12400, relative to upstream DSPP bases
54000/56000/58000/5a000. Actual physical SPR bases are0xae6a400/0xae6b400/
0xae6c400/0xae6d400. Another board's offsets or calibration cannot be used.
The quiet-pipeline CPU approach was inspected in joonhoekim/tb323fu-linux
at da44c8412fed3baa609a8084a214ae9b5c9f4311 (EYC/kaanapali provenance).
Register contents are the separately checked AA569 profile, not that panel's.
Physical power-cycle/clock/scanout behavior still requires device acceptance.
