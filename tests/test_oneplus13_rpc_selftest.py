# SPDX-License-Identifier: GPL-2.0-only
import contextlib,hashlib,importlib.util,io,json,struct,subprocess,tarfile,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
p=Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-rpc-selftest.py'
spec=importlib.util.spec_from_file_location('mock_rpc_selftest',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Archive(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'tests.tar.gz'
    def fixture(self,names=None,kind=None,arch=183):
        data=bytearray(32);data[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',data,18,arch)
        files={}
        with tarfile.open(self.path,'w:gz') as tf:
            for name in names if names is not None else m.NAMES:
                item=tarfile.TarInfo(name);item.mode=0o755
                if kind=='link':item.type=tarfile.SYMTYPE;item.linkname='/dev/fastrpc-adsp-secure';tf.addfile(item)
                else:item.size=len(data);tf.addfile(item,io.BytesIO(data))
                files[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':0o755}
        return {'scope':'PUBLIC_ARM64_RPC_MOCK_SELFTESTS','files':files,
                'archive_sha256':m.sha(self.path),'synthetic_data_only':True,
                'fastrpc_call_mocked_to_fail':True,'hardware_qualification_implied':False}
    def test_exact_inventory(self):
        self.assertEqual(set(m.archive_entries(self.path,self.fixture())),set(m.NAMES))
    def test_archive_hash_change_refused(self):
        f=self.fixture();f['archive_sha256']='0'*64
        with self.assertRaises(ValueError):m.archive_entries(self.path,f)
    def test_device_link_refused(self):
        with self.assertRaises(ValueError):m.archive_entries(self.path,self.fixture(kind='link'))
    def test_duplicate_refused(self):
        with self.assertRaises(ValueError):m.archive_entries(self.path,self.fixture([*m.NAMES,m.NAMES[0]]))
    def test_wrong_architecture_refused(self):
        with self.assertRaises(ValueError):m.archive_entries(self.path,self.fixture(arch=62))
    def test_unknown_or_traversal_inventory_refused(self):
        for name in ['/absolute','../outside','test_other']:
            with self.subTest(name=name),self.assertRaises(ValueError):
                m.archive_entries(self.path,self.fixture([m.NAMES[0],name]))
    def test_hardware_claim_refused(self):
        f=self.fixture();f['hardware_qualification_implied']=True
        with self.assertRaises(ValueError):m.archive_entries(self.path,f)

class Sandbox(unittest.TestCase):
    def exercise(self,mode='success'):
        self.calls=[]
        def run(args,**kwargs):
            self.calls.append(args)
            if args[0].endswith('systemd-run'):
                if mode=='timeout':raise subprocess.TimeoutExpired(args,18)
                return SimpleNamespace(returncode=0,stdout='\n'.join(m.MARKERS)+'\n',stderr='')
            if args[1]=='stop':return SimpleNamespace(returncode=0,stdout='',stderr='')
            if args[1]=='is-active':return SimpleNamespace(returncode=0 if mode=='active' else 3,stdout='active' if mode=='active' else 'inactive',stderr='')
            raise AssertionError(args)
        with contextlib.redirect_stdout(io.StringIO()):return m.run_tests(run=run)
    def test_bounds_and_no_device_access(self):
        cmd=m.command();s=' '.join(cmd)
        for item in ['User=65534','CapabilityBoundingSet=','PrivateDevices=yes','DevicePolicy=closed','ProtectSystem=strict','RuntimeMaxSec=12s','PrivateNetwork=yes']:
            self.assertIn('--property='+item,cmd)
        self.assertNotIn('DeviceAllow=',s);self.assertNotIn('fastrpc-adsp',s)
    def test_success_is_synthetic_only(self):
        result=self.exercise();self.assertEqual(result['checks_passed'],2)
        self.assertFalse(result['hardware_peripheral_qualification_implied'])
    def test_timeout_still_requests_stop_once(self):
        with self.assertRaises(subprocess.TimeoutExpired):self.exercise('timeout')
        self.assertEqual(sum(len(c)>1 and c[1]=='stop' for c in self.calls),1)
    def test_active_unit_refuses_pass(self):
        with self.assertRaises(ValueError):self.exercise('active')

if __name__=='__main__':unittest.main()
