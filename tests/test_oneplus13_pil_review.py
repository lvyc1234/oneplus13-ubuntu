import importlib.util
from pathlib import Path
import struct,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools/oneplus13'))
spec=importlib.util.spec_from_file_location('pil',Path(__file__).resolve().parents[1]/'tools/oneplus13/review-pil-firmware.py')
pil=importlib.util.module_from_spec(spec);spec.loader.exec_module(pil)

def fixture(folder,offset=0,filesz=4,memsz=4096,flags=pil.RELOCATABLE|5):
    ident=b'\x7fELF\x01\x01\x01'+b'\0'*9
    header=pil.EHDR.pack(ident,2,164,1,0,52,0,0,52,32,3,0,0,0)
    ph=[(0,0,0,0,148,0,7<<24,0),
        (1,4096,0x100000,0x100000+offset,filesz,memsz,flags,4096),
        (0,8192,0,0,16,16,pil.TYPE_HASH,4096)]
    packed=header+b''.join(pil.PHDR.pack(*p) for p in ph)
    (folder/'adsp.mdt').write_bytes(packed+b'H'*16)
    (folder/'adsp.b00').write_bytes(packed)
    (folder/'adsp.b01').write_bytes(b'DATA')
    (folder/'adsp.b02').write_bytes(b'H'*16)

class PilReview(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);fixture(self.root)
    def tearDown(self):self.temp.cleanup()
    def inspect(self,size=8192):return pil.inspect_split(self.root,'adsp',0x90000000,size)
    def test_valid_relocatable_split_is_structure_only(self):
        result=self.inspect();self.assertTrue(result['relocatable'])
        self.assertEqual(result['required_span_bytes'],4096)
        self.assertFalse(result['signature_authenticated'])
    def test_short_file(self):
        (self.root/'adsp.b01').write_bytes(b'BAD')
        with self.assertRaises(ValueError):self.inspect()
    def test_missing_segment(self):
        (self.root/'adsp.b01').unlink()
        with self.assertRaises(ValueError):self.inspect()
    def test_stale_segment_is_rejected(self):
        (self.root/'adsp.b24').write_bytes(b'STALE')
        with self.assertRaises(ValueError):self.inspect()
    def test_hash_and_header_consistency(self):
        for name in ['adsp.b00','adsp.b02']:
            with self.subTest(name=name):
                fixture(self.root);data=(self.root/name).read_bytes()
                (self.root/name).write_bytes(b'X'+data[1:])
                with self.assertRaises(ValueError):self.inspect()
    def test_carveout_bounds(self):
        with self.assertRaises(ValueError):self.inspect(size=4095)
    def test_file_larger_than_memory(self):
        fixture(self.root,memsz=3)
        with self.assertRaises(ValueError):self.inspect()
    def test_fixed_address_cannot_relocate_silently(self):
        fixture(self.root,flags=5)
        with self.assertRaises(ValueError):self.inspect()
    def test_truncated_program_table(self):
        (self.root/'adsp.mdt').write_bytes((self.root/'adsp.mdt').read_bytes()[:100])
        with self.assertRaises(ValueError):self.inspect()

if __name__=='__main__':unittest.main()
