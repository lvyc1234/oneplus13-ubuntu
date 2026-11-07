import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('policy',Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-module-policy.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)

class ModulePolicy(unittest.TestCase):
    def test_peripheral_closure_preserves_transport_and_reviewed_platform(self):
        names=['kernel/sound/soc/qcom/snd-soc-sc8280xp.ko','kernel/drivers/net/wireless/ath/ath12k/ath12k.ko',
               'kernel/drivers/power/sequencing/pwrseq-qcom-wcn.ko','kernel/drivers/pci/pwrctrl/pci-pwrctrl-pwrseq.ko',
               'kernel/drivers/misc/fastrpc.ko','kernel/drivers/media/platform/qcom/iris/qcom-iris.ko',
               'kernel/drivers/nfc/nxp-nci/nxp-nci.ko','kernel/net/nfc/nfc.ko',
               'kernel/drivers/usb/gadget/libcomposite.ko','kernel/drivers/mailbox/qcom-cpucp-mbox.ko',
               'kernel/drivers/soc/qcom/socinfo.ko','kernel/drivers/input/touchscreen/dodge-s3910.ko']
        found=policy.blocked_modules(['./lib/modules/reviewed/'+x for x in names],'reviewed')
        self.assertEqual(len(found),8)
        self.assertIn('snd_soc_sc8280xp',found)
        self.assertNotIn('libcomposite',found)
        self.assertNotIn('dodge_s3910',found)
        self.assertNotIn('qcom_cpucp_mbox',found)
    def test_camera_controller_and_sensor_remain_manual(self):
        paths=['lib/modules/r/kernel/drivers/i2c/busses/i2c-qcom-cci.ko',
               'lib/modules/r/kernel/drivers/media/i2c/s5kjn5.ko',
               'lib/modules/r/kernel/drivers/i2c/busses/i2c-qcom-geni.ko',
               'lib/modules/r/kernel/drivers/clk/qcom/camcc-sm8750.ko',
               'lib/modules/r/kernel/drivers/clk/qcom/cambistmclkcc-sm8750.ko',
               'lib/modules/r/kernel/drivers/phy/qualcomm/phy-qcom-mipi-csi2.ko']
        self.assertEqual(
            policy.blocked_modules(paths,'r'),
            ['cambistmclkcc_sm8750','camcc_sm8750',
             'i2c_qcom_cci','phy_qcom_mipi_csi2','s5kjn5'],
        )

    def test_no_path_traversal_or_other_release(self):
        for name in ['/lib/modules/r/kernel/sound/x.ko','./lib/modules/r/../x.ko','lib/modules/other/kernel/sound/x.ko']:
            with self.subTest(name=name),self.assertRaises(ValueError):policy.blocked_modules([name],'r')
    def test_normalizes_module_name_and_deduplicates(self):
        name='lib/modules/r/kernel/sound/foo-bar.ko'
        self.assertEqual(policy.blocked_modules([name,name],'r'),['foo_bar'])
    def test_install_rule_rejects_explicit_dependency_and_alias_requests(self):
        self.assertEqual(policy.configuration(['fastrpc']).count('install fastrpc /bin/false'),1)
        self.assertIn('blacklist fastrpc\n',policy.configuration(['fastrpc']))
    def test_rejects_shell_tokens(self):
        for names in [[],['a','a'],['x;true'],['a\nb']]:
            with self.subTest(names=names),self.assertRaises(ValueError):policy.configuration(names)
    def test_dependencies_are_denied_before_any_insertion(self):
        deps='kernel/ath12k.ko: kernel/mhi.ko\nkernel/mhi.ko: kernel/shared_bus.ko\nkernel/shared_bus.ko:\nkernel/usb.ko:\n'
        self.assertEqual(policy.dependency_closure(['ath12k'],deps),['ath12k','mhi','shared_bus'])
    def test_missing_or_unsafe_dependency_is_rejected(self):
        for deps in ['kernel/a.ko: kernel/missing.ko\n','kernel/a.ko: ../outside.ko\n','kernel/a.ko:\nkernel/a.ko:\n']:
            with self.subTest(deps=deps),self.assertRaises(ValueError):policy.dependency_closure(['a'],deps)

if __name__=='__main__':unittest.main()
