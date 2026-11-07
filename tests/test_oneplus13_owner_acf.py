# SPDX-License-Identifier: GPL-2.0-only
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('owner_acf',Path(__file__).resolve().parents[1]/'tools/oneplus13/review-owner-acf.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class OwnerAcfTests(unittest.TestCase):
    def setUp(self):
        self.data=b'synthetic input, no register data'
        self.metadata=f'FILE {len(self.data)} /odm/firmware/aw882xx_acf.bin\n{hashlib.sha256(self.data).hexdigest()}  -\n'
    def test_match_keeps_independent_stream_gates_closed(self):
        result=m.review(self.data,self.metadata)
        self.assertTrue(result['saved_owner_acf_content_matched'])
        for k in ['physical_chip_id_verified','factory_calibration_verified','dynamic_speaker_protection_verified','stream_eligible']:
            self.assertFalse(result[k])
    def test_changed_bytes_rejected(self):
        with self.assertRaises(ValueError):m.review(self.data+b'x',self.metadata)
    def test_ambiguous_duplicate_rejected(self):
        with self.assertRaises(ValueError):m.review(self.data,self.metadata*2)
    def test_other_device_path_rejected(self):
        with self.assertRaises(ValueError):m.review(self.data,self.metadata.replace('/odm/firmware/','/vendor/firmware/'))

if __name__=='__main__':unittest.main()
