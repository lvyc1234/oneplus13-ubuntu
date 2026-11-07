import hashlib,json,sys,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/oneplus13'))
from verify_qualified_data import verify
from stock import Fdt

class QualifiedDataTests(unittest.TestCase):
    def test_real_dataset_is_complete_and_identity_free(self):
        result=verify(ROOT/'devices/oneplus13/qualified-data')
        self.assertEqual(result['files'],124)
        self.assertEqual(result['firmware_files'],120)

    def fixture(self,path):
        data=ROOT/'devices/oneplus13/qualified-data'
        dt=(data/'calibrated.dtb').read_bytes()
        (path/'calibrated.dtb').write_bytes(dt)
        (path/'PROVENANCE.md').write_text('fixture')
        pin={'format':'ONEPLUS13_QUALIFIED_DATA_V1','files':{'calibrated.dtb':{'sha256':hashlib.sha256(dt).hexdigest(),'bytes':len(dt)}}}
        (path/'manifest.json').write_text(json.dumps(pin))
        return pin

    def test_unreviewed_file_cannot_enter_dataset(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);self.fixture(p)
            (p/'network.conf').write_text('unexpected')
            with self.assertRaisesRegex(ValueError,'unreviewed'):verify(p)

    def test_changed_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);self.fixture(p)
            with (p/'calibrated.dtb').open('ab') as f:f.write(b'x')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):verify(p)

    def test_identifier_property_is_rejected_even_with_updated_hash(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);pin=self.fixture(p)
            dt=Fdt((p/'calibrated.dtb').read_bytes())
            dt.nodes['/']['serial-number']=b'synthetic-test-identity\0'
            blob=dt.to_blob();(p/'calibrated.dtb').write_bytes(blob)
            pin['files']['calibrated.dtb']={'sha256':hashlib.sha256(blob).hexdigest(),'bytes':len(blob)}
            (p/'manifest.json').write_text(json.dumps(pin))
            with self.assertRaisesRegex(ValueError,'identity'):verify(p)

if __name__=='__main__':unittest.main()
