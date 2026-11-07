#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Reject a delivery unless this exact artifact set passed joint regressions.

This reads private local evidence, emits no identifiers/credentials, and grants
no hardware authorization. It cannot substitute for owner visual/touch/peer tests.
"""
import argparse,hashlib,json,re
from pathlib import Path

def evaluate(state,regression,baseline_match,artifact_match):
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',regression)
    wifi=state.get('automatic_wifi',{});gpu=state.get('automatic_gpu',{})
    desktop=state.get('automatic_desktop',{});platform=state.get('passive_platform',{})
    battery=False
    for supply in platform.get('power_supplies',[]):
        try:battery|=supply.get('type')=='Battery' and 0<=int(supply.get('capacity'))<=100
        except (ValueError,TypeError):pass
    gates={
      'exact_artifacts_match_executed_plan':artifact_match,
      'all_stages_completed_without_failure':state.get('stages_completed') is True and not state.get('failure') and not state.get('halt_further_runs'),
      'automatic_android_return_and_seven_baselines':state.get('android_return_confirmed') is True and state.get('baseline_keys_match') is True and baseline_match,
      'zero_partition_writes':state.get('partition_writes')==0,
      'display_activated':state.get('display_activation') is True,
      'hardware_gpu_readback_and_project_mesa':all(gpu.get(k) is True for k in ['readback_matches','hardware_renderer','project_mesa_loaded']) and gpu.get('gl_error')==0,
      'ordinary_user_desktop_settings_and_wifi_combination':desktop.get('software_acceptance_pass') is True and desktop.get('combined_pass') is True,
      'wifi_scan_auth_dhcp_https':all(wifi.get(k) is True for k in ['scan_completed','target_seen','association_pass','dhcp_pass','https_pass','automatic_acceptance_pass','ipv4_global_address_present','ipv4_default_route_present']),
      'https_certificate_and_http_verified':wifi.get('https_metrics',{}).get('ssl_verify_result')==0 and wifi.get('https_metrics',{}).get('http_code')==200,
      'bluetooth_controller_and_sender_obex_present':'BT_STACK_COMPONENTS_PRESERVED' in text and bool(re.search(r'Powered:\s+yes\b',text)),
      'touch_device_observed':bool(re.search(r'\b(?:s3910|synaptics)\b',text,re.I)),
      'battery_telemetry_present':battery,
      'all_observed_storage_read_only':platform.get('all_observed_block_devices_read_only') is True and not platform.get('writable_block_devices_observed'),
    }
    return {'scope':'EXACT_CANDIDATE_AUTOMATIC_FEATURE_PRESERVATION_GATE',
            'delivery_eligible':all(gates.values()),'gates':gates,'failed_gates':[k for k,v in gates.items() if not v],
            'physical_touch_visual_or_peer_receipt_retested':False,'rf_policy_qualified_by_this_check':False,
            'phone_operations':0,'hardware_authorization_granted':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    for name in ['image','rootfs','payload']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    read=lambda n:json.loads((a.evidence/n).read_text(encoding='utf-8-sig'))
    state=read('record.json')['state'];plan=read('reviewed-plan.private.json')
    before=read('android-baseline.private.json');after=read('android-return.private.json')
    match=before==after and len(before)==7
    hashes={}
    for name in ['image','rootfs','payload']:
        with getattr(a,name).open('rb') as stream:hashes[name]=hashlib.file_digest(stream,'sha256').hexdigest()
    expected_image=plan.get('image_sha256',plan.get('boot_image_sha256'))
    binding=hashes['image']==expected_image and hashes['rootfs']==plan['rootfs_sha256'] and hashes['payload']==plan['payload_sha256']
    binding&=read('BOOT-ATTEMPTED.json')['image_sha256']==hashes['image']
    proof=evaluate(state,(a.evidence/'regression.txt').read_text(encoding='utf-8',errors='replace'),match,binding)
    proof['artifact_sha256']=hashes;a.report.write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(proof,indent=2));raise SystemExit(0 if proof['delivery_eligible'] else 2)

if __name__=='__main__':main()
