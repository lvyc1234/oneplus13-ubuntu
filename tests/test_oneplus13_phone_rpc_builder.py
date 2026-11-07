# SPDX-License-Identifier: GPL-2.0-only
import importlib.util,io,json,tarfile,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'tools/oneplus13/build-phone-rpc.py'
spec=importlib.util.spec_from_file_location('phone_rpc_builder',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class SourceArchive(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def archive(self,names,kind=None):
        path=self.root/'source.tar.gz'
        with tarfile.open(path,'w:gz') as tf:
            for name in names:
                item=tarfile.TarInfo(name)
                if kind=='link':item.type=tarfile.SYMTYPE;item.linkname='../../outside';tf.addfile(item)
                else:item.size=1;tf.addfile(item,io.BytesIO(b'x'))
        return path
    def test_exact_source_prefix_extracts(self):
        dest=self.root/'out';dest.mkdir()
        m.extract(self.archive(['pinned/source.c']),dest,'pinned')
        self.assertEqual((dest/'pinned/source.c').read_bytes(),b'x')
    def test_traversal_and_wrong_root_refused_before_extract(self):
        for name in ['../outside','/outside','pinned/../../outside','other/source.c']:
            with self.subTest(name=name),self.assertRaises(ValueError):m.extract(self.archive([name]),self.root/'out','pinned')
        self.assertFalse((self.root/'out').exists())
    def test_source_link_is_not_followed(self):
        with self.assertRaises(ValueError):m.extract(self.archive(['pinned/link'],'link'),self.root/'out','pinned')
    def test_duplicate_source_entry_refused(self):
        with self.assertRaises(ValueError):m.extract(self.archive(['pinned/a','pinned/a']),self.root/'out','pinned')
    def test_phone_patches_are_fixed_and_separate_from_shared_recipe(self):
        lock=json.loads((m.REPO/'packaging/sensors/sources.json').read_text())
        recipe=next(x for x in lock['sources'] if x['name']=='hexagonrpc')
        self.assertEqual(len(recipe['patches']),5)
        self.assertEqual(len(m.EXTRAS),10)
        for name,digest in m.EXTRAS.items():self.assertEqual(m.sha(m.REPO/'tools/oneplus13/sensor-patches'/name),digest)
    def test_recorded_meson_environment_excludes_runner_secrets(self):
        with patch.dict(m.os.environ,{'PATH':'/usr/bin','HOME':'/tmp/home','GITHUB_TOKEN':'fixture-secret','HTTPS_PROXY':'fixture-private-proxy'},clear=True):
            value=m.build_environment()
        self.assertEqual(value['PATH'],'/usr/bin')
        self.assertNotIn('GITHUB_TOKEN',value)
        self.assertNotIn('HTTPS_PROXY',value)
        self.assertNotIn('fixture-secret',json.dumps(value))

if __name__=='__main__':unittest.main()
