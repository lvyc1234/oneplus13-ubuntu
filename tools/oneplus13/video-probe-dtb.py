#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare a separate camera/video RAM tree, preserving the default desktop."""
import copy,hashlib,importlib
from stock import Fdt,cells,strings
camera=importlib.import_module('camera-probe-dtb')
text=camera.text
VIDEO='/soc@0/video-codec@aa00000'
MEMORY='/reserved-memory/video@9c300000'
FIRMWARE='qcom/sm8750/oneplus/dodge/vpu35_4v.mbn'
FIRMWARE_SHA256='e1b2cb6de867893537fe6a1ffba312c2a2949402ca9892d393e2f40de255690a'
FIRMWARE_BYTES=2417352


def validate_video_graph(tree):
    node=tree.nodes[VIDEO]
    if strings(node['compatible'])!=['qcom,sm8750-iris'] or strings(node['status'])!=['disabled']:
        raise ValueError('Unexpected active or foreign video core')
    for key,expected in [('reg',[0,0xaa00000,0,0xf0000]),
                         ('interrupts',[0,174,4])]:
        if cells(node[key])!=expected:raise ValueError('Wrong phone video resource: '+key)
    memory=cells(node['memory-region'])
    if len(memory)!=1 or tree.phandles.get(memory[0])!=MEMORY:
        raise ValueError('Wrong video firmware memory reference')
    reserved=tree.nodes[MEMORY]
    if cells(reserved['reg'])!=[0,0x9c300000,0,0x800000] or 'no-map' not in reserved:
        raise ValueError('Video firmware memory differs from saved phone range')
    for key in ('clocks','clock-names','iommus','interconnects','power-domains',
                'power-domain-names','resets','reset-names','operating-points-v2'):
        if not node.get(key):raise ValueError('Missing pinned video resource: '+key)
    if 'firmware-name' in node:raise ValueError('Unexpected pre-existing video firmware')


def derive(blob):
    camera_blob,camera_report=camera.derive(blob)
    tree=Fdt(camera_blob);before=copy.deepcopy(tree.nodes)
    validate_video_graph(tree)
    tree.nodes[VIDEO]['status']=text('okay')
    tree.nodes[VIDEO]['firmware-name']=text(FIRMWARE)
    changed=[p for p in before if before[p]!=tree.nodes[p]]
    if changed!=[VIDEO] or set(before)!=set(tree.nodes):raise ValueError('Unexpected video tree changes')
    result=tree.to_blob();after=Fdt(result)
    if after.nodes!=tree.nodes or after.reservations!=tree.reservations:raise ValueError('Video tree readback mismatch')
    return result,{'scope':'SEPARATE_MANUAL_CAMERA_VIDEO_RAM_TREE',
        'camera_derivation':camera_report,'video_changed_nodes':changed,
        'video_firmware':FIRMWARE,'video_firmware_sha256':FIRMWARE_SHA256,
        'video_firmware_bytes':FIRMWARE_BYTES,'stock_video_memory_matches':True,
        'additional_memory_reservations':0,'default_boot_tree_changed':False,
        'firmware_authentication_qualified':False,'hardware_decode_qualified':False,
        'base_sha256':hashlib.sha256(blob).hexdigest(),
        'output_sha256':hashlib.sha256(result).hexdigest()}
