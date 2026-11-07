# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
helper_path=Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-wifi-quiescent.py'
helper_spec=importlib.util.spec_from_file_location('quiescent',helper_path)
draft=importlib.util.module_from_spec(helper_spec);helper_spec.loader.exec_module(draft)

path=Path(__file__).resolve().parents[1]/'tools/oneplus13/automatic-wifi-check.py'
spec=importlib.util.spec_from_file_location('real_wifi_country',path)
wifi=importlib.util.module_from_spec(spec);spec.loader.exec_module(wifi)

class Fake:
    def __init__(self,states=None,associated=False,global_only=False):
        self.states=list(states or []);self.associated=associated;self.global_only=global_only
        self.calls=[];self.sets=0
    def __call__(self,args,seconds):
        self.calls.append(args)
        if args[0]=='nmcli':return 0,str(self.states.pop(0) if self.states else 10)+' (state)\n'
        if args[-1]=='link':return 0,'Connected to synthetic peer\n' if self.associated else 'Not connected.\n'
        if args[-1]=='info':return 0,'\twiphy 0\n'
        if args[:3]==['iw','reg','set']:self.sets+=1;return 0,''
        if args==['iw','reg','get']:
            global_code='CN' if self.sets and self.global_only else '00'
            phy_code='CN' if self.sets and not self.global_only else '00'
            return 0,'global\ncountry '+global_code+': DFS-UNSET\nphy#0 (self-managed)\ncountry '+phy_code+': DFS-UNSET\n'
        raise AssertionError('Unexpected command')

class Idle(unittest.TestCase):
    def test_managed_device_cannot_reach_country_hint(self):
        fake=Fake(states=[30])
        with self.assertRaisesRegex(ValueError,'unmanaged'):draft.country_when_quiescent(wifi,'wlan-test','CN',fake)
        self.assertEqual(fake.sets,0)
    def test_management_race_refuses_even_first_link_query(self):
        fake=Fake(states=[10,30])
        with self.assertRaisesRegex(ValueError,'unmanaged'):draft.country_when_quiescent(wifi,'wlan-test','CN',fake)
        self.assertEqual(fake.sets,0);self.assertTrue(all(c[0]=='nmcli' for c in fake.calls))
    def test_association_refuses_hint(self):
        fake=Fake(associated=True)
        with self.assertRaisesRegex(ValueError,'acknowledge'):draft.country_when_quiescent(wifi,'wlan-test','CN',fake)
        self.assertEqual(fake.sets,0)
    def test_actual_country_helper_requires_selected_phy_ack_and_sends_once(self):
        fake=Fake();facts=draft.country_when_quiescent(wifi,'wlan-test','CN',fake)
        self.assertTrue(facts['country_phy_acknowledged']);self.assertEqual(fake.sets,1)
        for i,args in enumerate(fake.calls):
            if args[0]=='iw':self.assertEqual(fake.calls[i-1][0],'nmcli')
    def test_global_domain_alone_does_not_release_hold(self):
        fake=Fake(global_only=True)
        with patch.object(wifi.time,'sleep'),self.assertRaisesRegex(ValueError,'acknowledge'):
            draft.country_when_quiescent(wifi,'wlan-test','CN',fake)
        self.assertEqual(fake.sets,1)
    def test_effective_config_must_preserve_usb_and_have_no_wifi_exception(self):
        for value in ['type:wifi',draft.USB_TOKEN+';except:type:wifi',draft.USB_TOKEN,
                      draft.USB_TOKEN+';type:wifi;except:interface-name:wlan-test']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                draft.effective_config(lambda *a:(0,'[keyfile]\nunmanaged-devices='+value+'\n'))

    def test_new_kernel_fault_stops_before_country_hint(self):
        health_path=Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-kernel-health.py'
        health_spec=importlib.util.spec_from_file_location('quiescent_health',health_path)
        health=importlib.util.module_from_spec(health_spec);health_spec.loader.exec_module(health)
        fake=Fake();histories=iter(['healthy\n','ath12k_wifi7_pci: failed to start next scan range: -110\n'])
        def run(args,seconds):
            if args==['dmesg']:return 0,next(histories)
            return fake(args,seconds)
        with self.assertRaisesRegex(ValueError,'fault'):
            draft.country_when_quiescent(wifi,'wlan-test','CN',run,health)
        self.assertEqual(fake.sets,0)

    def test_freshness_uses_host_clock_not_unsynchronized_guest_clock(self):
        value='2026-10-27T12:47:07.193247+00:00'
        imported=__import__('datetime').datetime
        stamp=int(imported.fromisoformat(value).timestamp())
        draft.stock_observation_fresh(value,stamp+60)
        for rejected in [None,stamp-1,stamp+3601]:
            with self.subTest(host_epoch=rejected),self.assertRaises(ValueError):
                draft.stock_observation_fresh(value,rejected)

if __name__=='__main__':unittest.main()
