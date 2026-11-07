"""Synthetic loader controls, no proprietary ROM or devices."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('sensor_loader',Path(__file__).resolve().parents[1]/'tools/oneplus13/review-sensor-loader.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

def config():
    return 'version=2\n'+''.join(f'file={k}={v}\n' for k,v in s.FILES.items())+''.join(f'property={k}={v}\n' for k,v in s.PROPERTIES.items())

class Loader(unittest.TestCase):
    def test_exact_configuration_and_comments_accepted(self):
        self.assertEqual(len(s.parse_config('# synthetic\n'+config())),12)

    def test_duplicate_unreviewed_property_path_and_version_rejected(self):
        for text in [config()+'version=2\n',config().replace('version=2','version=3'),
                     config().replace('persist.vendor.sensors.enable.property1','ro.unreviewed'),
                     config().replace('/sys/devices/soc0/soc_id','/unreviewed/soc_id')]:
            with self.subTest(text=text),self.assertRaises(ValueError):s.parse_config(text)

    def test_index_missing_files_counted_and_valid_json_parsed(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'json_list';p.write_text('a.json\nb.json\n');(p.parent/'a.json').write_text('{}')
            r=s.index_report(p);self.assertEqual((r['entries'],r['present'],r['missing']),(2,1,1))
            self.assertFalse(r['all_present'])

    def test_stock_duplicate_index_entries_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'json.lst';p.write_text('a.json\na.json\n');(p.parent/'a.json').write_text('{}')
            r=s.index_report(p);self.assertEqual(r['entries'],2)
            self.assertEqual(r['unique_entries'],1);self.assertEqual(r['duplicate_occurrences'],1)

    def test_index_traversal_and_malformed_json_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'json_list'
            for content in ['../a.json\n','/a.json\n']:
                p.write_text(content)
                with self.assertRaises(ValueError):s.index_report(p)
            p.write_text('a.json\n');(p.parent/'a.json').write_text('{')
            with self.assertRaises(ValueError):s.index_report(p)

if __name__=='__main__':unittest.main()
