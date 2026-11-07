"""Network failures must not expose unverified files to packaging."""
import hashlib,importlib.util,io,tempfile,unittest,urllib.error
from pathlib import Path
spec=importlib.util.spec_from_file_location('verified',Path(__file__).resolve().parents[1]/'tools/oneplus13/verified_download.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Download(unittest.TestCase):
    def test_transport_retry_then_valid_bytes_are_cached(self):
        data=b'pinned archive';sha=hashlib.sha256(data).hexdigest();calls=[];delays=[]
        def open_(url,timeout):
            calls.append(timeout)
            if len(calls)==1:raise urllib.error.URLError('TLS EOF')
            return io.BytesIO(data)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'output/archive'
            m.fetch('https://example.test/archive',sha,target,cache_dir=root/'cache',opener=open_,sleep=delays.append)
            self.assertEqual(target.read_bytes(),data);self.assertEqual(calls,[45,45]);self.assertEqual(delays,[2])
            m.fetch('https://example.test/archive',sha,root/'second',cache_dir=root/'cache',opener=lambda *a,**k:self.fail('cache missed'))
            self.assertEqual((root/'second').read_bytes(),data)

    def test_bad_digest_never_replaces_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'archive';target.write_bytes(b'previous')
            sha=hashlib.sha256(b'expected').hexdigest()
            with self.assertRaises(ValueError):m.fetch('https://example.test/archive',sha,target,cache_dir=root/'cache',opener=lambda *a,**k:io.BytesIO(b'wrong'))
            self.assertEqual(target.read_bytes(),b'previous');self.assertEqual(list((root/'cache').iterdir()),[])

    def test_corrupted_cache_is_revalidated(self):
        data=b'valid';sha=hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'cache';cache.mkdir();(cache/sha).write_bytes(b'corrupt')
            m.fetch('https://example.test/archive',sha,root/'output',cache_dir=cache,opener=lambda *a,**k:io.BytesIO(data))
            self.assertEqual((cache/sha).read_bytes(),data)

    def test_transport_failure_is_bounded_and_leaves_no_partial_destination(self):
        calls=[]
        def fail(*a,**kw):calls.append(1);raise urllib.error.URLError('TLS EOF')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaises(urllib.error.URLError):m.fetch('https://example.test/archive','a'*64,root/'output',cache_dir=root/'cache',opener=fail,sleep=lambda _:None)
            self.assertEqual(len(calls),3);self.assertFalse((root/'output').exists());self.assertEqual(list((root/'cache').iterdir()),[])

    def test_official_fallback_keeps_exact_pool_path_and_never_changes_indexes(self):
        base='https://snapshot.ubuntu.com/ubuntu/20261024T000000Z/'
        pool='pool/main/r/resources/resources_1%2b2.tar.gz'
        self.assertEqual(m.ubuntu_pool_urls(base+pool),[
            base+pool,'https://ports.ubuntu.com/ubuntu-ports/'+pool,
            'https://archive.ubuntu.com/ubuntu/'+pool,
            'https://launchpad.net/ubuntu/+archive/primary/+files/resources_1%2b2.tar.gz'])
        for url in [base+'dists/resolute/InRelease',base+pool+'?x=1',
                    (base+pool).replace('snapshot.ubuntu.com','snapshot.ubuntu.com.evil')]:
            self.assertEqual(m.ubuntu_pool_urls(url),[url])
        for path in ['pool/../bad','pool/%2e%2e/bad','pool/a%2fb','pool/a%3fb']:
            with self.assertRaises(ValueError):m.ubuntu_pool_urls(base+path)

    def test_snapshot_failure_uses_hash_verified_official_pool(self):
        data=b'exact snapshot bytes';sha=hashlib.sha256(data).hexdigest();calls=[]
        uri='https://snapshot.ubuntu.com/ubuntu/20261024T000000Z/pool/main/r/r/source.tar.gz'
        def open_(url,timeout):
            calls.append(url)
            if len(calls)==1:raise urllib.error.HTTPError(url,502,'Unavailable',{},None)
            return io.BytesIO(data)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            m.fetch(uri,sha,root/'output',cache_dir=root/'cache',opener=open_,sleep=lambda _:None)
            self.assertEqual((root/'output').read_bytes(),data)
            self.assertEqual(calls,m.ubuntu_pool_urls(uri)[:2])

    def test_wrong_mirror_bytes_are_rejected_without_replacing_destination(self):
        uri='https://snapshot.ubuntu.com/ubuntu/20261024T000000Z/pool/main/r/r/source.tar.gz'
        calls=[]
        def open_(url,timeout):
            calls.append(url)
            if len(calls)==1:raise urllib.error.HTTPError(url,502,'Unavailable',{},None)
            return io.BytesIO(b'wrong')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'output';target.write_bytes(b'previous')
            with self.assertRaises(ValueError):
                m.fetch(uri,hashlib.sha256(b'expected').hexdigest(),target,
                        cache_dir=root/'cache',opener=open_,sleep=lambda _:None)
            self.assertEqual(target.read_bytes(),b'previous')
            self.assertEqual(len(calls),2)
            self.assertEqual(list((root/'cache').iterdir()),[])

if __name__=='__main__':unittest.main()
