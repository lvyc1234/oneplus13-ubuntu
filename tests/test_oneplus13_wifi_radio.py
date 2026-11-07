# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest

path=Path(__file__).resolve().parents[1]/'tools/oneplus13/wifi-radio-report.py'
spec=importlib.util.spec_from_file_location('radio',path)
radio=importlib.util.module_from_spec(spec);spec.loader.exec_module(radio)

class RadioFacts(unittest.TestCase):
    def test_distinguishes_disabled_passive_dfs_and_6ghz(self):
        text='''Wiphy private-radio
        * 2437.0 MHz [6] (20.0 dBm)
        * 5180.0 MHz [36] (23.0 dBm) (no IR)
        * 5260 MHz [52] (20.0 dBm) (radar detection)
        * 5745 MHz [149] (disabled)
        * 5975 MHz [5] (23.0 dBm)
'''
        result=radio.channel_facts(text)
        bands=result['radios'][0]['bands']
        self.assertEqual(result['radio_count'],1)
        self.assertEqual(len(bands['5GHz']),3)
        self.assertTrue(bands['5GHz'][0]['no_ir'])
        self.assertTrue(bands['5GHz'][1]['radar'])
        self.assertTrue(bands['5GHz'][2]['disabled'])
        self.assertEqual(len(bands['6GHz']),1)
        self.assertNotIn('private-radio',json.dumps(result))

    def test_self_managed_domain_is_distinct_from_global(self):
        result=radio.regulatory_facts('global\ncountry 00: DFS-UNSET\nphy#0 (self-managed)\ncountry CN: DFS-FCC\n')
        self.assertEqual(result['self_managed_radio_count'],1)
        self.assertEqual([d['alpha2'] for d in result['domains']],['00','CN'])

    def test_collection_is_read_only_and_bounded(self):
        calls=[]
        def execute(command,**kwargs):
            calls.append(command)
            self.assertEqual(kwargs['timeout'],5)
            return SimpleNamespace(returncode=0,stdout='private error text')
        result=radio.collect(execute)
        self.assertEqual(calls,[['iw','phy'],['iw','reg','get']])
        self.assertNotIn('private error text',json.dumps(result))
        self.assertFalse(result['scan_requested'] or result['country_or_sar_set'])

    def test_timeouts_do_not_leak_partial_output(self):
        def execute(*args,**kwargs):raise subprocess.TimeoutExpired('iw',5,output='private')
        result=radio.collect(execute)
        self.assertNotIn('private',json.dumps(result))
        self.assertEqual(result['channels_rc'],124)

if __name__=='__main__':unittest.main()
