# SPDX-License-Identifier: BSD-3-Clause
# Bounded speaker graph, no automatic UCM activation or DSP protection claim.
include(`audioreach/audioreach.m4')
include(`audioreach/stream-subgraph.m4')
include(`audioreach/device-subgraph.m4')
include(`util/route.m4')
include(`util/mixer.m4')
include(`audioreach/tokens.m4')

STREAM_SG_PCM_ADD(audioreach/subgraph-stream-vol-playback.m4, FRONTEND_DAI_MULTIMEDIA1,
 `S16_LE', 48000, 48000, 2, 2,
 0x00004001, 0x00004001, 0x00006001, `110000')
DEVICE_SG_ADD(audioreach/subgraph-device-i2s-playback.m4, `Secondary', SECONDARY_MI2S_RX,
 `S16_LE', 48000, 48000, 2, 2,
 LPAIF_INTF_TYPE_LPAIF, I2S_INTF_TYPE_SECONDARY, SD_LINE_IDX_I2S_SD1, DATA_FORMAT_FIXED_POINT,
 0x00004010, 0x00004010, 0x00006100, `Secondary MI2S')
STREAM_DEVICE_PLAYBACK_MIXER(SECONDARY_MI2S_RX, ``Secondary MI2S'', ``MultiMedia1'')
STREAM_DEVICE_PLAYBACK_ROUTE(SECONDARY_MI2S_RX, ``Secondary MI2S Audio Mixer'', ``MultiMedia1, stream0.logger1'')
