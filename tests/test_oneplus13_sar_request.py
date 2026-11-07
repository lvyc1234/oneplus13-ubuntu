# SPDX-License-Identifier: GPL-2.0-only
import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'tools/oneplus13/review-sar-request.py'
spec=importlib.util.spec_from_file_location('sar_request',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Sar(unittest.TestCase):
    def test_vendor_user_unit_is_half_dbm_no_effective_power_claim(self):
        r=m.interpret({'selection':'user','rows':[{'power_half_dbm':11}]})
        self.assertEqual(r['qca_selection'],6);self.assertEqual(r['rows'][0]['requested_limit_dbm'],'5.5')
        self.assertIsNone(r['effective_transmit_power_dbm']);self.assertFalse(r['hardware_radio_eligible'])
        self.assertFalse(r['command_or_wire_payload_generated'])
    def test_saved_fallback_80_is_request_only(self):
        r=m.interpret({'selection':'user','rows':[{'chain':0,'power_half_dbm':80},{'chain':1,'power_half_dbm':80}]})
        self.assertEqual([x['requested_limit_dbm'] for x in r['rows']],['40','40'])
        self.assertFalse(r['regulatory_or_phone_sar_policy_accepted'])
    def test_v2_index_is_not_power(self):
        r=m.interpret({'selection':'v2','rows':[{'power_index':11}]})
        self.assertEqual(r['qca_selection'],7);self.assertNotIn('requested_limit_dbm',r['rows'][0])
        self.assertEqual(r['rows'][0]['bdf_profile_index'],11)
    def test_missing_rows_or_mixed_namespace_rejected(self):
        for selection,rows in [('user',[]),('v2',[]),('user',[{'power_index':1}]),('v2',[{'power_half_dbm':1}]),
                ('user',[{'power_index':1,'power_half_dbm':1}])]:
            with self.assertRaises(ValueError):m.interpret({'selection':selection,'rows':rows})
    def test_invalid_numeric_values_rejected(self):
        for value in [True,-1,0x100000000,1.5,'11',None]:
            with self.assertRaises(ValueError):m.interpret({'selection':'user','rows':[{'power_half_dbm':value}]})
        with self.assertRaises(ValueError):m.interpret({'selection':'v2','rows':[{'power_index':12}]})
    def test_profiles_and_disable_are_distinct(self):
        r=m.interpret({'selection':'bdf0','rows':[]});self.assertFalse(r['disable_requested'])
        self.assertTrue(m.interpret({'selection':'none','rows':[]})['disable_requested'])
        with self.assertRaises(ValueError):m.interpret({'selection':'bdf4','rows':[{'power_half_dbm':1}]})
    def test_duplicate_or_overlapping_rows_rejected(self):
        for rows in [[{'power_half_dbm':1},{'power_half_dbm':2}],
                [{'power_half_dbm':1},{'chain':1,'power_half_dbm':2}]]:
            with self.assertRaises(ValueError):m.interpret({'selection':'user','rows':rows})
    def test_unknown_fields_or_selector_rejected(self):
        for req in [{'selection':'default','rows':[]},{'selection':'user','rows':[{'band':2,'power_half_dbm':1}]},
                {'selection':'user','rows':[{'power_half_dbm':1}],'send':True}]:
            with self.assertRaises(ValueError):m.interpret(req)

if __name__=='__main__':unittest.main()
