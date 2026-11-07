# SPDX-License-Identifier: BSD-3-Clause
# Offline phone graph, using Linaro AudioReach macros; no UCM or activation.
include(`audioreach/audioreach.m4')
include(`audioreach/stream-subgraph.m4')
include(`audioreach/device-subgraph.m4')
include(`util/route.m4')
include(`util/mixer.m4')
include(`audioreach/tokens.m4')

# The saved phone OTA declares TX3, 48kHz, two channels, 24 valid bits.
# Keep capture independent from every speaker/DP playback graph.
STREAM_SG_PCM_ADD(audioreach/subgraph-stream-capture.m4, FRONTEND_DAI_MULTIMEDIA3,
 `S24_LE', 48000, 48000, 2, 2,
 0x00004003, 0x00004003, 0x00006020, `110000')
DEVICE_SG_ADD(audioreach/subgraph-device-codec-dma-capture.m4, `TX_CODEC_DMA_TX_3', TX_CODEC_DMA_TX_3,
 `S24_LE', 48000, 48000, 2, 2,
 LPAIF_INTF_TYPE_RXTX, CODEC_INTF_IDX_TX3, 0, DATA_FORMAT_FIXED_POINT,
 0x00004009, 0x00004009, 0x00006090)
STREAM_DEVICE_CAPTURE_MIXER(FRONTEND_DAI_MULTIMEDIA3, ``TX_CODEC_DMA_TX_3'')
STREAM_DEVICE_CAPTURE_ROUTE(FRONTEND_DAI_MULTIMEDIA3, ``MultiMedia3 Mixer'', ``TX_CODEC_DMA_TX_3, device120.logger1'')
