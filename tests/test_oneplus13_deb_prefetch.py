# SPDX-License-Identifier: GPL-2.0-only
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import threading
import time
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools/oneplus13'))

SPEC = importlib.util.spec_from_file_location('deb_prefetch', Path(__file__).resolve().parents[1] / 'tools/oneplus13/prefetch-rootfs-debs.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
BASE = 'https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/'
DATA = b'public fake package payload'
SHA = hashlib.sha256(DATA).hexdigest()
ROW = (BASE + 'pool/main/x/x/x_1_arm64.deb', 'x_1%3a1_arm64.deb', len(DATA), SHA)


class Prefetch(unittest.TestCase):
    def line(self, row=ROW):
        return f"'{row[0]}' {row[1]} {row[2]} SHA256:{row[3]}"

    def test_real_apt_epoch_filename_and_human_output(self):
        self.assertEqual(MOD.plan('Reading package lists...\n' + self.line(), BASE), [ROW])

    def test_apt_percent_escaped_version_is_accepted(self):
        row = (BASE + 'pool/main/x/x/x_1%2b2%7e3_arm64.deb', *ROW[1:])
        self.assertEqual(MOD.plan(self.line(row), BASE), [row])

    def test_reject_untrusted_origin_paths_and_hashes(self):
        for uri in [ROW[0].replace('https:', 'http:'), ROW[0] + '?token=x',
                    ROW[0].replace('/pool/', '/pool/../'),
                    ROW[0].replace('/pool/', '/pool/%2e/'),
                    ROW[0].replace('snapshot.ubuntu.com', 'snapshot.ubuntu.com.evil')]:
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                MOD.plan(self.line((uri, *ROW[1:])), BASE)
        for line in [self.line().replace('SHA256:', 'MD5Sum:'),
                     self.line().replace(ROW[1], '../x.deb'),
                     self.line().replace(str(ROW[2]) + ' SHA256', '0 SHA256'),
                     self.line() + '\n' + self.line((*ROW[:3], '0' * 64))]:
            with self.subTest(line=line), self.assertRaises(ValueError):
                MOD.plan(line, BASE)

    def test_cache_hit_is_rehashed_and_no_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); cache = MOD.cache_directory(root / 'cache')
            archives = MOD.safe_directory(root / 'archives')
            (cache / ROW[1]).write_bytes(DATA)
            def no_network(*args):
                self.fail('Valid cache should not download')
            hit, size = MOD.fetch(ROW, cache, archives, threading.Event(), time.monotonic() + 10, no_network)
            self.assertTrue(hit); self.assertEqual(size, len(DATA))
            self.assertEqual((archives / ROW[1]).read_bytes(), DATA)

    def test_corrupt_cache_repaired_only_by_verified_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); cache = MOD.cache_directory(root / 'cache')
            archives = MOD.safe_directory(root / 'archives')
            (cache / ROW[1]).write_bytes(b'bad')
            calls = []
            def download(uri, target, *unused):
                calls.append(uri); target.write_bytes(DATA)
            self.assertEqual(MOD.fetch(ROW, cache, archives, threading.Event(), time.monotonic() + 10, download), (False, len(DATA)))
            self.assertEqual(len(calls), 1)
            self.assertEqual((cache / ROW[1]).read_bytes(), DATA)

    def test_wrong_download_size_and_hash_never_staged(self):
        for data in [b'bad', b'!' * len(DATA)]:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); cache = MOD.cache_directory(root / 'cache')
                archives = MOD.safe_directory(root / 'archives'); calls = []
                def download(uri, target, *unused):
                    calls.append(uri); target.write_bytes(data)
                with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                    MOD.fetch(ROW, cache, archives, threading.Event(), time.monotonic() + 10, download)
                self.assertEqual(len(calls), 9)
                self.assertFalse((cache / ROW[1]).exists())
                self.assertEqual(list(archives.iterdir()), [])
                self.assertFalse(list(cache.glob('*.partial')))

    def test_snapshot_transport_failure_uses_official_bytes_from_signed_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cache=MOD.cache_directory(root/'cache')
            archives=MOD.safe_directory(root/'archives');calls=[]
            def download(uri,target,*unused):
                calls.append(uri)
                if len(calls)==1:raise RuntimeError('snapshot unavailable')
                target.write_bytes(DATA)
            self.assertEqual(MOD.fetch(ROW,cache,archives,threading.Event(),
                time.monotonic()+10,download),(False,len(DATA)))
            self.assertEqual(calls,[ROW[0],'https://ports.ubuntu.com/ubuntu-ports/pool/main/x/x/x_1_arm64.deb'])
            self.assertEqual((archives/ROW[1]).read_bytes(),DATA)

    def test_foreign_cache_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'other').write_text('retain')
            with self.assertRaisesRegex(ValueError, 'unowned'):
                MOD.cache_directory(root)
            self.assertEqual((root / 'other').read_text(), 'retain')

    @unittest.skipIf(__import__('os').name == 'nt', 'Native symlink policy verified in Linux')
    def test_linked_cache_and_archive_destinations_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / 'target'; target.mkdir()
            linked = root / 'linked'; linked.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError): MOD.cache_directory(linked)
            cache = MOD.cache_directory(root / 'cache'); archives = MOD.safe_directory(root / 'archives')
            external = root / 'external'; external.write_bytes(DATA)
            (cache / ROW[1]).symlink_to(external)
            with self.assertRaises(ValueError):
                MOD.fetch(ROW, cache, archives, threading.Event(), time.monotonic() + 10)
            self.assertEqual(external.read_bytes(), DATA)


if __name__ == '__main__':
    unittest.main()
