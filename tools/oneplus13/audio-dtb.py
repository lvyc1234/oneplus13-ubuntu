#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Derive a gated speaker candidate; preserve every nonaudio DT property."""
import copy
import hashlib
import struct
from stock import Fdt, cells, strings

BASE_SHA = '2a2ca49e0002ae4a80c0f35aad00a2efd282be6b640a684abb01ac72866f6b91'
I2C = '/soc@0/geniqup@9c0000/i2c@990000'
TLMM = '/soc@0/pinctrl@f100000'
APM = '/soc@0/remoteproc@6800000/glink-edge/gpr/service@1'
BEDAI = APM + '/bedais'
PRM = '/soc@0/remoteproc@6800000/glink-edge/gpr/service@2/clock-controller'
FIRMWARE = 'awinic/oneplus13/aw88461_acf.bin'
TOPOLOGY = 'qcom/sm8750/OnePlus13-Speaker-Test-tplg.bin'

def u32(*values):
    return struct.pack('>'+'I'*len(values), *values)

def text(*values):
    return ('\0'.join(values)+'\0').encode()

def derive(blob):
    # Accept only the reviewed calibration tree. Boot arguments are added by
    # the caller afterwards and never carried over from an old boot.
    if hashlib.sha256(blob).hexdigest() != BASE_SHA:
        raise ValueError('Audio derivation needs the reviewed base DTB')
    tree=Fdt(blob)
    before=copy.deepcopy(tree.nodes)
    expected={I2C:'qcom,geni-i2c-master-hub', TLMM:'qcom,sm8750-tlmm',
              APM:'qcom,q6apm', BEDAI:'qcom,q6apm-lpass-dais',
              PRM:'qcom,q6prm-lpass-clocks'}
    for path,compatible in expected.items():
        if compatible not in strings(tree.nodes[path]['compatible']):
            raise ValueError('Audio provider identity changed')
    handle=max(tree.phandles)+1
    def add(path, props, phandle=False):
        nonlocal handle
        if path in tree.nodes:raise ValueError('Audio node already exists')
        tree.nodes[path]=props
        if phandle:
            tree.nodes[path]['phandle']=u32(handle)
            handle+=1
            return handle-1
    def ph(path):return cells(tree.nodes[path]['phandle'])[0]
    # Exact owner stock MI2S active drive is 4mA. Sleep uses GPIO pull-down,
    # 2mA, rather than inheriting the tablet's TDM drive or its six amps.
    active=add(TLMM+'/oneplus13-mi2s-active',{},True)
    asleep=add(TLMM+'/oneplus13-mi2s-sleep',{},True)
    for pin,function in [(121,'i2s1_sck'),(122,'i2s1_data0'),
                         (123,'i2s1_ws'),(124,'i2s1_data1')]:
        add(TLMM+f'/oneplus13-mi2s-active/pin{pin}',
            {'pins':text(f'gpio{pin}'),'function':text(function),
             'drive-strength':u32(4),'bias-disable':b''})
        add(TLMM+f'/oneplus13-mi2s-sleep/pin{pin}',
            {'pins':text(f'gpio{pin}'),'function':text('gpio'),
             'drive-strength':u32(2),'bias-pull-down':b''})
    amps=[]
    for channel,address,reset in [(0,0x34,2),(1,0x35,164)]:
        amps.append(add(I2C+f'/audio-codec@{address:x}',{
            'compatible':text('awinic,aw88461'),'reg':u32(address),
            '#sound-dai-cells':u32(0),'reset-gpios':u32(ph(TLMM),reset,1),
            'sound-name-prefix':text(f'Amp{channel}'),
            'awinic,audio-channel':u32(channel),'firmware-name':text(FIRMWARE),
            'status':text('okay')},True))
    tree.nodes[I2C]['status']=text('okay')
    tree.nodes[I2C.rsplit('/',1)[0]]['status']=text('okay')
    tree.nodes[BEDAI]['#address-cells']=u32(1)
    tree.nodes[BEDAI]['#size-cells']=u32(0)
    # SECONDARY_MI2S_RX=18; SEC_MI2S_IBIT=3; COUPLE_NO=1 in the pinned
    # qcom,q6dsp-lpass-ports.h. Initial graph is 16bit stereo at 48kHz,
    # matching the CPU port's supported format and a 1.536MHz bit clock.
    add(BEDAI+'/dai@12',{'reg':u32(18),'clocks':u32(ph(PRM),3,1),
                          'clock-names':text('bclk')})
    sound=tree.nodes['/sound']
    sound['model']=text('OnePlus13-Speaker-Test')
    sound['pinctrl-names']=text('default','sleep')
    sound['pinctrl-0']=u32(active)
    sound['pinctrl-1']=u32(asleep)
    sound.pop('audio-routing',None)
    # Capture/headset remain separate unqualified work. Do not require their
    # codecs or publish the stale WSA route in this explicitly named test card.
    for path in before:
        if path.startswith('/sound/') and path.count('/')==2:
            tree.nodes[path]['status']=text('disabled')
    link='/sound/oneplus13-speaker-playback-dai-link'
    add(link,{'link-name':text('Speaker Playback'),'dai-format':text('i2s'),
              'convert-rate':u32(48000),'convert-channels':u32(2),
              'convert-sample-format':text('s16_le')})
    add(link+'/cpu',{'sound-dai':u32(ph(BEDAI),18)})
    add(link+'/platform',{'sound-dai':u32(ph(APM))})
    add(link+'/codec',{'sound-dai':u32(*amps)})
    existing_changes={p for p in before if before[p]!=tree.nodes[p]}
    allowed={I2C,I2C.rsplit('/',1)[0],BEDAI,'/sound'}|{
        p for p in before if p.startswith('/sound/') and p.count('/')==2}
    if existing_changes!=allowed:
        raise ValueError('Unexpected existing-node mutation')
    blob=tree.to_blob()
    readback=Fdt(blob)
    if readback.nodes!=tree.nodes or readback.reservations!=tree.reservations:
        raise ValueError('Audio DTB readback differs')
    return blob,{'scope':'EXPERIMENTAL_SPEAKER_DTB_NOT_HARDWARE_ACCEPTANCE',
        'base_sha256':BASE_SHA,'output_sha256':hashlib.sha256(blob).hexdigest(),
        'existing_nodes_changed':sorted(existing_changes),
        'nodes_added':sorted(set(tree.nodes)-set(before)),
        'nonaudio_properties_and_reservations_preserved':True,
        'default_module_policy_requires_explicit_activation':True,
        'amplifier_default_volume':0,'amplifier_test_volume_ceiling':640,
        'qualified_quiet_checker_volume':256,'acoustic_checker_volume':640,
        'dynamic_protection_qualified':False,'audio_accepted':False}
