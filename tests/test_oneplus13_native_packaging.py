# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import unittest

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_packaging',REPO/'tools/oneplus13/package-desktop.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class NativePackagingEvidenceTests(unittest.TestCase):
    def test_requires_the_exact_reviewed_kernel_rootfs_and_helper(self):
        kernel={'fixed_phy_version':1,'host_power_resources_version':1,'dsi_batch_version':1,'command_budget_version':3,
                'integration_commit':'kernel'}
        desktop={'qemu_native_preflight_rejects_public_root_verified':True,
                 'qemu_native_preflight_runtime_guards_verified':True,
                 'native_desktop_helper_sha256':'helper','integration_commit':'desktop','rootfs_sha256':'rootfs'}
        evidence={'eligible_for_offline_native_packaging':True,'hardware_tested':False,'phone_operations':0,
                  'kernel_commit':'kernel','desktop_commit':'desktop','rootfs_sha256':'rootfs',
                  'native_desktop_helper_sha256':'helper','kernel_download_verified':True,
                  'clock_te_asan_ubsan_verified':True,'native_preflight_arm64_tests_verified':True,
                  'desktop_download_verified':True,'actual_dtb_opp_budget_verified':True}
        module.check_native_offline_evidence(kernel,desktop,evidence,'helper')
        for target in [kernel,desktop,evidence]:
            for key in target:
                changed=dict(target);changed[key]=None
                with self.subTest(key=key),self.assertRaises(ValueError):
                    module.check_native_offline_evidence(changed if target is kernel else kernel,
                        changed if target is desktop else desktop,changed if target is evidence else evidence,'helper')
