#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read bounded kernel history and stop on DMA faults or WLAN recovery loops."""
import json
import re
import subprocess

PATTERNS={
    'kernel_bug_or_panic':r'BUG:|Kernel panic',
    'iommu_context_fault':r'Unhandled context fault|[Ii][Oo][Mm][Mm][Uu].*(?:translation fault|page fault)',
    'cpu_stall':r'rcu[^\n]*stall|blocked for more than',
    'gpu_fault':r'GPU fault|gpu[^\n]*hang',
    'firmware_crash':r'firmware[^\n]*crash|firmware crashed',
    'wifi_hardware_restart':r'Hardware restart was requested',
    'wifi_invalid_runtime_id':r'invalid pdev_id[^\n]*mgmt_rx_event|invalid vdev id',
    'wifi_vdev_failure':r'failed to (?:start vdev|delete WMI vdev|submit WMI_VDEV_DELETE)',
    'wifi_driver_failure':r'ath12k[^\n]*(?:failed|timed out|timeout|invalid|crash|error)',
    'out_of_memory':r'Out of memory:|oom-kill:|Killed process [0-9]+',
}

def classify(text):
    counts={key:len(re.findall(pattern,text,re.I)) for key,pattern in PATTERNS.items()}
    return {'scope':'BOUNDED_KERNEL_HISTORY_HEALTH','counts':counts,
            'pass':not any(counts.values()),'raw_kernel_text_published':False}

if __name__=='__main__':
    try:
        result=subprocess.run(['dmesg'],capture_output=True,timeout=4)
        if result.returncode or len(result.stdout)>4*1024*1024:raise ValueError('Unavailable or oversized kernel history')
        facts=classify(result.stdout.decode(errors='replace'))
    except (OSError,ValueError,subprocess.TimeoutExpired):
        facts={'scope':'BOUNDED_KERNEL_HISTORY_HEALTH','pass':False,'availability':'unavailable'}
    print('RAM_KERNEL_HEALTH_RESULT '+json.dumps(facts,sort_keys=True))
    raise SystemExit(0 if facts['pass'] else 1)
