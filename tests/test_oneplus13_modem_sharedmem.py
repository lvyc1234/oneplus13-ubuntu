# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
import hashlib
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ModemSharedmemSource(unittest.TestCase):
    def test_public_provenance_and_cleanup(self):
        source = (ROOT / "tools/oneplus13/modem-draft/msm_sharedmem.c").read_text()
        review = json.loads((ROOT / "tools/oneplus13/modem-draft/sources.json").read_text())
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), review["adapted_sha256"])
        self.assertEqual(review["upstream_blob"], "84dafa706b28edd10051ab3960d67c9ed6105c40")
        self.assertIn("dma_alloc_coherent", source)
        self.assertIn("dma_free_coherent", source)
        self.assertIn("QCOM_SCM_VMID_MSS_MSA", source)
        self.assertIn("QCOM_SCM_VMID_HLOS", source)
        self.assertIn("qcom_scm_assign_mem", source)
        self.assertNotIn("info->mmap =", source)
        self.assertIn("static void msm_sharedmem_remove", source)

    def test_module_and_device_link_are_build_inputs(self):
        config = (ROOT / "devices/oneplus13/oneplus13.config").read_text()
        self.assertIn("CONFIG_UIO=y", config.splitlines())
        self.assertIn("CONFIG_UIO_MSM_SHAREDMEM=m", config.splitlines())
        patch = (ROOT / "devices/oneplus13/kernel-patches/0020-msm-sharedmem.patch").read_text()
        self.assertIn("new file mode 100644", patch)
        self.assertIn("config UIO_MSM_SHAREDMEM", patch)
        self.assertIn("obj-DOLLAR(CONFIG_UIO_MSM_SHAREDMEM)".replace("DOLLAR", chr(36)), patch)
        rules = (ROOT / "devices/oneplus13/rmtfs-uio.rules").read_text()
        self.assertIn('ATTR{name}=="rmtfs"', rules)
        self.assertIn('SYMLINK+="qcom_rmtfs_uio1"', rules)
        wait = (ROOT / "devices/oneplus13/rmtfs-wait.conf").read_text()
        self.assertIn("/dev/qcom_rmtfs_uio1", wait)
        self.assertIn("TimeoutStartSec=30s", wait)


if __name__ == "__main__":
    unittest.main()
