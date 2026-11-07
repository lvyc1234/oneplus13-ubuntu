"""Synthetic FDT controls: no private FDT or hardware required."""
import copy
import importlib.util
from pathlib import Path
import struct
import sys
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools/oneplus13'
sys.path.insert(0,str(TOOLS))
from stock import Fdt
spec=importlib.util.spec_from_file_location('audio_topology',TOOLS/'review-audio-topology.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)


def tree(nodes):
    result=object.__new__(Fdt);result.nodes=nodes
    result.phandles={struct.unpack('>I',v['phandle'])[0]:p for p,v in nodes.items() if 'phandle' in v}
    result.reservations=[[0x1000,0x100]]
    return Fdt(result.to_blob())


class AudioTopology(unittest.TestCase):
    def setUp(self):
        self.stock=tree({'/':{},'/soc':{},'/soc/wsa':{'status':b'disabled\0'},
                         '/soc/wsa/spk':{'compatible':b'qcom,wsa884x\0'},
                         '/soc/aw0':{'compatible':b'awinic,aw882xx_smartpa\0'},
                         '/soc/aw1':{'compatible':b'awinic,aw882xx_smartpa\0'}})
        self.candidate=tree({'/':{},'/soc':{},'/soc/display':{'status':b'okay\0','test':b'unchanged\0'},
                             '/soc/swr':{'compatible':b'qcom,soundwire-v2.1.0\0','status':b'okay\0'},
                             '/soc/swr/l':{'compatible':b'sdw20217020400\0','phandle':struct.pack('>I',1)},
                             '/soc/swr/r':{'compatible':b'sdw20217020400\0','phandle':struct.pack('>I',2)},
                             '/sound':{},'/sound/wsa-playback-dai-link':{},
                             '/sound/wsa-playback-dai-link/codec':{'sound-dai':struct.pack('>2I',1,2)},
                             '/sound/wsa-playback-dai-link/cpu':{}})

    def test_disabled_ancestor_overrides_missing_child_status(self):
        self.assertFalse(a.enabled(self.stock.nodes,'/soc/wsa/spk'))
        self.assertEqual(a.inventory(self.stock.nodes)['enabled_wsa_nodes'],0)

    def test_only_status_and_one_link_subtree_change(self):
        blob,r=a.quarantine(self.stock,self.candidate);new=Fdt(blob)
        self.assertTrue(r['nonaudio_quarantine_complete'])
        self.assertFalse(r['eligible_for_wsa_stream'])
        self.assertEqual(new.nodes['/soc/display'],self.candidate.nodes['/soc/display'])
        self.assertEqual(new.reservations,self.candidate.reservations)
        self.assertNotIn('/sound/wsa-playback-dai-link',new.nodes)
        self.assertEqual(a.inventory(new.nodes)['enabled_wsa_nodes'],0)
        self.assertEqual(a.inventory(self.candidate.nodes)['enabled_wsa_nodes'],2)

    def test_stock_active_wsa_cannot_be_silently_quarantined(self):
        self.stock.nodes['/soc/wsa']['status']=b'okay\0'
        with self.assertRaises(ValueError):a.quarantine(self.stock,self.candidate)

    def test_missing_aw_pair_or_candidate_pair_refused(self):
        del self.stock.nodes['/soc/aw1']
        with self.assertRaises(ValueError):a.quarantine(self.stock,self.candidate)
        self.setUp();del self.candidate.nodes['/soc/swr/r']
        with self.assertRaises(ValueError):a.quarantine(self.stock,self.candidate)

    def test_other_link_reference_refused(self):
        self.candidate.nodes['/sound/another']={'sound-dai':struct.pack('>I',1)}
        with self.assertRaises(ValueError):a.quarantine(self.stock,self.candidate)

    def test_disabled_root_and_unknown_status_are_not_enabled(self):
        for status in [b'disabled\0',b'reserved\0',b'fail\0']:
            nodes=copy.deepcopy(self.candidate.nodes);nodes['/']['status']=status
            self.assertFalse(a.enabled(nodes,'/soc/swr/l'))


if __name__=='__main__':unittest.main()
