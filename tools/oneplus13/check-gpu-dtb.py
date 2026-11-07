#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Validate isolated GPU graph; compilation never establishes rendering."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
from stock import Fdt, cells

ZAP = b'qcom/sm8750/oneplus/dodge/gen80000_zap.mbn\0'


def inspect(blob):
    tree = Fdt(blob)

    def enabled(path):
        return all(tree.nodes.get('/'.join(path.split('/')[:i]) or '/', {}).get('status', b'okay\0')
                   != b'disabled\0' for i in range(1, len(path.split('/'))+1))

    def find(compatible):
        found = [p for p, v in tree.nodes.items() if compatible in
                 v.get('compatible', b'').split(b'\0') and enabled(p)]
        if len(found) != 1:
            raise ValueError('Expected one enabled '+compatible.decode())
        return found[0]

    def ref(path, prop):
        value = cells(tree.nodes[path][prop])
        if len(value) != 1 or value[0] not in tree.phandles:
            raise ValueError('Invalid graph reference: '+prop)
        return tree.phandles[value[0]]

    if tree.nodes['/']['compatible'].split(b'\0')[:2] != [b'oneplus,dodge', b'qcom,sm8750']:
        raise ValueError('Wrong phone')
    gpu = find(b'qcom,adreno-44050001')
    gmu = find(b'qcom,adreno-gmu-830.1')
    smmu = find(b'qcom,adreno-smmu')
    if not gpu.endswith('/gpu@3d00000') or not gmu.endswith('/gmu@3d37000') or not smmu.endswith('/iommu@3da0000'):
        raise ValueError('Wrong GPU/GMU/SMMU instance')
    if ref(gpu, 'qcom,gmu') != gmu:
        raise ValueError('Wrong GMU reference')
    if cells(tree.nodes[gpu]['reg']) != [0, 0x3d00000, 0, 0x6d000, 0, 0x3d9e000, 0, 0x2000]:
        raise ValueError('Wrong GPU register regions')
    if cells(tree.nodes[gmu]['reg']) != [0, 0x3d37000, 0, 0x68000]:
        raise ValueError('Wrong GMU register region')
    sid = cells(tree.nodes[smmu]['phandle'])[0]
    if (cells(tree.nodes[gpu].get('iommus', b'')) != [sid, 0, 0, sid, 1, 0]
            or cells(tree.nodes[gmu].get('iommus', b'')) != [sid, 5, 0]):
        raise ValueError('Normal GPU/GMU SMMU mapping is required')
    if b'iommu.passthrough' in tree.nodes['/chosen'].get('bootargs', b''):
        raise ValueError('IOMMU passthrough is forbidden')
    if tree.nodes[gpu].get('nvmem-cell-names') != b'speed_bin\0' or not tree.nodes[gpu].get('nvmem-cells'):
        raise ValueError('Speed bin must not be bypassed')
    speed = ref(gpu, 'nvmem-cells')
    if 'reg' not in tree.nodes[speed]:
        raise ValueError('Missing speed-bin source')
    zap = gpu+'/zap-shader'
    if tree.nodes[zap].get('firmware-name') != ZAP:
        raise ValueError('Wrong phone-specific zap path')
    memory = ref(zap, 'memory-region')
    if (cells(tree.nodes[memory]['reg']) != [0, 0x9b09a000, 0, 0x2000]
            or 'no-map' not in tree.nodes[memory]):
        raise ValueError('Stock GPU microcode reservation changed')
    for path, props in tree.nodes.items():
        if enabled(path) and any(c in props.get('compatible', b'').split(b'\0') for c in
                [b'qcom,sm8750-mdss', b'oneplus,dodge-aa569', b'oneplus,dodge-s3910-lbp',
                 b'qcom,wcn7860-pmu', b'pci17cb,110e', b'qcom,wcn7860-bt', b'qcom,pcie-sm8750']):
            raise ValueError('GPU candidate must remain isolated')
    for path in [gpu, gmu]:
        opp = ref(path, 'operating-points-v2')
        if not any(p.startswith(opp+'/') and 'opp-hz' in v for p, v in tree.nodes.items()):
            raise ValueError('Missing platform operating points')
    return {'scope': 'COMPILED_GPU_GRAPH_ONLY', 'hardware_tested': False,
            'dtb_sha256': hashlib.sha256(blob).hexdigest(), 'chip_id': '0x44050001',
            'gpu_gmu_smmu_graph_checked': True, 'stock_microcode_reservation_checked': True,
            'speed_bin_and_platform_opps_retained': True, 'display_touch_radio_absent': True,
            'phone_specific_zap_path': ZAP[:-1].decode(), 'contains_firmware': False,
            'owner_firmware_matched': False, 'physical_renderer_verified': False,
            'platform_clock_power_opp_electrically_verified': False,
            'headless_requires_explicit_msm_separate_gpu_kms': True}


def negative_controls(blob):
    t = Fdt(blob)
    gpu = next(p for p, v in t.nodes.items() if b'qcom,adreno-44050001\0' in v.get('compatible', b''))
    mdss = next(p for p, v in t.nodes.items() if b'qcom,sm8750-mdss\0' in v.get('compatible', b''))
    memory = t.phandles[cells(t.nodes[gpu+'/zap-shader']['memory-region'])[0]]
    controls = [(gpu, 'compatible', b'qcom,adreno-44050000\0qcom,adreno\0'),
                (gpu+'/zap-shader', 'firmware-name', b'qcom/sm8750/erhai/gen80000_zap.mbn\0'),
                (gpu, 'iommus', b''), (mdss, 'status', b'okay\0'),
                (memory, 'reg', struct.pack('>4I', 0, 0x9b09b000, 0, 0x2000))]
    for path, prop, value in controls:
        tree = Fdt(blob)
        tree.nodes[path][prop] = value
        try:
            inspect(tree.to_blob())
        except ValueError:
            continue
        raise ValueError('GPU negative control accepted: '+prop)
    return len(controls)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dtb', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--kernel-source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    blob = a.dtb.read_bytes()
    report = inspect(blob)
    config = set(a.config.read_text().splitlines())
    if 'CONFIG_DRM_MSM=m' not in config or 'CONFIG_SM_GPUCC_8750=y' not in config:
        raise ValueError('Expected modular render driver and platform GPU clock')
    catalog = (a.kernel_source/'drivers/gpu/drm/msm/adreno/a6xx_catalog.c').read_text()
    entry = catalog.split('static const struct adreno_info a8xx_gpus[] = {', 1)[1].split('.funcs', 1)[0]
    if ('ADRENO_CHIP_IDS(0x44050001)' not in entry or any(name not in entry for name in
            ['gen80000_sqe.fw', 'gen80000_gmu.bin', 'gen80000_aqe.fw'])):
        raise ValueError('Driver catalog does not match the phone')
    drv = (a.kernel_source/'drivers/gpu/drm/msm/msm_drv.c').read_text()
    if not re.search(r'bool msm_gpu_no_components\(void\)\s*\{\s*return separate_gpu_kms;\s*\}', drv):
        raise ValueError('Re-review headless GPU binding for this source')
    report.update(negative_controls_rejected=negative_controls(blob), render_driver_modular=True,
                  catalog_sha256=hashlib.sha256(catalog.encode()).hexdigest(),
                  msm_driver_sha256=hashlib.sha256(drv.encode()).hexdigest())
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2)+'\n')
    print('GPU graph checked; signed firmware and actual rendering remain unverified.')


if __name__ == '__main__':
    main()
