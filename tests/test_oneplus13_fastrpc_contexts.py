# SPDX-License-Identifier: GPL-2.0-only
import copy, importlib.util, sys, unittest
from pathlib import Path
TOOLS=Path(__file__).resolve().parents[1]/'tools/oneplus13'
sys.path.insert(0,str(TOOLS))
spec=importlib.util.spec_from_file_location('rpc_contexts',TOOLS/'review-fastrpc-contexts.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def u(*values):return b''.join(v.to_bytes(4,'big') for v in values)

def fixture(handle=1,wide=False):
    return {'/':{'#address-cells':u(2),'#size-cells':u(2)},
      '/soc':{'ranges':b'','#address-cells':u(2 if wide else 1),'#size-cells':u(2 if wide else 1)},
      '/soc/iommu':{'phandle':u(handle),'#iommu-cells':u(2),
          'reg':u(0,0x15000000,0,0x100000) if wide else u(0x15000000,0x100000)},
      '/heap':{'phandle':u(handle+1),'compatible':b'shared-dma-pool\0','reusable':b'',
          'size':u(0,0xc00000),'alignment':u(0,0x400000),'alloc-ranges':u(0,0,0,0xffffffff)},
      '/soc/rpc':{'compatible':b'qcom,fastrpc\0','memory-region':u(handle+1),'qcom,vmids':u(22,37)},
      '/soc/rpc/cb':{'compatible':b'qcom,fastrpc-compute-cb\0','reg':u(3),'iommus':u(handle,0x1003,0x80)}}

class Contexts(unittest.TestCase):
    def test_resolves_phandle_and_bus_geometry(self):
        self.assertEqual(m.normalize(fixture(),'/soc/rpc'),m.normalize(fixture(19,True),'/soc/rpc'))
    def test_controller_mmio_difference_is_visible(self):
        a=fixture();b=copy.deepcopy(a);b['/soc/iommu']['reg']=u(0x3da0000,0x100000)
        self.assertNotEqual(m.normalize(a,'/soc/rpc'),m.normalize(b,'/soc/rpc'))
    def test_sid_mask_difference_is_visible(self):
        a=fixture();b=copy.deepcopy(a);b['/soc/rpc/cb']['iommus']=u(1,0x1003,0x20)
        self.assertNotEqual(m.normalize(a,'/soc/rpc'),m.normalize(b,'/soc/rpc'))
    def test_heap_and_security_difference_is_visible(self):
        for node,key,value in [('/heap','size',u(0,0x1000000)),('/soc/rpc','qcom,non-secure-domain',b'')]:
            a=fixture();b=copy.deepcopy(a);b[node][key]=value
            self.assertNotEqual(m.normalize(a,'/soc/rpc'),m.normalize(b,'/soc/rpc'))
    def test_rejects_disabled_ancestors_and_controller(self):
        for path in ['/soc','/soc/rpc','/soc/rpc/cb','/soc/iommu']:
            a=fixture();a[path]['status']=b'disabled\0'
            with self.assertRaises(ValueError):m.normalize(a,'/soc/rpc')
    def test_rejects_unresolved_translation(self):
        for value in [None,u(0,0,0,0)]:
            a=fixture()
            if value is None:del a['/soc']['ranges']
            else:a['/soc']['ranges']=value
            with self.assertRaises(ValueError):m.normalize(a,'/soc/rpc')
    def test_translates_nonempty_ranges_and_rejects_unmapped(self):
        a=fixture();a['/soc']['ranges']=u(0,0,0,0xffffffff)
        self.assertEqual(m.normalize(a,'/soc/rpc'),m.normalize(fixture(19,True),'/soc/rpc'))
        a['/soc']['ranges']=u(0,0,0x10000000,0xffffffff)
        self.assertEqual(m.normalize(a,'/soc/rpc')['contexts'][0]['iommus'][0]['controller_mmio'],[[0x25000000,0x100000]])
        a['/soc']['ranges']=u(0,0,0,0x1000)
        with self.assertRaises(ValueError):m.normalize(a,'/soc/rpc')
    def test_rejects_truncated_iommu_args(self):
        a=fixture();a['/soc/rpc/cb']['iommus']=u(1,0x1003)
        with self.assertRaises(ValueError):m.normalize(a,'/soc/rpc')
    def test_rejects_unknown_child_and_duplicate_context(self):
        for extra in [{'compatible':b'other\0'},fixture()['/soc/rpc/cb']]:
            a=fixture();a['/soc/rpc/other']=extra
            with self.assertRaises(ValueError):m.normalize(a,'/soc/rpc')

if __name__=='__main__':unittest.main()
