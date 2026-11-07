#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the actual scan-start header block; no firmware scheduler emulation."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import re
import signal
import subprocess
import tempfile


def header(source):
    spec=importlib.util.spec_from_file_location('scan_helpers',Path(__file__).with_name('check-wcn7860-scan.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    function=module.function(source,'ath12k_wmi_send_scan_start_cmd')
    start=function.index('\tcmd->scan_id = ')
    end=function.index('\n\tath12k_wmi_copy_scan_event_cntrl_flags',start)
    return function[start:end]


PREFIX=r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
@ACTUAL_ENUMERATIONS@
struct ath12k { int state_11d; };
struct argument { uint32_t scan_id,scan_req_id,vdev_id,scan_priority,notify_scan_events; };
struct wire { uint32_t scan_id,scan_req_id,vdev_id,scan_priority,notify_scan_events; };
static uint32_t cpu_to_le32(uint32_t value) {
#if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
 return __builtin_bswap32(value);
#else
 return value;
#endif
}
static void serialize(struct ath12k *ar,struct argument *arg,struct wire *cmd) {
'''
SUFFIX=r'''
}
int main(void) {
 for(int state=0;state<5;state++) {
  struct ath12k ar={.state_11d=state};
  struct argument arg={.scan_id=0xaecdfbfa,.scan_req_id=0xbbcaecef,
                       .vdev_id=0xcadbcdab,.scan_priority=77,.notify_scan_events=0xdfabceaf};
  struct wire cmd;memset(&cmd,0,sizeof(cmd));
  serialize(&ar,&arg,&cmd);
  uint32_t expected=state==ATH12K_11D_PREPARING?WMI_SCAN_PRIORITY_MEDIUM:WMI_SCAN_PRIORITY_LOW;
  assert(arg.scan_priority==expected);
  const unsigned char *bytes=(const unsigned char *)&cmd.scan_priority;
  assert(bytes[0]==expected && bytes[1]==0 && bytes[2]==0 && bytes[3]==0);
  assert(cmd.scan_id==cpu_to_le32(arg.scan_id));
  assert(cmd.scan_req_id==cpu_to_le32(arg.scan_req_id));
  assert(cmd.vdev_id==cpu_to_le32(arg.vdev_id));
  assert(cmd.notify_scan_events==cpu_to_le32(arg.notify_scan_events));
 }
 return 0;
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    source=(args.kernel_source/'drivers/net/wireless/ath/ath12k/wmi.c').read_text()
    enums=[]
    for filename,name in [('wmi.h','wmi_scan_priority'),('core.h','ath12k_11d_state')]:
        text=(args.kernel_source/'drivers/net/wireless/ath/ath12k'/filename).read_text()
        match=re.search(r'enum '+name+r'\s*\{[^}]+\};',text)
        if not match:raise ValueError('Missing actual enum: '+name)
        enums.append(match[0])
    prefix=PREFIX.replace('@ACTUAL_ENUMERATIONS@','\n'.join(enums))
    block=header(source)
    assignment='\tcmd->scan_priority = cpu_to_le32(arg->scan_priority);'
    if block.count(assignment)!=1:raise ValueError('Missing unique scan-start priority serialization')
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    with tempfile.TemporaryDirectory(prefix='phone-wmi-priority-') as temp:
        root=Path(temp)
        for kind,flags in [('release',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
            code=root/(kind+'.c');binary=root/kind
            code.write_text(prefix+block+SUFFIX)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(code),'-o',str(binary)],check=True,capture_output=True,timeout=30)
            result=subprocess.run([str(binary)],capture_output=True,timeout=10)
            if result.returncode:raise ValueError('Actual WMI header serialization failed: '+kind)
        code=root/'negative.c';binary=root/'negative'
        code.write_text(prefix+block.replace(assignment,'')+SUFFIX)
        subprocess.run(['cc','-std=c11','-O2',str(code),'-o',str(binary)],check=True,capture_output=True,timeout=30)
        negative=subprocess.run([str(binary)],capture_output=True,timeout=10)
        if negative.returncode!=-signal.SIGABRT:raise ValueError('The missing-assignment control did not fail')
    args.output.write_text(json.dumps({'scope':'ACTUAL_WMI_SCAN_START_HEADER_BLOCK',
      'pass':True,'release_and_asan_ubsan':True,'missing_assignment_negative_control_rejected':True,
      'function':'ath12k_wmi_send_scan_start_cmd','whole_function_compiled':False,
      'actual_block_sha256':hashlib.sha256(block.encode()).hexdigest(),
      'actual_priority_and_11d_enum_definitions_compiled':True,
      'priority_policy_changed':False,'hardware_acceptance':False,'firmware_scheduler_emulated':False},indent=2)+'\n')
    print('ACTUAL_WMI_SCAN_PRIORITY_BLOCK_PASS_AND_NEGATIVE_CONTROL_REJECTED')


if __name__=='__main__':main()
