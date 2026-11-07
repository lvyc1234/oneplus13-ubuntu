import gzip,stat,unittest
import test_oneplus13 as formats
from common import make_cpio,read_cpio
from native import build_initramfs
from spr import encode_profile

class ImportedProfileTests(unittest.TestCase):
    def inputs(self):
        original=gzip.compress(make_cpio([('init',stat.S_IFREG|0o755,b'#!/bin/sh\nprotect_storage\nsleep 300\n',0,0),
            ('bin',stat.S_IFDIR|0o755,b'',0,0),('bin/modprobe',stat.S_IFLNK|0o777,b'busybox',0,0)]),mtime=0)
        return original,formats.PhoneFormats.native_modules(),'test-release',(formats.TOOLS.parents[1]/'devices/oneplus13/start-native-display').read_bytes()

    def test_imported_profile_preserves_native_archive(self):
        original,modules,release,helper=self.inputs();xml=formats.PhoneFormats.synthetic_spr()
        expected,_=build_initramfs(original,modules,release,xml,helper)
        actual,_=build_initramfs(original,modules,release,None,helper,encoded_profile=encode_profile(xml))
        self.assertEqual(actual,expected)

    def test_invalid_imported_profile_is_rejected(self):
        original,modules,release,helper=self.inputs();profile=encode_profile(formats.PhoneFormats.synthetic_spr())
        for blob in [profile[:-1],b'X'+profile[1:],profile[:8]+b'\x02'+profile[9:],profile[:-1]+bytes([profile[-1]^1])]:
            with self.subTest(size=len(blob)),self.assertRaises(ValueError):
                build_initramfs(original,modules,release,None,helper,encoded_profile=blob)
        with self.assertRaises(ValueError):
            build_initramfs(original,modules,release,formats.PhoneFormats.synthetic_spr(),helper,encoded_profile=profile)

if __name__=='__main__':unittest.main()
