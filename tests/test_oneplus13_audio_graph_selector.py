# SPDX-License-Identifier: GPL-2.0-only
import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'tools/oneplus13/audio-draft/build-topology-drafts.py'
s=importlib.util.spec_from_file_location('graph_selector',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Selector(unittest.TestCase):
    def test_decoded_hardware_tuple_not_token_declaration(self):
        text="'device18.i2s_rx1:tuple0' {\n tuples {\n token251 0\n token250 1\n token256 2\n }\n\t}\n"
        m.verify_i2s(text)
        for bad in [text.replace('token256 2','token256 0'),text.replace('token250 1','token250 0'),
                    text.replace('token251 0','token251 1'),text+text,
                    'SectionVendorTokens { token256 2 }']:
            with self.assertRaises(ValueError):m.verify_i2s(bad)
if __name__=='__main__':unittest.main()
