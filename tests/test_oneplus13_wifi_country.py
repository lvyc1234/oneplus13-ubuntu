# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[1]/'tools/oneplus13/automatic-wifi-check.py'
spec=importlib.util.spec_from_file_location('wifi_country',path)
wifi=importlib.util.module_from_spec(spec);spec.loader.exec_module(wifi)

class StockCountry(unittest.TestCase):
    def run_hint(self,mode='ack'):
        self.calls=[];self.reads=0;self.links=0
        def command(label,args,seconds):
            self.calls.append(args)
            if args[-1]=='link':
                self.links+=1
                if mode=='connected' or (mode=='association_race' and self.links==2):return 0,'Connected to private\nfreq: 2437\n'
                return 0,'Not connected.\n'
            if args[-1]=='info':return 0,'Interface private\n\twiphy 3\n'
            if args[-1]=='get':
                self.reads+=1
                country='CN' if mode=='already' or (mode=='ack' and self.reads>1) else '00'
                target='4' if mode=='wrong_phy' else '3'
                if mode=='global_only':return 0,'global\ncountry CN: DFS-FCC\n'
                return 0,'global\ncountry CN: DFS-FCC\nphy#'+target+' (self-managed)\ncountry '+country+': DFS-FCC\n'
            return (1,'private failure') if mode=='set_failure' else (0,'')
        with patch.object(wifi.time,'sleep'):
            return wifi.set_stock_country('wlan-test','CN',command)

    def test_selected_self_managed_ack_after_exactly_one_hint(self):
        result=self.run_hint()
        self.assertTrue(result['country_hint_sent'] and result['country_phy_acknowledged'])
        self.assertEqual(sum(c[1:3]==['reg','set'] for c in self.calls),1)

    def test_already_matching_phy_does_not_send_a_hint(self):
        result=self.run_hint('already')
        self.assertTrue(result['country_phy_acknowledged'])
        self.assertFalse(result['country_hint_sent'])

    def test_association_and_association_race_both_refuse_set(self):
        for mode in ['connected','association_race']:
            with self.subTest(mode=mode):
                result=self.run_hint(mode)
                self.assertTrue(result['country_refused_associated_or_unknown'])
                self.assertFalse(any(c[1:3]==['reg','set'] for c in self.calls))

    def test_global_domain_or_other_phy_cannot_fake_ack(self):
        for mode in ['global_only','wrong_phy','no_ack']:
            with self.subTest(mode=mode):
                result=self.run_hint(mode)
                self.assertFalse(result['country_phy_acknowledged'])
                self.assertEqual(result['country_ack_observations'],6)
                self.assertEqual(sum(c[1:3]==['reg','set'] for c in self.calls),1)

    def test_failed_hint_is_not_retried(self):
        result=self.run_hint('set_failure')
        self.assertFalse(result['country_hint_sent'] or result['country_phy_acknowledged'])
        self.assertEqual(sum(c[1:3]==['reg','set'] for c in self.calls),1)

    def test_invalid_country_never_executes(self):
        for country in ['00','cn','CN; reboot',None,42]:
            with self.subTest(country=country),self.assertRaises(ValueError):
                wifi.set_stock_country('wlan-test',country,lambda *args:self.fail('must not execute'))

if __name__=='__main__':unittest.main()
