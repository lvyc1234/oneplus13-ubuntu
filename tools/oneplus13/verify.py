#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Inspect compiled outputs; QEMU proves userspace only, never phone hardware."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import stat
import tarfile
import importlib

from common import boot_sections, read_cpio, sha256

REQUIRED = ['ARCH_QCOM', 'ARCH_VEXPRESS', 'BLK_DEV_INITRD', 'RD_GZIP',
            'DEVTMPFS', 'DEVTMPFS_MOUNT', 'TMPFS', 'CONFIGFS_FS', 'USB_GADGET',
            'USB_CONFIGFS', 'USB_CONFIGFS_NCM', 'USB_CONFIGFS_ACM', 'USB_DWC3', 'USB_DWC3_QCOM',
            'PHY_QCOM_M31_EUSB', 'PHY_QCOM_EUSB2_REPEATER', 'PINCTRL_SM8750',
            'SM_GCC_8750', 'SM_TCSRCC_8750', 'REGULATOR_QCOM_RPMH',
            'QCOM_COMMAND_DB', 'QCOM_RPMH', 'QCOM_SMEM', 'QCOM_RPMHPD',
            'INTERCONNECT_QCOM_SM8750', 'SCSI_UFS_QCOM', 'PHY_QCOM_QMP_UFS',
            'RC_CORE', 'LIRC']


def check_config(path):
    importlib.import_module('container-kernel').check_config(path.read_text())
    settings = set(path.read_text().splitlines())
    missing = [name for name in REQUIRED if f'CONFIG_{name}=y' not in settings]
    if missing:
        raise ValueError(f'Required early-boot drivers are not built in: {missing}')
    unrelated = ['ARCH_APPLE', 'ARCH_MEDIATEK', 'ARCH_ROCKCHIP', 'ARCH_TEGRA',
                 'ARCH_EXYNOS', 'ARCH_RENESAS', 'ARCH_MESON', 'ARCH_SUNXI']
    enabled = [name for name in unrelated if f'CONFIG_{name}=y' in settings]
    if enabled:
        raise ValueError(f'Unrelated platform families enabled by mistake: {enabled}')


def check_module_indexes(archive, release):
    prefix = 'lib/modules/' + release + '/'
    members = {m.name.removeprefix('./'): m for m in archive.getmembers()}
    for name in ['modules.dep', 'modules.dep.bin', 'modules.alias', 'modules.alias.bin']:
        item = members.get(prefix + name)
        if item is None or not item.isfile() or not 0 < item.size <= 16 * 1024 * 1024:
            raise ValueError('Missing usable module index: ' + name)
    payloads = {name[len(prefix):] for name, item in members.items()
                if name.startswith(prefix) and item.isfile() and
                name.endswith(('.ko', '.ko.zst', '.ko.xz', '.ko.gz'))}
    rows = {}
    for line in archive.extractfile(members[prefix + 'modules.dep']).read().decode().splitlines():
        key, separator, dependencies = line.partition(':')
        if not separator or key in rows or key not in payloads:
            raise ValueError('Invalid module dependency index')
        rows[key] = dependencies.split()
        if not set(rows[key]) <= payloads:
            raise ValueError('Module dependency points outside the delivered payload')
    if not payloads or set(rows) != payloads:
        raise ValueError('Module dependency index does not cover every delivered module')


