# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
import contextlib,errno,os,stat,tempfile,types,unittest
from unittest.mock import Mock,patch
import importlib.util
_spec=importlib.util.spec_from_file_location('readonly_block',Path(__file__).resolve().parents[1]/'tools/oneplus13/readonly-block.py')
ro=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(ro)


class NonblockingReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.entry=self.root/'sdh';self.entry.mkdir()
        for name,value in [('dev','8:112\n'),('size','0\n'),('removable','1\n')]:
            (self.entry/name).write_text(value)
        self.readonly=0;self.calls=[];self.node_mode=stat.S_IFBLK;self.changed=False;self.fail_set=False
        self.os_stat=os.stat
        self.close=Mock()

    def open(self,path,flags):
        self.assertEqual(flags&os.O_ACCMODE,os.O_RDONLY)
        if not flags&os.O_NONBLOCK:raise OSError(errno.ENOMEDIUM,'No medium found')
        self.assertTrue(flags&os.O_NOFOLLOW);return 99

    def inode(self):
        return types.SimpleNamespace(st_mode=self.node_mode,st_rdev=os.makedev(8,112),st_ino=901)

    def node_stat(self,path,*args,**kwargs):
        if str(path)=='/dev/sdh':
            value=self.inode()
            if self.changed:value.st_ino+=1
            return value
        return self.os_stat(path,*args,**kwargs)

    def ioctl(self,fd,cmd,arg,mutate):
        self.assertEqual(fd,99);self.calls.append(cmd)
        if cmd==ro.BLKROGET:arg[0]=self.readonly
        elif cmd==ro.BLKROSET:
            self.assertEqual(list(arg),[1])
            if self.fail_set:raise PermissionError(errno.EPERM,'denied')
            self.readonly=1
        else:raise AssertionError('Unexpected block operation')

    def protect(self):
        with patch.object(ro.os,'open',side_effect=self.open), \
                patch.object(ro.os,'fstat',side_effect=lambda fd:self.inode()), \
                patch.object(ro.os,'stat',side_effect=self.node_stat), \
                patch.object(ro.os,'close',self.close), \
                patch.object(ro.fcntl,'ioctl',side_effect=self.ioctl):
            return ro.protect('/dev/sdh',self.root)

    def test_empty_medium_open_negative_control_and_real_helper_flag(self):
        with self.assertRaises(OSError) as error:self.open('/dev/sdh',os.O_RDONLY)
        self.assertEqual(error.exception.errno,errno.ENOMEDIUM)
        result=self.protect()
        self.assertTrue(result['nonblocking_open']);self.assertEqual(result['size'],'0')
        self.assertEqual(result['removable'],'1');self.assertEqual(result['readonly_after'],1)
        self.assertEqual(self.calls,[ro.BLKROGET,ro.BLKROSET,ro.BLKROGET])
        self.close.assert_called_once_with(99)

    def test_already_readonly_medium_has_no_set_request(self):
        self.readonly=1;result=self.protect()
        self.assertFalse(result['set_readonly_requested'])
        self.assertEqual(self.calls,[ro.BLKROGET,ro.BLKROGET])

    def test_non_block_descriptor_cannot_receive_ioctl(self):
        self.node_mode=stat.S_IFREG
        with self.assertRaises(RuntimeError):self.protect()
        self.assertEqual(self.calls,[]);self.close.assert_called_once()

    def test_device_number_mismatch_fails_before_ioctl(self):
        (self.entry/'dev').write_text('8:113\n')
        with self.assertRaises(RuntimeError):self.protect()
        self.assertEqual(self.calls,[]);self.close.assert_called_once()

    def test_replaced_device_node_never_claims_verified(self):
        self.changed=True
        with self.assertRaisesRegex(RuntimeError,'identity changed'):self.protect()
        self.close.assert_called_once()

    def test_set_failure_remains_failure_and_closes_fd(self):
        self.fail_set=True
        with self.assertRaises(PermissionError):self.protect()
        self.assertEqual(self.calls,[ro.BLKROGET,ro.BLKROSET]);self.close.assert_called_once()

    def test_unconfirmed_readonly_flag_never_passes(self):
        original=self.ioctl
        def transfer(fd,cmd,arg,mutate):
            original(fd,cmd,arg,mutate)
            if cmd==ro.BLKROGET:arg[0]=0
        with patch.object(self,'ioctl',side_effect=transfer):
            with self.assertRaisesRegex(RuntimeError,'did not confirm'):self.protect()
        self.close.assert_called_once()

    def test_unsafe_path_is_rejected_without_open(self):
        for path in ['/tmp/sdh','/dev/../sdh','/dev/sub/sdh']:
            with self.assertRaises(ValueError):ro.protect(path,self.root)


if __name__=='__main__':unittest.main()
