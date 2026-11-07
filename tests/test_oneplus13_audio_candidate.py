# SPDX-License-Identifier: GPL-2.0-only
import copy, hashlib, importlib.util, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/oneplus13'))
from stock import Fdt,cells,strings
spec=importlib.util.spec_from_file_location('audio_dtb',ROOT/'tools/oneplus13/audio-dtb.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class AudioCandidate(unittest.TestCase):
    def setUp(self):
        self.base=(ROOT/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes()

    def test_only_audio_nodes_change_and_reservations_are_preserved(self):
        out,facts=m.derive(self.base)
        old,new=Fdt(self.base),Fdt(out)
        allowed=set(facts['existing_nodes_changed'])
        for path,props in old.nodes.items():
            if path not in allowed:self.assertEqual(props,new.nodes[path],path)
        self.assertEqual(old.reservations,new.reservations)
        self.assertTrue(facts['nonaudio_properties_and_reservations_preserved'])
        self.assertFalse(facts['audio_accepted'])
        self.assertFalse(facts['dynamic_protection_qualified'])

    def test_exact_two_pid2308_amps_reset_polarity_and_clock(self):
        out,_=m.derive(self.base);tree=Fdt(out)
        for channel,addr,pin in [(0,0x34,2),(1,0x35,164)]:
            props=tree.nodes[m.I2C+f'/audio-codec@{addr:x}']
            self.assertEqual(strings(props['compatible']),['awinic,aw88461'])
            self.assertEqual(cells(props['reset-gpios'])[1:],[pin,1])
            self.assertEqual(cells(props['awinic,audio-channel']),[channel])
        clock=tree.nodes[m.BEDAI+'/dai@12']
        self.assertEqual(cells(clock['reg']),[18])
        self.assertEqual(cells(clock['clocks'])[1:],[3,1])
        self.assertEqual(strings(tree.nodes['/sound']['model']),['OnePlus13-Speaker-Test'])

    def test_changed_base_or_second_derivation_rejected(self):
        for blob in [self.base+b'x',m.derive(self.base)[0]]:
            with self.assertRaises(ValueError):m.derive(blob)

    def test_generic_owner_matched_acf_is_hash_bound(self):
        path=ROOT/'devices/oneplus13/audio/aw88461_acf.bin'
        self.assertEqual(path.stat().st_size,1792)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         '941f23e9e315820a01170a97dbb0c88382c46a548993e5d2ca98c7619b1b1847')

if __name__=='__main__':unittest.main()
