#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""QEMU dummy-virt only: pure SSC guard tests and actual service timeout."""
import argparse,importlib.util,json,subprocess,time
from pathlib import Path

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--archive-sha256',required=True);p.add_argument('--manifest-sha256',required=True)
    a=p.parse_args()
    if b'linux,dummy-virt' not in Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0'):
        raise ValueError('QEMU-only fixture')
    here=Path('/run/oneplus13');helper=here/'ram-ssc-metadata.py'
    result=subprocess.run(['python3',str(helper),'--mode','verify','--archive',str(here/'ssc-probe.tar.gz'),
                          '--manifest',str(here/'ssc-probe.json'),'--archive-sha256',a.archive_sha256,
                          '--manifest-sha256',a.manifest_sha256],capture_output=True,text=True,timeout=40)
    if result.returncode:raise ValueError('Actual sandbox guard tests failed: '+(result.stdout+result.stderr)[-1800:])
    rows=[line.split(' ',1)[1] for line in result.stdout.splitlines() if line.startswith('RAM_SSC_METADATA_RESULT ')]
    if len(rows)!=1:raise ValueError('Missing real sandbox result')
    facts=json.loads(rows[0])
    assert facts['native_guard_checks_passed']==3 and facts['unit_inactive'] and facts['uid']==65534
    assert not facts['qrtr_query_requested'] and not facts['measurement_commands_allowed']
    spec=importlib.util.spec_from_file_location('ssc_timeout_fixture',helper)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    original=m.command
    def sleeping(mode,state=m.STATE):
        unit,command=original(mode,state)
        # Keep the real sandbox and cleanup. Shorten ONLY this dummy-virt
        # synthetic timer; no phone runtime/deadline is changed.
        command=[x.replace('RuntimeMaxSec=20s','RuntimeMaxSec=2s') for x in command]
        boundary=command.index('/bin/sh')
        return unit,command[:boundary]+['/bin/sleep','30']
    m.command=sleeping;started=time.monotonic();refused=False
    try:m.run_probe('verify')
    except ValueError:refused=True
    elapsed=time.monotonic()-started
    assert refused and elapsed<15,'Timer/cleanup did not refuse the sleeping fixture'
    report={'scope':'QEMU_PURE_SSC_GUARD_AND_TIMEOUT_ONLY','native_checks':3,'uid':65534,
            'real_unit_timeout_refused':True,'timeout_elapsed_seconds':round(elapsed,3),
            'qrtr_queries_executed':False,'measurement_commands_executed':False,
            'phone_recovery_deadline_changed':False}
    print('QEMU_SSC_METADATA_SELFTEST_RESULT '+json.dumps(report,sort_keys=True),flush=True)

if __name__=='__main__':main()
