#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Derive a separate CCI chip-ID probe tree; no CAMSS/PHY or camera stream."""
import copy,hashlib,importlib
from stock import Fdt,cells,strings
mic=importlib.import_module('microphone-dtb')
audio=importlib.import_module('audio-dtb')
u32=audio.u32;text=audio.text
RSC='/soc@0/rsc@16500000'
TLMM='/soc@0/pinctrl@f100000'
CAMCC='/soc@0/clock-controller@ade0000'
MCLK='/soc@0/clock-controller@1760000'
CORE=RSC+'/regulators-4/ldo1'
CCI='/soc@0/cci@ac7b000'
NFC_I2C='/soc@0/geniqup@ac0000/i2c@a80000'
NFC=NFC_I2C+'/nfc@28'
BANK=RSC+'/regulators-camera'
# C.93 D/L/S ultrawide module slaveInfo all carry 8-bit0x5a.
# Linux I2C reg is the 7-bit address, not the reference-board0x10.
SENSOR=CCI+'/i2c-bus@0/camera@2d'


def derive(blob):
    result,microphone=mic.derive(blob)
    tree=Fdt(result);before=copy.deepcopy(tree.nodes)
    for path,compatible in ((CAMCC,'qcom,sm8750-camcc'),(MCLK,'qcom,sm8750-cambistmclkcc'),
                            (TLMM,'qcom,sm8750-tlmm')):
        if compatible not in strings(tree.nodes[path]['compatible']):
            raise ValueError('Wrong camera provider: '+path)
    if strings(tree.nodes[CORE]['regulator-name'])!=['vreg_l1i_1p2']:
        raise ValueError('Wrong existing camera-core regulator')
    if (cells(tree.nodes[CORE]['regulator-min-microvolt'])!=[1000000] or
            cells(tree.nodes[CORE]['regulator-max-microvolt'])!=[1200000] or
            'phandle' in tree.nodes[CORE]):
        raise ValueError('Existing camera-core use or range changed')
    fresh=max(tree.phandles)+1
    if fresh>=0xffffff00:raise ValueError('No bounded phandle space')
    def phandle(path):
        nonlocal fresh
        if 'phandle' not in tree.nodes[path]:
            tree.nodes[path]['phandle']=u32(fresh);fresh+=1
        return cells(tree.nodes[path]['phandle'])[0]
    def add(path,properties):
        if path in tree.nodes:raise ValueError('Camera node already exists')
        tree.nodes[path]=properties
        return phandle(path)
    cc=phandle(CAMCC);mclk=phandle(MCLK);gpio=phandle(TLMM);core=phandle(CORE)
    # CORE had no consumer/phandle in the qualified tree. Keep this restriction
    # so tightening it for the stock1.0V camera vote cannot affect other users.
    tree.nodes[CORE]['regulator-max-microvolt']=u32(1000000)
    tree.nodes[BANK]={'compatible':text('qcom,pm8010-rpmh-regulators'),'qcom,pmic-id':text('m')}
    rails={}
    for number,voltage in ((4,2200000),(5,1800000),(7,2800000)):
        rails[number]=add(BANK+'/ldo'+str(number),{
            'regulator-name':text('oneplus13-camera-l'+str(number)+'m'),
            'regulator-min-microvolt':u32(voltage),'regulator-max-microvolt':u32(voltage),
            'regulator-initial-mode':tree.nodes[CORE]['regulator-initial-mode']})
    active=add(TLMM+'/cci0-probe-active-state',{})
    sleep=add(TLMM+'/cci0-probe-sleep-state',{})
    for parent,bias,function_sda,function_scl in (
            ('cci0-probe-active-state','bias-pull-up','cci_i2c_sda','cci_i2c_scl'),
            ('cci0-probe-sleep-state','bias-pull-down','gpio','gpio')):
        for name,pin,function in (('sda-pins',113,function_sda),('scl-pins',114,function_scl)):
            tree.nodes[TLMM+'/'+parent+'/'+name]={
                'pins':text('gpio'+str(pin)),'function':text(function),
                'drive-strength':u32(2),bias:b''}
    sensor_pin=add(TLMM+'/ultrawide-probe-active-state',{})
    tree.nodes[TLMM+'/ultrawide-probe-active-state/mclk-pins']={
        'pins':text('gpio89'),'function':text('cam_mclk'),'drive-strength':u32(2),'bias-disable':b''}
    tree.nodes[TLMM+'/ultrawide-probe-active-state/reset-pins']={
        'pins':text('gpio178'),'function':text('gpio'),'drive-strength':u32(2),'bias-pull-down':b''}
    add(CCI,{'compatible':text('qcom,sm8750-cci','qcom,msm8996-cci'),
             'reg':u32(0,0x0ac7b000,0,0x1000),'interrupts':u32(0,426,1),
             'power-domains':u32(cc,0),'clocks':u32(cc,0,cc,19),
             'clock-names':text('ahb','cci'),'pinctrl-0':u32(active),'pinctrl-1':u32(sleep),
             'pinctrl-names':text('default','sleep'),'status':text('okay'),
             '#address-cells':u32(1),'#size-cells':u32(0)})
    tree.nodes[CCI+'/i2c-bus@0']={'reg':u32(0),'clock-frequency':u32(100000),
        '#address-cells':u32(1),'#size-cells':u32(0)}
    tree.nodes[SENSOR]={'compatible':text('samsung,s5kjn5'),'reg':u32(0x2d),
        'clocks':u32(mclk,0),'assigned-clocks':u32(mclk,0),'assigned-clock-rates':u32(19200000),
        'reset-gpios':u32(gpio,178,1),'pinctrl-0':u32(sensor_pin),'pinctrl-names':text('default'),
        'vddio-supply':u32(rails[5]),'vddd-supply':u32(core),'vddm-supply':u32(core),
        'vdda-supply':u32(rails[4]),'afvdd-supply':u32(rails[7]),'status':text('okay')}
    tree.nodes[SENSOR+'/port']={}
    tree.nodes[SENSOR+'/port/endpoint']={'data-lanes':u32(1,2,3,4),
        'link-frequencies':u32(0,1248000000)}
    if 'qcom,geni-i2c' not in strings(tree.nodes[NFC_I2C]['compatible']):
        raise ValueError('Wrong NFC I2C provider')
    irq=add(TLMM+'/nfc-irq-active-state',{})
    enable=add(TLMM+'/nfc-enable-active-state',{})
    sleep_nfc=add(TLMM+'/nfc-sleep-state',{})
    tree.nodes[TLMM+'/nfc-irq-active-state/irq-pins']={
        'pins':text('gpio75'),'function':text('gpio'),'drive-strength':u32(2),'bias-pull-down':b''}
    tree.nodes[TLMM+'/nfc-enable-active-state/enable-pins']={
        'pins':text('gpio34','gpio35'),'function':text('gpio'),'drive-strength':u32(2),'bias-disable':b''}
    tree.nodes[TLMM+'/nfc-sleep-state/sleep-pins']={
        'pins':text('gpio34','gpio35'),'function':text('gpio'),'drive-strength':u32(2),'bias-disable':b''}
    tree.nodes[NFC_I2C]['status']=text('okay')
    tree.nodes[NFC_I2C]['clock-frequency']=u32(400000)
    add(NFC,{'compatible':text('nxp,nxp-nci-i2c'),'reg':u32(0x28),
        'interrupt-parent':u32(gpio),'interrupts':u32(75,0),'wakeup-source':b'',
        'enable-gpios':u32(gpio,34,0),'pinctrl-0':u32(irq,enable),
        'pinctrl-1':u32(sleep_nfc),'pinctrl-names':text('default','sleep'),'status':text('okay')})
    changed={path for path in before if before[path]!=tree.nodes[path]}
    if changed!={CAMCC,MCLK,CORE,NFC_I2C}:raise ValueError('Unexpected existing-node mutation')
    new=set(tree.nodes)-set(before)
    allowed=(CCI,BANK,TLMM+'/cci0-probe-',TLMM+'/ultrawide-probe-',TLMM+'/nfc-',NFC)
    if any(not any(path.startswith(prefix) for prefix in allowed) for path in new):
        raise ValueError('Unexpected new camera node')
    final=tree.to_blob();check=Fdt(final)
    if check.nodes!=tree.nodes or check.reservations!=tree.reservations:
        raise ValueError('Camera chip-ID tree readback mismatch')
    return final,{'scope':'SEPARATE_MANUAL_CCI_S5KJN5_CHIP_ID_RAM_TREE',
        'base_sha256':hashlib.sha256(blob).hexdigest(),'output_sha256':hashlib.sha256(final).hexdigest(),
        'microphone_derivation':microphone,'additional_changed_nodes':sorted(changed),
        'additional_nodes':sorted(new),'cci_i2c_address':45,'cci_master':0,'reset_gpio':178,
        'cci_i2c_address_source':'C93_ULTRAWIDE_D_L_S_SLAVEINFO_8BIT_0X5A',
        'camera_core_microvolt':1000000,'camera_analog_microvolt':2200000,
        'camera_io_microvolt':1800000,'camera_af_microvolt':2800000,'mclk_hz':19200000,
        'vddm_shared_core_vote_is_inferred':True,'optional_1p2_io_rail_absent':True,
        'regulator_inputs_firmware_managed_no_parent_assumed':True,
        'camss_or_phy_enabled':False,'capture_pipeline_present':False,
        'sensor_driver_has_streaming_callbacks':True,
        'physical_power_sequence_qualified':False,'camera_capture_qualified':False,
        'requires_manual_sensor_and_cci_module_policy':True,
        'nfc_candidate':{'i2c':NFC_I2C,'address':0x28,'irq_gpio':75,
            'enable_gpio':34,'driver':'nxp,nxp-nci-i2c','hardware_qualified':False},
        'default_boot_tree_changed':False}
