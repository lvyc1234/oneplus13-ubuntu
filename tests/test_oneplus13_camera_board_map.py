import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'tools/oneplus13/review-camera-board-map.py'
s=importlib.util.spec_from_file_location('camera_map',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
BASE={'properties':set(m.REQUIRED),'status':'ok','eeprom_known':True,'position_complete':True,'front_reg_present':False}
def row(i):
    x=dict(BASE);x.update(cell_index=i,phy=m.EXPECTED[i]['phy'],bus=m.EXPECTED[i]['bus'])
    if i==3:x['front_reg_present']=True
    if i==1:x['probe_intent']=0x30e0
    return x
class Tests(unittest.TestCase):
    def test_vendor_or_selects_cphy_templates_without_relaxing_physical_id(self):
        out=m.validate_records([row(i) for i in range(4)])['ultrawide_selection']
        templates={x['template']:x for x in out['templates']}
        self.assertEqual(templates['D']['effective_id'],0x30e0)
        self.assertFalse(templates['D']['matches_observed_chip_id'])
        for name in ('L','S'):
            self.assertEqual(templates[name]['effective_id'],0x38e5)
            self.assertEqual(templates[name]['phy_mode_inferred'],'CPHY')
            self.assertEqual(templates[name]['lanes_or_trios'],3)
        self.assertFalse(out['supplier_selected'])
        self.assertFalse(out['camera_capture_qualified'])
    def test_missing_or_changed_ultrawide_probe_intent_refused(self):
        for value in (None,0,0x38e5,0x30e1,'0x30e0',True):
            bad=row(1);bad['probe_intent']=value
            with self.subTest(value=value),self.assertRaises(ValueError):
                m.validate_records([row(0),bad,row(2),row(3)])
    def test_phone_graph_contract(self):
        out=m.validate_records([row(i) for i in range(4)]);self.assertEqual(out['phy_indices'],[5,1,3,4]);self.assertFalse(out['camera_capture_qualified'])
    def test_duplicate_index_refused(self):
        with self.assertRaises(ValueError):m.validate_records([row(0),row(0),row(2),row(3)])
    def test_wrong_bus_or_phy_refused(self):
        bad=row(1);bad['phy']=5
        with self.assertRaises(ValueError):m.validate_records([row(0),bad,row(2),row(3)])
    def test_missing_power_property_refused(self):
        bad=row(2);bad['properties']=set(m.REQUIRED)-{'cam_vana-supply'}
        with self.assertRaises(ValueError):m.validate_records([row(0),row(1),bad,row(3)])
    def test_unresolved_eeprom_refused(self):
        bad=row(0);bad['eeprom_known']=False
        with self.assertRaises(ValueError):m.validate_records([bad,row(1),row(2),row(3)])
    def test_incomplete_orientation_refused(self):
        bad=row(3);bad['position_complete']=False
        with self.assertRaises(ValueError):m.validate_records([row(0),row(1),row(2),bad])
    def test_front_address_shape_refused(self):
        bad=row(3);bad['front_reg_present']=False
        with self.assertRaises(ValueError):m.validate_records([row(0),row(1),row(2),bad])
if __name__=='__main__':unittest.main()
