# AW88461 offline component

Phone audio graph drafts are separate source-only outputs. The microphone
uses TX_CODEC_DMA_TX_3, and the speaker uses Secondary MI2S, each48kHz/two
channels/S24_LE as declared by the saved phone OTA XML. Both compile/decode
against the original pinned BSD-3-Clause AudioReach macros. No Pad TDM/WSA/DP
graph, gain coefficients, UCM, service or runtime install is included.
The Android ADC1/ADC4 channel-map controls differ from the selected mainline
WCD939x SoundWire mapping; that mapping and machine clocks remain unqualified.
The speaker graph does not implement calibration, thermal/excursion protection
or permission to stream. See docs/oneplus13/AV-SSC-OFFLINE-20261026.md.

Compile and synthetic fault/parser tests only. No phone DT references, firmware,
profile, calibration, service, module-install target or boot image is supplied.
The running phone amplifier identity and its protected owner-matched tuning
remain unverified. Successful compilation does not authorize amplifier loading.

Driver reference: GEEKiDoS/linux commit
`b0c05eb72e10140f87efd0e499ad840e6c3e6975`,
`sound/soc/codecs/aw88461.c`, GPL-2.0-only. Retrieved source SHA256:
`ba583b081d8c5e741b384f02ac69f9c4eda80396bf4e37ace7e4cc3e6d7b3d05`.
The SPDX header and existing description are preserved. That pinned source
contains no named copyright or MODULE_AUTHOR; none is invented here.

The local draft adds a restricted legacy ACF preflight before the shared parser.
The preflight and synthetic tests were prepared by Codex. Power controls are
regenerated from the actual staged driver for each check, never from an old
generated copy. Poll timing, worker/DAPM concurrency, electrical behavior and
acoustic protection are not established by the host stubs.

The compile job uses the existing phone kernel pin
`yzddmr6/linux-sm8750-erhai` commit
`7a81d184e3098624d4342d8f4eff5e23674bcc9f` and its AW88395 shared parser.
It builds objects only, leaving the normal phone/Pad build paths unchanged.