def check_cci_report(out, manifest):
    if manifest.get('cci_clock_version') != 1:
        raise ValueError('Unknown CCI verification version')
    lock = importlib.import_module('check-cci').load_inventory()
    if json.loads((out / 'cci-upstream-source.json').read_text()) != lock:
        raise ValueError('Packaged CCI provenance differs from source')
    report = json.loads((out / 'cci-clock-verification.json').read_text())
    expected = {'scope':'OFFLINE_ACTUAL_CCI_C_FUNCTIONS', 'hardware_tested':False,
                'pass':True, 'sanitizers':True, 'pinned_base_patch_roundtrip':True,
                'kernel_commit':manifest['kernel_commit'],
                'patch_sha256':lock['patch_sha256'], 'generated_files':lock['generated_files'],
                'sm8750_required_rate_hz':37500000, 'unknown_rates_rejected':True,
                'legacy_19_2mhz_controls':True, 'mixed_master_modes_checked':True,
                'opp_failure_propagated':True, 'clock_stretch_bit_checked':True}
    if manifest['kernel_commit'] != lock['kernel_pin'] or any(
            report.get(k) != v for k, v in expected.items()):
        raise ValueError('Missing or failed actual CCI C verification')


def verify_artifacts(out):
    manifest = json.loads((out / 'manifest.json').read_text())
    if manifest['device'] != 'oneplus,dodge' or manifest['hardware_tested'] is not False:
        raise ValueError('Wrong device or unsupported hardware claim')
    for name, expected in manifest['files'].items():
        if Path(name).name != name or name in {'.', '..'}:
            raise ValueError('Unsafe manifest filename')
        path = out / name
        if path.stat().st_size != expected['size'] or sha256(path) != expected['sha256']:
            raise ValueError(f'Artifact hash/size mismatch: {name}')
    if 'display_support' in manifest:
        if manifest['display_support'] not in {'UNIMPLEMENTED_OFFLINE_PARAMETERS_ONLY',
                'EXPERIMENTAL_NATIVE_STATIC_SPR_NOT_DEVICE_TESTED'}:
            raise ValueError('Unsupported native-display claim')
        if (manifest['display_support'] == 'EXPERIMENTAL_NATIVE_STATIC_SPR_NOT_DEVICE_TESTED' and
                manifest.get('native_display_candidate_version') != 1):
            raise ValueError('Missing separate native candidate evidence')
        display = json.loads((out / 'display-conversion-verification.json').read_text())
        expected = {'scope': 'HOST_C_PPS_ONLY', 'hardware_tested': False,
                    'kernel_commit': manifest['kernel_commit'], 'matched_payload_bytes': 94,
                    'matched_zero_padding_bytes': 34, 'physical_bpp_q4': 128,
                    'encoded_bpp_q4': 256, 'wrong_bpp_negative_control_rejected': True,
                    'sanitizers': True}
        if manifest.get('display_conversion_version', 1) == 2:
            expected.update(scope='HOST_C_PPS_AND_DSI_RC_ONLY',
                            native422_from_minimal_panel_matches_all_pps_bytes=True,
                            baseline_native422_negative_control_rejected=True,
                            rgb_baseline_control_match=True,
                            invalid_native422_profiles_rejected=4)
        elif manifest.get('display_conversion_version', 1) != 1:
            raise ValueError('Unknown display-conversion evidence version')
        if any(display.get(k) != v for k, v in expected.items()):
            raise ValueError('Missing or failed C DSC conversion verification')
    if 'command_clock_version' in manifest:
        clock = json.loads((out / 'command-clock-verification.json').read_text())
        expected = {'scope': 'HOST_C_DSI_COMMAND_CLOCK_ONLY', 'hardware_tested': False,
                    'kernel_commit': manifest['kernel_commit'], 'sanitizers': True,
                    'lane_bit_rate': 1363200000, 'byte_clk_rate': 170400000,
                    'pixel_clk_rate': 181760000, 'phy_request_matches_byte_clock': True,
                    'legacy_baseline_controls': 6, 'invalid_controls_rejected': 10,
                    'non_byte_aligned_ceiling_rounded_down': True,
                    'dpu_transfer_window_programmed': False, 'pll_rounding_verified': False}
        if (manifest['command_clock_version'] != 1 or
                any(clock.get(k) != v for k, v in expected.items())):
            raise ValueError('Missing or failed C command-clock verification')
    if 'command_budget_version' in manifest:
        budget = json.loads((out / 'command-budget-verification.json').read_text())
        expected = {'scope': 'HOST_C_DPU_COMMAND_BUDGET_ONLY', 'hardware_tested': False,
                    'kernel_commit': manifest['kernel_commit'], 'sanitizers': True,
                    'nominal_refresh_hz': 120, 'stock_budget_us': 6800,
                    'ceiling_multiplier': [125, 102], 'arithmetic_vectors': 261,
                    'overflow_peak_opp_zero_limit_rejected': 5,
                    'fresh_enable_new_state_limits_checked': True,
                    'baseline_underbudget_rejected': True,
                    'nonoptin_legacy_modes_unchanged': 3, 'icc_error_positions': 2,
                    'icc_per_path_average_ceiling': True, 'missing_icc_path_rejected': True,
                    'te_timing_changed': False, 'hardware_transfer_timer_programmed': False,
                    'exact_vendor_hal_algorithm': False}
        if manifest['command_budget_version'] in [2, 3]:
            expected.update(new_atomic_plane_geometry_checked=True, original_plane_math_unchanged=True)
        elif manifest['command_budget_version'] != 1:
            raise ValueError('Unknown command-budget evidence version')
        if manifest['command_budget_version'] == 3:
            expected.update(actual_native_dtb_sha256=manifest['files']['sm8750-oneplus-dodge-native.dtb']['sha256'],
                            actual_mdp_opp_max_hz=575000000,actual_dtb_opp_mode_eligible=True,
                            fixed_two_mixer_clock_hz=355764706,full_width_old_budget_rejected=True,
                            actual_plane_split_executed=True,pipe_limit_rounding_boundaries=3417,
                            native_dtb_negative_controls=6,
                            plane_geometry_fixture='NO_ROTATION_SCALING_OR_CLIPPING')
        if (
                any(budget.get(k) != v for k, v in expected.items())):
            raise ValueError('Missing or failed C DPU command-budget verification')
    if 'panel_component_version' in manifest:
        panel = json.loads((out / 'panel-component-verification.json').read_text())
        expected = {'scope': 'HOST_C_PANEL_COMPONENT_ONLY', 'hardware_tested': False,
                    'kernel_commit': manifest['kernel_commit'], 'sanitizers': True,
                    'public_on_packets_match': 150, 'public_off_packets_match': 3,
                    'fixed_brightness_dbv': 1433, 'on_lp_off_and_brightness_hs': True,
                    'power_reset_delays_match': True, 'supply_failure_controls': 8,
                    'disable_enable_cycle_checked': True,
                    'off_command_failure_positions_checked': 3,
                    'off_command_errors_do_not_wedge_framework_state': True}
        if manifest['panel_component_version'] == 1:
            expected.update(enabled_in_any_dtb=False, spr2_pipeline_implemented=False)
        elif manifest['panel_component_version'] in (2, 3):
            expected.update(separate_native_dtb_source_present=True,
                            board_node_exercised_by_this_check=False,
                            spr_runtime_exercised_by_this_check=False)
        else:
            raise ValueError('Unknown panel component evidence version')
        if manifest['panel_component_version'] < 3:
            expected.update(init_failure_positions_checked=149, short_transfer_rejected=True)
        else:
            expected.update(init_dma_failure_groups_checked=12, on_vendor_groups_match=13,
                            allocation_and_split_group_controls=True)
            if manifest.get('dsi_batch_version') != 1:
                raise ValueError('Grouped panel requires DSI batch evidence')
        if (
                any(panel.get(k) != v for k, v in expected.items())):
            raise ValueError('Missing or failed C panel component verification')
    if 'dsi_batch_version' in manifest:
        batch = json.loads((out / 'dsi-batch-verification.json').read_text())
        expected = {'scope':'HOST_C_DSI_BATCH_ONLY', 'hardware_tested':False,
                    'sanitizers':True, 'kernel_commit':manifest['kernel_commit'],
                    'on_vendor_groups_match':13, 'on_packets':150, 'max_group_bytes':312,
                    'invalid_groups_fail_before_mapping_or_pm':True,
                    'pm_parent_rate_and_enable_failures_balanced':True,
                    'one_lock_mapping_resource_lifetime_and_kickoff_per_group':True,
                    'dma_timeout_reset_before_restore':True, 'no_queued_state_after_return':True,
                    'synchronous_component_attach_and_failure_cleanup_checked':True,
                    'ordinary_packing_dispatch_and_manager_trigger_unchanged':True}
        if manifest['dsi_batch_version'] != 1 or any(batch.get(k) != v for k,v in expected.items()):
            raise ValueError('Missing or failed C DSI batch verification')
    if 'fixed_phy_version' in manifest:
        phy = json.loads((out / 'fixed-phy-verification.json').read_text())
        expected = {'scope':'HOST_C_FIXED_AA569_PHY_ONLY','hardware_tested':False,
                    'sanitizers':True,'kernel_commit':manifest['kernel_commit'],
                    'production_timing_bytes':[0,44,12,12,29,26,12,12,11,2,4,0,36,17],
                    'real_mmio_writers_match_public_profile':True,'rejected_profiles_before_mmio':7,
                    'legacy_profiles_unchanged':33,'shared_host_timing_updated':True,
                    'all_rejection_outputs_unchanged':True,'selector_called_before_any_phy_mmio':True}
        if manifest['fixed_phy_version'] != 1 or any(phy.get(k) != v for k,v in expected.items()):
            raise ValueError('Missing or failed fixed AA569 PHY verification')
    if 'host_power_resources_version' in manifest:
        batch = json.loads((out / 'dsi-batch-verification.json').read_text())
        if (manifest['host_power_resources_version'] != 1 or batch.get('sanitizers') is not True or
                batch.get('host_power_pm_clock_and_pinctrl_failures_balanced') is not True):
            raise ValueError('Missing host power-on resource failure verification')
    if 'dpu_buffer_version' in manifest:
        dpu = json.loads((out / 'dpu-buffer-verification.json').read_text())
        expected = {'scope': 'HOST_C_DPU_BUFFER_AND_REGISTER_ONLY',
                    'hardware_tested': False, 'sanitizers': True,
                    'kernel_commit': manifest['kernel_commit'],
                    'native422_vendor_math_cases': 48, 'rgb_baseline_math_cases': 48,
                    'aa569_initial_lines': 4, 'baseline_initial_lines': 3,
                    'physical_bpp_q4': 128, 'hardware_bpp_field_q4': 256,
                    'full_ich_precision_capability_checked': True,
                    'modern_native422_wrapper_bit17_cleared': True,
                    'legacy_ctl_bounds_checked': True,
                    'rgb8_register_baseline_match': True,
                    'ppb_fifo_entries_per_720px_pipe': 720,
                    'ppb_pm_restore_call_present': True}
        if (manifest['dpu_buffer_version'] != 1 or
                any(dpu.get(k) != v for k, v in expected.items())):
            raise ValueError('Missing or failed C DPU buffer verification')
    if 'spr_profile_version' in manifest:
        spr = json.loads((out / 'spr-profile-verification.json').read_text())
        expected = {'scope': 'HOST_C_SPR_PROFILE_AND_TRANSACTION_PLAN_ONLY',
                    'hardware_tested': False, 'sanitizers': True,
                    'synthetic_profiles': 9, 'private_profile_checked': False,
                    'profile_size': 176, 'truncated_lengths_rejected': 176,
                    'single_byte_corruptions_rejected': 176,
                    'reserved_word_cases_rejected': 34,
                    'semantic_invalid_cases_rejected': 5,
                    'unaligned_inputs_checked': 4,
                    'oversized_input_rejected': True, 'failure_outputs_unchanged': True,
                    'udc_bit25_preserved_in_plan': True,
                    'typed_direct_init_pu_paths': True, 'legacy_cfg14_write_absent': True,
                    'mmio_or_dma_submitted': False}
        if manifest['spr_profile_version'] == 1:
            expected['runtime_registered'] = False
        elif manifest['spr_profile_version'] == 2:
            expected['runtime_exercised_by_this_check'] = False
        else:
            raise ValueError('Unknown SPR profile evidence version')
        if (
                any(spr.get(k) != v for k, v in expected.items())):
            raise ValueError('Missing or failed C SPR profile verification')
    if 'spr_runtime_version' in manifest:
        runtime = json.loads((out / 'spr-runtime-verification.json').read_text())
        expected = {'scope': 'HOST_C_STATIC_SPR_RUNTIME_ONLY', 'hardware_tested': False,
                    'kernel_commit': manifest['kernel_commit'], 'sanitizers': True,
                    'backend': 'QUIESCENT_FIXED_PROFILE_MMIO_NO_DMA',
                    'all_pipe_udc_preflight_before_any_write': True,
                    'powered_idle_guards': True,
                    'physical_spr_addresses': ['0xae6a400', '0xae6b400', '0xae6c400', '0xae6d400'],
                    'profile_missing_or_invalid_rejected': True, 'optin_absent_noop': True,
                    'pm_restore_cycles': 3, 'aa569_format_exclusivity_checked': True,
                    'spr_flush_bit': 8, 'reset_errors_propagated': True,
                    'restore_before_encoder_restore': True, 'disable_before_clocks_off': True,
                    'resource_reservation_without_color_properties': True,
                    'dma_buffers_or_submissions': False}
        if (manifest['spr_runtime_version'] != 1 or
                any(runtime.get(k) != v for k, v in expected.items())):
            raise ValueError('Missing or failed C static SPR runtime verification')
    if 'native_display_candidate_version' in manifest:
        native = json.loads((out / 'native-dtb-verification.json').read_text())
        expected = {'scope': 'COMPILED_NATIVE_DTB_ONLY', 'hardware_tested': False,
                    'dtb_sha256': sha256(out / 'sm8750-oneplus-dodge-native.dtb'),
                    'mdss_reset_and_static_spr_optin': True, 'single_dsi0_aa569': True,
                    'supplies_gpio_te_and_endpoints_checked': True,
                    'vddr_gpio7_sid5_range8': True, 'display_only_touch_absent': True,
                    'private_spr_profile_in_dtb': False, 'negative_controls_rejected': 7}
        if (manifest['native_display_candidate_version'] != 1 or
                manifest.get('spr_runtime_version') != 1 or
                any(native.get(k) != v for k, v in expected.items())):
            raise ValueError('Missing or failed native DTB verification')
    if 'wlan_wiring_version' in manifest:
        wlan = json.loads((out / 'wlan-dtb-verification.json').read_text())
        expected = {'scope': 'COMPILED_WLAN_WIRING_ONLY', 'hardware_tested': False,
                    'dtb_sha256': sha256(out / 'sm8750-oneplus-dodge-wlan.dtb'),
                    'phone_six_rail_constraints': True, 'enable_pmic_gpio_and_pcie0_checked': True,
                    'pmu_output_graph_checked': True, 'display_touch_bluetooth_absent': True,
                    'wlan_pin16_16ma_pullup': True, 'firmware_board_revision_sar_verified': False,
                    'phy_rails_and_rf_clock_reference_only': True,
                    'contains_owner_address_or_calibration': False,
                    'negative_controls_rejected': 6, 'rf_stack_modular': True}
        if (manifest['wlan_wiring_version'] != 1 or
                any(wlan.get(k) != v for k, v in expected.items())):
            raise ValueError('Missing or failed WLAN wiring verification')
    if 'gpu_graph_version' in manifest:
        gpu = json.loads((out / 'gpu-dtb-verification.json').read_text())
        expected = {'scope': 'COMPILED_GPU_GRAPH_ONLY', 'hardware_tested': False,
                    'dtb_sha256': sha256(out / 'sm8750-oneplus-dodge-gpu.dtb'),
                    'chip_id': '0x44050001', 'gpu_gmu_smmu_graph_checked': True,
                    'stock_microcode_reservation_checked': True,
                    'speed_bin_and_platform_opps_retained': True, 'display_touch_radio_absent': True,
                    'phone_specific_zap_path': 'qcom/sm8750/oneplus/dodge/gen80000_zap.mbn',
                    'contains_firmware': False, 'owner_firmware_matched': False,
                    'physical_renderer_verified': False,
                    'platform_clock_power_opp_electrically_verified': False,
                    'headless_requires_explicit_msm_separate_gpu_kms': True,
                    'negative_controls_rejected': 5, 'render_driver_modular': True}
        if manifest['gpu_graph_version'] != 1 or any(gpu.get(k) != v for k, v in expected.items()):
            raise ValueError('Missing or failed isolated GPU graph verification')
    if 'cci_clock_version' in manifest:
        check_cci_report(out, manifest)
    check_config(out / 'kernel.config')
    image = (out / 'Image').read_bytes()
    if len(image) < 64 or image[56:60] != b'ARM\x64':
        raise ValueError('Invalid ARM64 Image magic')
    if gzip.decompress((out / 'Image.gz').read_bytes()) != image:
        raise ValueError('Compressed Image differs from Image')
    sections = boot_sections((out / 'boot-oneplus13-ram-experimental.img').read_bytes())
    for part, filename in [('kernel', 'Image.gz'), ('ramdisk', 'initramfs.cpio.gz'),
                           ('dtb', 'sm8750-oneplus-dodge.dtb')]:
        if sections[part] != (out / filename).read_bytes():
            raise ValueError(f'Boot image section differs: {part}')
    cpio = read_cpio(gzip.decompress(sections['ramdisk']))
    if hashlib.sha256(cpio['init']['data']).hexdigest() != manifest['phone_inputs_sha256']['init']:
        raise ValueError('Packaged init differs from phone source')
    if not cpio['init']['mode'] & stat.S_IXUSR:
        raise ValueError('Packaged init is not executable')
    if hashlib.sha256(cpio['bin/busybox']['data']).hexdigest() != manifest['tools_sha256']['busybox']:
        raise ValueError('Packaged BusyBox differs from pinned binary')
    console = cpio['dev/console']
    if stat.S_IFMT(console['mode']) != stat.S_IFCHR or (console['major'], console['minor']) != (5, 1):
        raise ValueError('Wrong console device in initramfs')
    with tarfile.open(out / 'modules.tar.gz') as archive:
        check_module_indexes(archive, manifest['kernel_release'])
        importlib.import_module('container-kernel').check_payload(
            (out / 'kernel.config').read_text(), archive, manifest['kernel_release'])
        names = [member.name for member in archive.getmembers()]
        config = (out / 'kernel.config').read_text().splitlines()
        for setting, filename in [('CONFIG_TOUCHSCREEN_DODGE_S3910=m', 'dodge-s3910.ko'),
                                   ('CONFIG_DRM_PANEL_ONEPLUS_AA569_EXPERIMENTAL=m', 'panel-oneplus-aa569.ko'),
                                   ('CONFIG_DRM_MSM=m', 'msm.ko'),
                                   ('CONFIG_ATH12K=m', 'ath12k.ko'),
                                   ('CONFIG_ATH12K=m', 'ath12k_wifi7.ko'),
                                   ('CONFIG_VIDEO_QCOM_CAMSS=m', 'qcom-camss.ko'),
                                   ('CONFIG_VIDEO_S5KJN5=m', 's5kjn5.ko'),
                                   ('CONFIG_I2C_QCOM_CCI=m', 'i2c-qcom-cci.ko'),
                                   ('CONFIG_PHY_QCOM_MIPI_CSI2=m', 'phy-qcom-mipi-csi2.ko'),
                                   ('CONFIG_SM_CAMCC_8750=m', 'camcc-sm8750.ko'),
                                   ('CONFIG_SM_CAMBISTMCLKCC_8750=m', 'cambistmclkcc-sm8750.ko'),
                                   ('CONFIG_IR_DODGE_SPI=m', 'ir-dodge-spi.ko'),
                                   ('CONFIG_PCI_PWRCTRL_PWRSEQ=m', 'pci-pwrctrl-pwrseq.ko'),
                                   ('CONFIG_POWER_SEQUENCING_QCOM_WCN=m', 'pwrseq-qcom-wcn.ko')]:
            if setting in config and not any(Path(name).name in
                    {filename, filename + '.xz', filename + '.zst', filename + '.gz'} for name in names):
                raise ValueError('Configured candidate module is missing: ' + filename)
        if any(Path(name).is_absolute() or '..' in Path(name).parts for name in names):
            raise ValueError('Unsafe module archive path')
        prefix = 'lib/modules/' + manifest['kernel_release'] + '/'
        if not any(name.removeprefix('./').startswith(prefix) and
                   name.endswith(('.ko', '.ko.zst', '.ko.xz', '.ko.gz')) for name in names):
            raise ValueError('Module archive does not match kernel release')
    dtb = out / 'sm8750-oneplus-dodge.dtb'
    compatible = subprocess.check_output(['fdtget', str(dtb), '/', 'compatible'], text=True)
    if compatible.strip().split() != ['oneplus,dodge', 'qcom,sm8750']:
        raise ValueError('Wrong DTB compatible')
    # Re-decompile with an independent dtc invocation: rejects damaged binary trees.
    subprocess.run(['dtc', '-q', '-I', 'dtb', '-O', 'dts', '-o', '/dev/null', str(dtb)], check=True)
    return {'status': 'OFFLINE_VERIFIED_NOT_DEVICE_TESTED', 'hardware_tested': False,
            'kernel_release': manifest['kernel_release'],
            'boot_sha256': sha256(out / 'boot-oneplus13-ram-experimental.img'),
            'checks': ['artifact sizes and SHA256', 'required built-in dependencies',
                       'ARM64 Image magic', 'Image gzip roundtrip', 'Android v2 section roundtrip',
                       'independent cpio inspection: init, BusyBox, console', 'matching module release archive',
                       'DTB identity and independent decompilation']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--artifacts', type=Path)
    parser.add_argument('--qemu', action='store_true')
    args = parser.parse_args()
    if args.config:
        check_config(args.config)
    if args.artifacts:
        out = args.artifacts.resolve()
        result = verify_artifacts(out)
        if args.qemu:
            command = ['qemu-system-aarch64', '-machine', 'virt', '-cpu', 'cortex-a76',
                       '-m', '1024', '-smp', '2', '-nographic', '-no-reboot', '-nic', 'none',
                       '-kernel', str(out / 'Image'), '-initrd', str(out / 'initramfs.cpio.gz'),
                       '-append', 'console=ttyAMA0 rdinit=/init panic=3 oneplus13.smoke=1']
            proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=90, text=True)
            (out / 'qemu.log').write_text(proc.stdout)
            if proc.returncode or 'ONEPLUS13_QEMU_INIT_OK' not in proc.stdout:
                print(proc.stdout[-10000:])
                raise SystemExit('QEMU initramfs smoke test failed')
            result['checks'].append('QEMU virt boots the exact Image/initramfs and reaches init marker')
            result['qemu_scope'] = 'ARM64 userspace only; not an SM8750 hardware emulator'
        (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
        (out / 'SHA256SUMS').write_text(''.join(
            f'{sha256(path)}  {path.name}\n' for path in sorted(out.iterdir())
            if path.is_file() and path.name != 'SHA256SUMS'))
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
