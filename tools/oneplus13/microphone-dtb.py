#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Separate, default-off boot candidate for native microphone/speaker tests."""
import copy,hashlib,importlib
from stock import Fdt,cells,strings
audio=importlib.import_module('audio-dtb')
MODEL='OnePlus13-Audio-Probe'
TOPOLOGY='qcom/sm8750/'+MODEL+'-tplg.bin'
WCD='/wcd939x'
SUPPLY='/soc@0/rsc@16500000/regulators-4/ldo2'
CAPTURE='/sound/wcd-capture-dai-link'
def derive(blob):
    native=Fdt(blob)
    result,speaker_proof=audio.derive(blob)
    tree=Fdt(result);before=copy.deepcopy(tree.nodes)
    if strings(native.nodes[SUPPLY]['regulator-name'])!=['vreg_l2i_1p2']:
        raise ValueError('Missing phone WCD PX supply')
    for k in ('regulator-min-microvolt','regulator-max-microvolt'):
        if cells(native.nodes[SUPPLY][k])!=[1200000]:raise ValueError('Wrong phone PX voltage')
    # C.93 owner tree uses V8I L2=1.2V for PX and 2.7V for all AMIC biases.
    # Preserve the qualified speaker-only tree and make this an alternative
    # boot artifact. The default image never probes this codec.
    tree.nodes[WCD]['vdd-px-supply']=native.nodes[SUPPLY]['phandle']
    for n in range(1,5):tree.nodes[WCD][f'qcom,micbias{n}-microvolt']=audio.u32(2700000)
    if cells(tree.nodes[WCD]['reset-gpios'])[1:]!=[101,1]:raise ValueError('Wrong phone WCD reset')
    tree.nodes['/sound']['model']=audio.text(MODEL)
    routes=strings(native.nodes['/sound']['audio-routing'])
    pairs=list(zip(routes[::2],routes[1::2]))
    wanted=[p for p in pairs if p[0].startswith(('AMIC','TX SWR_INPUT'))]
    if len(wanted)!=9:raise ValueError('Original phone microphone routes changed')
    # The pinned SM8750 tree falls back to sm8550 TX macro (LPASS v11), whose
    # inputs are TX SWR_INPUT0..11. Do not substitute the v9 TX SWR_ADC names.
    tree.nodes['/sound']['audio-routing']=audio.text(*(s for pair in wanted for s in pair))
    tree.nodes[CAPTURE]['status']=audio.text('okay')
    tree.nodes[CAPTURE]['convert-rate']=audio.u32(48000)
    tree.nodes[CAPTURE]['convert-channels']=audio.u32(2)
    tree.nodes[CAPTURE]['convert-sample-format']=audio.text('s24_le')
    # Keep the reviewed original WCD/SoundWire/TX-macro multi-codec endpoints.
    if tree.nodes[CAPTURE+'/codec']!=native.nodes[CAPTURE+'/codec']:
        raise ValueError('Microphone endpoints changed')
    if cells(tree.nodes[CAPTURE+'/cpu']['sound-dai'])[1:]!=[120]:raise ValueError('Wrong TX3 capture port')
    changed={p for p in before if before[p]!=tree.nodes[p]}
    if changed!={WCD,'/sound',CAPTURE}:raise ValueError('Nonaudio mutation in microphone alternative')
    result=tree.to_blob();check=Fdt(result)
    if check.nodes!=tree.nodes or check.reservations!=tree.reservations:raise ValueError('Microphone DTB readback changed')
    return result,{'scope':'SEPARATE_MICROPHONE_SPEAKER_RAM_BOOT_CANDIDATE',
        'base_sha256':audio.BASE_SHA,'output_sha256':hashlib.sha256(result).hexdigest(),
        'speaker_derivation':speaker_proof,'additional_changed_nodes':sorted(changed),
        'px_microvolt':1200000,'micbias_microvolt':2700000,'reset_gpio':101,
        'headphone_playback_enabled':False,'microphone_or_acoustic_acceptance':False,
        'default_speaker_boot_changed':False,'automatic_activation':False}
