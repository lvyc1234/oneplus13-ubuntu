# SPDX-License-Identifier: GPL-2.0-only
import copy,importlib.util,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('preservation',Path(__file__).resolve().parents[1]/'tools/oneplus13/verify-preserved-features.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Gate(unittest.TestCase):
    def setUp(self):
        self.state={'stages_completed':True,'android_return_confirmed':True,'baseline_keys_match':True,'partition_writes':0,'display_activation':True,
          'automatic_gpu':{'readback_matches':True,'hardware_renderer':True,'project_mesa_loaded':True,'gl_error':0},
          'automatic_desktop':{'software_acceptance_pass':True,'combined_pass':True},
          'automatic_wifi':{k:True for k in ['scan_completed','target_seen','association_pass','dhcp_pass','https_pass','automatic_acceptance_pass','ipv4_global_address_present','ipv4_default_route_present']},
          'passive_platform':{'power_supplies':[{'type':'Battery','capacity':'42'}],'all_observed_block_devices_read_only':True,'writable_block_devices_observed':0}}
        self.state['automatic_wifi']['https_metrics']={'ssl_verify_result':0,'http_code':200}
        self.log='BT_STACK_COMPONENTS_PRESERVED\nPowered: yes\nSynaptics S3910\n'
    def run_gate(self):return module.evaluate(self.state,self.log,True,True)
    def test_synthetic_complete_observation(self):self.assertTrue(self.run_gate()['delivery_eligible'])
    def test_wifi_missing_never_merges_other_features(self):
        self.state['automatic_wifi']['target_seen']=False;self.assertFalse(self.run_gate()['delivery_eligible'])
    def test_tls_failure_is_not_connectivity_success(self):
        self.state['automatic_wifi']['https_metrics']['ssl_verify_result']=9;self.assertFalse(self.run_gate()['delivery_eligible'])
    def test_bluetooth_binary_marker_without_controller_is_insufficient(self):
        self.log=self.log.replace('Powered: yes','Powered: no');self.assertFalse(self.run_gate()['delivery_eligible'])
    def test_artifact_or_baseline_mismatch_blocks_delivery(self):
        self.assertFalse(module.evaluate(self.state,self.log,False,True)['delivery_eligible'])
        self.assertFalse(module.evaluate(self.state,self.log,True,False)['delivery_eligible'])
    def test_gpu_or_touch_regression_blocks_delivery(self):
        self.state['automatic_gpu']['hardware_renderer']=False;self.assertFalse(self.run_gate()['delivery_eligible'])
        self.state['automatic_gpu']['hardware_renderer']=True;self.log=self.log.replace('Synaptics S3910','');self.assertFalse(self.run_gate()['delivery_eligible'])
    def test_failure_or_writable_storage_blocks_delivery(self):
        self.state['failure']='synthetic';self.assertFalse(self.run_gate()['delivery_eligible']);del self.state['failure']
        self.state['passive_platform']['all_observed_block_devices_read_only']=False;self.assertFalse(self.run_gate()['delivery_eligible'])

if __name__=='__main__':unittest.main()
