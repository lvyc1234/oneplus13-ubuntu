"""Synthetic roots/commands only. No connected hardware is used."""
import hashlib,importlib.util,subprocess,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('display_pre',Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-display-prerequisites.py')
pre=importlib.util.module_from_spec(spec);spec.loader.exec_module(pre)

class Prerequisite(unittest.TestCase):
    def fixture(self,root):
        files={'run/oneplus13/ram-session':b'', 'proc/mounts':b'overlay / overlay rw 0 0\n',
            pre.POLICY:b'blacklist cec\ninstall cec /bin/false\ninstall ubwc_config /bin/false\ninstall fastrpc /bin/false\n',
            'sys/class/block/sda/ro':b'1\n','usr/lib/modules/test/kernel/drivers/media/cec/core/cec.ko':b'module',
            'usr/lib/modules/test/kernel/drivers/soc/qcom/ubwc_config.ko':b'module'}
        for name,data in files.items():
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        (root/'dev').mkdir();self.calls=[]
        def run(args):
            self.calls.append(args)
            if 'is-active' in args:return 'active'
            if '-F' in args:return '' if 'depends' in args else 'test SMP'
            if '-c' in args:return (root/pre.POLICY).read_text()
            (root/'sys/module'/args[-1]).mkdir(parents=True)
            return ''
        return hashlib.sha256(b'module').hexdigest(),hashlib.sha256((root/pre.POLICY).read_bytes()).hexdigest(),run
    def test_one_explicit_core_load_and_no_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);digest,policy_sha,run=self.fixture(root)
            original=(root/pre.POLICY).read_bytes()
            result=pre.prepare(root,'test',digest,policy_sha,digest,run)
            self.assertEqual((root/pre.POLICY).read_bytes(),original.replace(b'install cec /bin/false\n',b'install cec /bin/true\n').replace(b'install ubwc_config /bin/false\n',b'install ubwc_config /bin/true\n'))
            self.assertEqual(result['hardware_cec_adapters'],0)
            loads=[x for x in self.calls if '--ignore-install' in x]
            self.assertEqual(loads,[['/usr/sbin/modprobe','--ignore-install',n] for n in ['cec','ubwc_config']])
            with self.assertRaises(ValueError):pre.prepare(root,'test',digest,policy_sha,digest,run)
            self.assertEqual([x for x in self.calls if '--ignore-install' in x],loads)
    def test_wrong_hash_readwrite_storage_or_adapter_prevents_load(self):
        for fault in ['hash','ubwc_hash','storage','adapter','bus','policy','service','vermagic','depends','duplicate']:
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);digest,policy_sha,run=self.fixture(root)
                ubwc_digest=digest
                if fault=='hash':digest='0'*64
                if fault=='ubwc_hash':ubwc_digest='0'*64
                if fault=='storage':(root/'sys/class/block/sda/ro').write_text('0')
                if fault=='adapter':(root/'dev/cec0').touch()
                if fault=='bus':(root/'sys/bus/cec/devices/cec0').mkdir(parents=True)
                def invoke(args):
                    if fault=='policy' and '-c' in args:return ''
                    if fault=='service' and 'is-active' in args:return 'inactive'
                    if fault=='vermagic' and 'vermagic' in args:return 'other SMP'
                    if fault=='depends' and 'depends' in args:return 'fastrpc'
                    if fault=='duplicate' and '-c' in args:return run(args)+'install cec /bin/true\n'
                    return run(args)
                with self.assertRaises(ValueError):pre.prepare(root,'test',digest,policy_sha,ubwc_digest,invoke)
                self.assertFalse(any('--ignore-install' in x for x in self.calls))
    def test_failed_load_retains_attempt_and_never_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);digest,policy_sha,run=self.fixture(root)
            def fail(args):
                if '--ignore-install' in args:raise subprocess.CalledProcessError(1,args)
                return run(args)
            with self.assertRaises(subprocess.SubprocessError):pre.prepare(root,'test',digest,policy_sha,digest,fail)
            self.assertTrue((root/'run/oneplus13/cec-core-load-attempted').exists())
            with self.assertRaises(FileExistsError):pre.prepare(root,'test',digest,policy_sha,digest,fail)
    def test_no_module_registration_is_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);digest,policy_sha,run=self.fixture(root)
            def no_load(args):return '' if '--ignore-install' in args else run(args)
            with self.assertRaises(ValueError):pre.prepare(root,'test',digest,policy_sha,digest,no_load)
    def test_adapter_created_during_core_load_is_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);digest,policy_sha,run=self.fixture(root)
            def adapter(args):
                answer=run(args)
                if '--ignore-install' in args:(root/'dev/cec0').touch()
                return answer
            with self.assertRaises(ValueError):pre.prepare(root,'test',digest,policy_sha,digest,adapter)
    def test_real_command_cannot_target_fixture(self):
        with self.assertRaises(ValueError):pre.prepare(Path('/never-real'),'test','0'*64,'0'*64,'0'*64)

    def test_second_load_failure_keeps_all_denials_and_attempt_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);digest,policy_sha,run=self.fixture(root)
            original=(root/pre.POLICY).read_bytes()
            def second_fails(args):
                if '--ignore-install' in args and args[-1]=='ubwc_config':raise subprocess.CalledProcessError(1,args)
                return run(args)
            with self.assertRaises(subprocess.SubprocessError):pre.prepare(root,'test',digest,policy_sha,digest,second_fails)
            self.assertEqual((root/pre.POLICY).read_bytes(),original)
            self.assertTrue((root/'run/oneplus13/cec-core-load-attempted').exists())
if __name__=='__main__':unittest.main()
