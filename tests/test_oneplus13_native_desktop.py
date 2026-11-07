# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic roots and injected commands only; never access a connected phone."""
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_desktop',REPO/'devices/oneplus13/native-desktop.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)


class NativeDesktopTests(unittest.TestCase):
    def fixture(self,root):
        payload=bytes(136)
        profile=b'AA569S2\0'+struct.pack('<8I',1,1440,3168,120,34,zlib.crc32(payload),0,0)+payload
        helper=b'#!/bin/sh\nexit 99\n'
        files={native.PROFILE:profile,native.HELPER:helper,
            'etc/oneplus13-native-candidate':b'test-release\n',
            'usr/lib/oneplus13-desktop/kernel-release':b'test-release\n',
            'etc/oneplus13-native-handoff':('ONEPLUS13_NATIVE_HANDOFF_V1\ntest-release\n'+
                hashlib.sha256(profile).hexdigest()+'\n'+hashlib.sha256(helper).hexdigest()+'\n').encode(),
            'sys/firmware/devicetree/base/model':b'OnePlus 13 (AA569 native display experimental candidate)\0',
            'sys/firmware/devicetree/base/chassis-type':b'handset\0',
            'sys/firmware/devicetree/base/compatible':b'oneplus,dodge\0qcom,sm8750\0',
            'run/oneplus13/ram-session':b'', 'proc/cmdline':b'oneplus13.desktop=1',
            'proc/mounts':b'overlay / overlay rw,lowerdir=/run/rootfs.ro,upperdir=/run/rootfs.rw 0 0\n',
            'sys/class/block/sda/ro':b'1\n','sys/class/udc/example/state':b'configured\n',
            'sys/kernel/config/usb_gadget/oneplus13/UDC':b'example\n'}
        for name,data in files.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        return files

    def test_complete_synthetic_preflight_does_not_execute_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            self.assertEqual(native.validate_files(root,'test-release')['physical_blocks_checked'],1)
            self.assertEqual((root/native.HELPER).read_bytes(),b'#!/bin/sh\nexit 99\n')
            self.assertFalse((root/'run/oneplus13/native/activation-attempted').exists())

    def test_rejects_wrong_context_corruption_and_storage(self):
        faults={
            'etc/oneplus13-native-candidate':b'other\n',
            'usr/lib/oneplus13-desktop/kernel-release':b'other\n',
            native.PROFILE:bytes(176),native.HELPER:b'changed',
            'sys/firmware/devicetree/base/model':b'Pad\0',
            'sys/firmware/devicetree/base/chassis-type':b'tablet\0',
            'proc/cmdline':b'oneplus13.desktop=1 oneplus13.desktop.qemu=1',
            'proc/mounts':b'/dev/sda1 / ext4 rw 0 0\n',
            'sys/class/block/sda/ro':b'0\n','sys/class/udc/example/state':b'disconnected\n'}
        for name,data in faults.items():
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root);(root/name).write_bytes(data)
                with self.assertRaises(ValueError):native.validate_files(root,'test-release')

    def test_crc_rejected_even_if_manifest_hash_is_recomputed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);files=self.fixture(root)
            p=bytearray(files[native.PROFILE]);p[-1]=1;(root/native.PROFILE).write_bytes(p)
            marker=root/'etc/oneplus13-native-handoff'
            marker.write_bytes(marker.read_bytes().replace(hashlib.sha256(files[native.PROFILE]).hexdigest().encode(),hashlib.sha256(p).hexdigest().encode()))
            with self.assertRaises(ValueError):native.validate_files(root,'test-release')

    def test_abl_identity_and_already_loaded_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            base=root/'sys/firmware/devicetree/base'
            (base/'model').write_bytes(b'Qualcomm Technologies, Inc. Sun MTP,dodge\0')
            (base/'compatible').write_bytes(b'qcom,sun\0')
            native.validate_files(root,'test-release')
            (root/'sys/module/msm').mkdir(parents=True)
            with self.assertRaises(ValueError):native.validate_files(root,'test-release')

    def test_runtime_services_policy_deadline_and_vermagic(self):
        calls=[]
        def run(args):
            calls.append(args)
            if 'is-active' in args:return 'active'
            if 'get-property' in args:return 't 900000000'
            if '-c' in args:return '\n'.join('install '+m+' /bin/false' for m in native.MODULES)
            return 'test-release SMP'
        native.runtime_checks('test-release',run=run,now=lambda:100)
        self.assertFalse(any('--ignore-install' in args for args in calls))
        for fault in ['inactive','deadline','policy','vermagic']:
            def bad(args):
                if fault=='inactive' and 'is-active' in args:return 'inactive'
                if fault=='deadline' and 'get-property' in args:return 't 1000000000'
                if fault=='policy' and '-c' in args:return ''
                if fault=='vermagic' and '-F' in args:return 'wrong SMP'
                return run(args)
            with self.subTest(fault=fault),self.assertRaises(ValueError):
                native.runtime_checks('test-release',run=bad,now=lambda:100)
        with self.assertRaises(ValueError):native.runtime_checks('test-release',run=run,now=lambda:850)

    def test_explicit_loader_order_and_no_retry_after_error(self):
        calls=[]
        native.load_modules(run=lambda args:calls.append(args))
        self.assertEqual(calls,[['/usr/sbin/modprobe','--ignore-install',m] for m in native.MODULES])
        for fail in [1,2]:
            calls=[]
            def run(args):
                calls.append(args)
                if len(calls)==fail:raise OSError('synthetic failure')
            with self.subTest(fail=fail),self.assertRaises(OSError):native.load_modules(run=run)
            self.assertEqual(len(calls),fail)

    @unittest.skipUnless(os.name=='posix','Exercise real flock on the ARM64 Linux runner')
    def test_one_shot_lock_and_failure_marker_use_only_synthetic_root(self):
        for fail in [False,True]:
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root);loads=[]
                def run(args):
                    if 'is-active' in args:return 'active'
                    if 'get-property' in args:return 't 900000000'
                    if '-c' in args:return '\n'.join('install '+m+' /bin/false' for m in native.MODULES)
                    if '-F' in args:return 'test-release SMP'
                    loads.append(args)
                    if fail:raise OSError('synthetic module failure')
                    return ''
                if fail:
                    with self.assertRaises(OSError):native.activate(root,'test-release',run=run,now=lambda:100)
                else:native.activate(root,'test-release',run=run,now=lambda:100)
                self.assertTrue((root/'run/oneplus13/native/activation-attempted').is_file())
                count=len(loads)
                with self.assertRaises(ValueError):native.activate(root,'test-release',run=run,now=lambda:100)
                self.assertEqual(len(loads),count)
