# SPDX-License-Identifier: GPL-2.0-only
import errno, hashlib, importlib.util, os, stat, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
P=Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-sensor-attach-only.py'
spec=importlib.util.spec_from_file_location('sensor_attach_only',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Worker(unittest.TestCase):
    def test_one_exact_ioctl_then_close_no_client(self):
        calls=[]
        def ioctl(*a):calls.append(('ioctl',a));return 0
        def close(fd):calls.append(('close',fd))
        result=m.worker(lambda *a:9,ioctl,close,iter([0,0.1]).__next__)
        self.assertEqual(calls,[('ioctl',(9,0x5208,0)),('close',9)])
        self.assertEqual(result['attach_return'],0)
        self.assertFalse(result['dsp_release_acknowledgement_observed'])
        self.assertFalse(result['sensor_stream_requested'])
    def test_ioctl_error_still_closes_without_retry(self):
        ioctl=Mock(side_effect=OSError(errno.ETIMEDOUT,'timeout'));close=Mock()
        result=m.worker(lambda *a:9,ioctl,close,iter([0,20]).__next__)
        self.assertEqual(result['attach_errno'],errno.ETIMEDOUT)
        ioctl.assert_called_once_with(9,0x5208,0);close.assert_called_once_with(9)
    def test_close_failure_cannot_pass(self):
        with self.assertRaises(OSError):
            m.worker(lambda *a:9,lambda *a:0,Mock(side_effect=OSError('close failed')))

@unittest.skipIf(os.name=='nt','Runtime masks require Linux symlink semantics')
class Qualification(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.release='7.2.0-test';self.digest=hashlib.sha256(b'module').hexdigest()
        self.policy=b'install fastrpc /bin/false\ninstall ath12k /bin/false\n'
        self.policy_sha=hashlib.sha256(self.policy).hexdigest();self.calls=[]
        for name,data in [('run/oneplus13/ram-session',b''),('proc/mounts',b'overlay / overlay rw 0 0\n'),
          ('sys/class/block/sda/ro',b'1\n'),(m.POLICY,self.policy),
          ('usr/lib/modules/'+self.release+'/kernel/drivers/misc/fastrpc.ko',b'module')]:
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        def run(args):
            self.calls.append(args)
            if args[1:]==['-c']:return self.policy.decode()
            if 'is-active' in args:return 'active'
            if 'vermagic' in args:return self.release+' SMP preempt mod_unload aarch64'
            if 'depends' in args:return ''
            if 'mask' in args:
                target=self.root/'run/systemd/system'/args[-1];target.parent.mkdir(parents=True,exist_ok=True)
                target.symlink_to(self.root/'dev/null');return ''
            if '--ignore-install' in args:(self.root/'sys/module/fastrpc').mkdir(parents=True);return ''
            if 'settle' in args:return ''
            raise AssertionError(args)
        self.run=run;self.probe=Mock(return_value={'attach_return':0,'local_fd_closed':True})
    def prepare(self):return m.prepare(self.root,self.release,self.digest,self.policy_sha,self.run,self.probe,lambda root:None)
    def test_qualified_one_load_masks_first_policy_unchanged(self):
        result=self.prepare();self.assertEqual(result['attempts'],1);self.probe.assert_called_once()
        load=next(i for i,args in enumerate(self.calls) if '--ignore-install' in args)
        self.assertEqual(sum('mask' in a for a in self.calls[:load]),len(m.UNITS))
        self.assertEqual((self.root/m.POLICY).read_bytes(),self.policy)
        self.assertFalse(any('restart' in a or 'rmmod' in a for a in self.calls))
    def test_rejects_writable_storage_before_load(self):
        (self.root/'sys/class/block/sda/ro').write_text('0')
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse(any('--ignore-install' in a for a in self.calls));self.probe.assert_not_called()
    def test_rejects_automatic_fastrpc_hook(self):
        p=self.root/'usr/lib/udev/rules.d/90-rpc.rules';p.parent.mkdir(parents=True)
        p.write_text('KERNEL=="fastrpc*", RUN+="hexagonrpcd -s"\n')
        with self.assertRaises(ValueError):self.prepare()
        self.probe.assert_not_called()
    def test_prior_attempt_prevents_load(self):
        (self.root/m.MARKER).write_text('prior')
        with self.assertRaises(FileExistsError):self.prepare()
        self.assertFalse(any('--ignore-install' in a for a in self.calls))
    def test_module_digest_or_policy_mismatch_prevents_load(self):
        for attr in ('digest','policy_sha'):
            original=getattr(self,attr);setattr(self,attr,'0'*64)
            with self.assertRaises(ValueError):self.prepare()
            setattr(self,attr,original)
        self.probe.assert_not_called()
    def test_probe_failure_retains_attempt_no_restart(self):
        self.probe.side_effect=ValueError('ioctl failed')
        with self.assertRaises(ValueError):self.prepare()
        self.assertTrue((self.root/m.MARKER).exists())
        self.assertEqual(sum('--ignore-install' in a for a in self.calls),1)
        self.assertFalse(any('restart' in a or 'rmmod' in a for a in self.calls))

@unittest.skipIf(os.name=='nt','RPMSG links require Linux symlink semantics')
class DeviceTopology(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.info=SimpleNamespace(st_mode=stat.S_IFCHR|0o600,st_rdev=os.makedev(10,123))
        for name,data in [(m.NODE,b''),('sys/class/misc/fastrpc-adsp-secure/dev',b'10:123\n'),
              ('sys/firmware/devicetree/base/'+m.OF_NODE+'/label',b'adsp\0')]:
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        self.channel=self.root/'sys/bus/rpmsg/devices/adsp-channel';self.channel.mkdir(parents=True)
        driver=self.root/'sys/bus/rpmsg/drivers/qcom,fastrpc';driver.mkdir(parents=True)
        (self.channel/'driver').symlink_to(driver)
        (self.channel/'of_node').symlink_to(self.root/'sys/firmware/devicetree/base'/m.OF_NODE)
    def test_real_driver_shape_has_no_misc_parent(self):
        self.assertFalse((self.root/'sys/class/misc/fastrpc-adsp-secure/device').exists())
        m.device_ready(self.root,self.info)
    def test_wrong_device_number_rejected(self):
        (self.root/'sys/class/misc/fastrpc-adsp-secure/dev').write_text('10:124')
        with self.assertRaises(ValueError):m.device_ready(self.root,self.info)
    def test_wrong_or_ambiguous_rpmsg_channel_rejected(self):
        extra=self.channel.parent/'other';extra.mkdir();(extra/'driver').symlink_to((self.channel/'driver').resolve())
        with self.assertRaises(ValueError):m.device_ready(self.root,self.info)
        (extra/'driver').unlink();extra.rmdir()
        (self.channel/'of_node').unlink();(self.channel/'of_node').symlink_to(self.root/'wrong')
        with self.assertRaises(ValueError):m.device_ready(self.root,self.info)
    def test_additional_domain_or_wrong_label_rejected(self):
        p=self.root/'dev/fastrpc-cdsp';p.touch()
        with self.assertRaises(ValueError):m.device_ready(self.root,self.info)
        p.unlink();(self.root/'sys/firmware/devicetree/base'/m.OF_NODE/'label').write_bytes(b'cdsp\0')
        with self.assertRaises(ValueError):m.device_ready(self.root,self.info)

if __name__=='__main__':unittest.main()
