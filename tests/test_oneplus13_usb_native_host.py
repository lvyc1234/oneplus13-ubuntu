# SPDX-License-Identifier: GPL-2.0-only
"""Test native USB identity boundaries and failure cleanup without a phone."""
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_usb',
    ROOT/'tools/oneplus13/ram-usb-native-host-check.py')
N = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(N)
U = N.load_usb()


class NativeIdentityTests(unittest.TestCase):
    def fixture(self, base):
        root = Path(base)
        controller = root/'devices/platform/a600000.usb/xhci-hcd.0.auto'
        hub = controller/'usb1'; hub.mkdir(parents=True)
        bus = root/'bus/usb/devices'; bus.mkdir(parents=True)
        driver = root/'bus/platform/drivers/xhci-hcd'; driver.mkdir(parents=True)
        (controller/'driver').symlink_to(driver, target_is_directory=True)
        (driver/'xhci-hcd.0.auto').symlink_to(controller, target_is_directory=True)
        (bus/'usb1').symlink_to(hub, target_is_directory=True)
        usb = hub/'1-1'; usb.mkdir()
        (usb/'subsystem').symlink_to(root/'bus/usb', target_is_directory=True)
        (bus/'1-1').symlink_to(usb, target_is_directory=True)
        block = usb/'1-1:1.0/host0/target0/0:0/block/sdb/sdb1'; block.mkdir(parents=True)
        blocks = root/'class/block'; blocks.mkdir(parents=True)
        (blocks/'sdb1').symlink_to(block, target_is_directory=True)
        for k,v in dict(partition='1', dev='8:17', size='2048', ro='1').items():
            (block/k).write_text(v)
        return root, usb, block

    def test_exact_controller_read_only_usb_descendant_selected(self):
        with tempfile.TemporaryDirectory() as d:
            root, usb, block = self.fixture(d)
            rows=N.block_candidates(U, root)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['major_minor'],'8:17')
            self.assertEqual(rows[0]['sysfs_path'],str(block))
            self.assertEqual(rows[0]['usb_ancestor'],str(usb))
            self.assertEqual(rows[0]['sysfs_inode'],block.stat().st_ino)
            self.assertTrue(rows[0]['block_read_only'])
            self.assertEqual([x['path'] for x in N.devices(U, root)], ['1-1'])

    def test_other_controller_and_phone_ufs_cannot_qualify(self):
        with tempfile.TemporaryDirectory() as d:
            root, usb, block = self.fixture(d)
            old = root/'class/block/sdb1'; old.unlink()
            unrelated = root/'devices/platform/ufs/sdb1'; unrelated.mkdir(parents=True)
            for k,v in dict(partition='1',dev='8:17',size='2048').items(): (unrelated/k).write_text(v)
            old.symlink_to(unrelated,target_is_directory=True)
            self.assertEqual(N.block_candidates(U,root),[])
            driver=root/'bus/platform/drivers/xhci-hcd/xhci-hcd.0.auto';driver.unlink()
            self.assertEqual(N.devices(U,root),[])

    def test_bad_metadata_and_short_partition_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root, usb, block = self.fixture(d)
            for key,value in [('dev','8:17 extra'),('size','7'),('size','-1'),('ro','0')]:
                original=(block/key).read_text();(block/key).write_text(value)
                self.assertEqual(N.block_candidates(U,root),[])
                (block/key).write_text(original)

    def test_read_uses_only_read_descriptor_and_checks_identity(self):
        row=dict(name='sdb1',device='/dev/sdb1',major_minor='8:17',size_sectors=2048)
        with patch.object(N,'block_candidates',return_value=[row]), \
             patch.object(N.os,'open',return_value=99) as opened, \
             patch.object(N.os,'fstat',return_value=Mock(st_mode=stat.S_IFBLK,st_rdev=N.os.makedev(8,17))), \
             patch.object(N.os,'pread',return_value=b'x'*4096) as read, \
             patch.object(N.os,'close') as closed:
            proof=N.read_block(U,row)
            flags=opened.call_args.args[1]
            self.assertEqual(flags & N.os.O_ACCMODE,N.os.O_RDONLY)
            self.assertTrue(flags & N.os.O_NOFOLLOW)
            read.assert_called_once_with(99,4096,0);closed.assert_called_once_with(99)
            self.assertEqual(proof['read_bytes'],4096)

    def test_wrong_descriptor_or_short_read_closes_and_never_passes(self):
        row=dict(name='sdb1',device='/dev/sdb1',major_minor='8:17',size_sectors=2048)
        for mode,rdev,data in [(stat.S_IFREG,0,b'x'*4096),
                              (stat.S_IFBLK,N.os.makedev(8,18),b'x'*4096),
                              (stat.S_IFBLK,N.os.makedev(8,17),b'')]:
            with patch.object(N,'block_candidates',return_value=[row]), \
                 patch.object(N.os,'open',return_value=99), \
                 patch.object(N.os,'fstat',return_value=Mock(st_mode=mode,st_rdev=rdev)), \
                 patch.object(N.os,'pread',return_value=data),patch.object(N.os,'close') as closed:
                with self.assertRaises(RuntimeError):N.read_block(U,row)
                closed.assert_called_once_with(99)

    def test_detach_between_checks_cannot_qualify(self):
        row=dict(name='sdb1',device='/dev/sdb1',major_minor='8:17',size_sectors=2048)
        with patch.object(N,'block_candidates',side_effect=[[row],[]]), \
             patch.object(N.os,'open',return_value=99), \
             patch.object(N.os,'fstat',return_value=Mock(st_mode=stat.S_IFBLK,st_rdev=N.os.makedev(8,17))), \
             patch.object(N.os,'pread') as read,patch.object(N.os,'close') as closed:
            with self.assertRaises(RuntimeError):N.read_block(U,row)
            read.assert_not_called();closed.assert_called_once_with(99)


