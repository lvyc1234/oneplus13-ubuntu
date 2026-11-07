"""Offline checks for the read-only dual-band cache observer."""
import importlib.util,json
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'tools/oneplus13/wifi-band-cache.py'
spec=importlib.util.spec_from_file_location('bands',path)
bands=importlib.util.module_from_spec(spec);spec.loader.exec_module(bands)

class Bands(unittest.TestCase):
    def test_human_readable_mhz_and_duplicate_rows(self):
        result=bands.classify(
            'private-24:2437 MHz\nprivate-24:2437 MHz\nprivate-5:5240 MHz\n',
            {'ssid':'private-24','comparison_ssid':'private-5'})
        self.assertEqual([x['frequencies_mhz'] for x in result['targets']],[[2437],[5240]])
        self.assertNotIn('private-',json.dumps(result))
        self.assertFalse(result['requested_scan'])
    def test_missing_target_is_explicit(self):
        result=bands.classify(
            'private-24:2437 MHz\n',
            {'ssid':'private-24','comparison_ssid':'absent'})
        self.assertFalse(result['targets'][1]['seen'])

if __name__=='__main__':unittest.main()