class NativeLifecycleTests(unittest.TestCase):
    def run_probe(self, *, run=None, candidates=True, idle=True):
        usb=Mock()
        usb.firmware_lease_status.return_value=dict(N.IDLE)
        usb.firmware_otg_inventory.return_value=dict(complete=True,values=dict(N.ZERO))
        if not idle:usb.firmware_lease_status.return_value=dict(N.IDLE,used=1)
        proof=dict(read_bytes=4096,descriptor_read_only=True,block_identity_verified=True,cleanup_complete=True)
        run=run or Mock(return_value=Mock(returncode=0,stdout=json.dumps(proof)))
        with patch.object(N,'host_guard'),patch.object(N,'devices',return_value=[dict(path='1-1')]), \
             patch.object(N,'block_candidates',return_value=[dict(device='/dev/sdb1')] if candidates else []), \
             patch.object(N.time,'monotonic',side_effect=[10,10,75,75]):
            result=N.probe(usb,Path('/unused-mode'),60,run)
        usb.write.assert_not_called();usb.firmware_lease_command.assert_not_called()
        usb.firmware_lease_restore.assert_not_called();usb.reconcile_host.assert_not_called()
        return result,run

    def test_success_has_independent_scope_without_restore_claim(self):
        result,run=self.run_probe()
        self.assertTrue(result['functional_pass']);self.assertTrue(result['cleanup_complete'])
        self.assertNotIn('role_restored',result);self.assertNotIn('firmware_lease_restore',result)
        self.assertEqual(run.call_args.kwargs['timeout'],8)

    def test_timeout_or_invalid_child_proof_never_qualifies(self):
        cases=[Mock(side_effect=subprocess.TimeoutExpired('reader',8)),
               Mock(return_value=Mock(returncode=1,stdout='')),
               Mock(return_value=Mock(returncode=0,stdout='{}')),
               Mock(return_value=Mock(returncode=0,stdout='not json')),
               Mock(return_value=Mock(returncode=0,stdout='{"read_bytes":0,"read_bytes":4096,"descriptor_read_only":true,"block_identity_verified":true,"cleanup_complete":true}')),
               Mock(return_value=Mock(returncode=0,stdout=json.dumps(dict(read_bytes=0,
                   descriptor_read_only=True,block_identity_verified=True,cleanup_complete=True))))]
        for run in cases:
            result,_=self.run_probe(run=run)
            self.assertFalse(result['functional_pass']);self.assertTrue(result['cleanup_complete'])

    def test_native_host_without_block_is_not_io(self):
        result,run=self.run_probe(candidates=False)
        self.assertFalse(result['functional_pass']);run.assert_not_called()

    def test_dirty_baseline_refuses_all_observation_and_read(self):
        result,run=self.run_probe(idle=False)
        self.assertFalse(result['functional_pass']);self.assertFalse(result['cleanup_complete'])
        self.assertTrue(result['firmware_state_unverified']);run.assert_not_called()

    def test_bool_is_not_exact_unused_zero(self):
        usb=Mock();usb.firmware_lease_status.return_value=dict(N.IDLE,used=False)
        usb.firmware_otg_inventory.return_value=dict(complete=True,values=dict(N.ZERO))
        with self.assertRaises(RuntimeError):N.unused(usb)


if __name__=='__main__':unittest.main()
