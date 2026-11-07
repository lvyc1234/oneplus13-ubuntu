#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run an explicitly selected diagnostic using only source-bound image inputs."""
import argparse,hashlib,json,os,re,subprocess,sys
from pathlib import Path
BINDINGS=Path('/usr/lib/oneplus13-sensors/runtime-bindings.json')
PATHS={
 'attach-helper':'/usr/libexec/oneplus13-ram-sensor-attach-only.py',
 'fileserver-helper':'/usr/libexec/oneplus13-ram-sensor-fileserver.py',
 'ssc-helper':'/usr/libexec/oneplus13-ram-ssc-metadata.py',
 'ssc-samples-helper':'/usr/libexec/oneplus13-ram-ssc-samples.py',
 'proxy-helper':'/usr/libexec/oneplus13-ram-ssc-proxy.py',
 'discovery-helper':'/usr/libexec/oneplus13-ram-sensor-discovery.py',
 'archive':'/usr/lib/oneplus13-sensors/phone-sensor-inputs.tar.gz',
 'manifest':'/usr/lib/oneplus13-sensors/phone-sensor-inputs-manifest.json',
 'ssc-archive':'/usr/lib/oneplus13-ssc/phone-ssc-probe-arm64.tar.gz',
 'ssc-manifest':'/usr/lib/oneplus13-ssc/phone-ssc-probe-manifest.json',
 'ssc-samples-archive':'/usr/lib/oneplus13-ssc/phone-ssc-samples-arm64.tar.gz',
 'ssc-samples-manifest':'/usr/lib/oneplus13-ssc/phone-ssc-samples-manifest.json',
 'trace-tool':'/usr/bin/strace'}
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def arguments(doc,mode,check=sha,release=None):
    if mode not in ('metadata','samples','proxy') or doc.get('format')!='ONEPLUS13_IMAGE_SENSOR_INPUTS_V1':raise ValueError('Unreviewed image sensor mode/bindings')
    if set(doc.get('inputs',{}))!=set(PATHS):raise ValueError('Image sensor inventory changed')
    if doc.get('kernel_release')!=(release or os.uname().release):raise ValueError('Image sensor kernel mismatch')
    args=['--release',doc['kernel_release'],'--probe-mode',mode]
    for key,value in PATHS.items():
        item=doc['inputs'][key]
        if item.get('path')!=value or not re.fullmatch('[0-9a-f]{64}',item.get('sha256','')) or check(value)!=item['sha256']:
            raise ValueError('Delivered image sensor input changed: '+key)
        if key=='discovery-helper':continue
        if key=='proxy-helper' and mode!='proxy':continue
        digest_key='trace' if key=='trace-tool' else key
        args+=['--'+key,value,'--'+digest_key+'-sha256',item['sha256']]
    for key,path in [('module','/usr/lib/modules/'+doc['kernel_release']+'/kernel/drivers/misc/fastrpc.ko'),
                     ('policy','/etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf')]:
        digest=doc.get(key+'_sha256','')
        if not re.fullmatch('[0-9a-f]{64}',digest) or check(path)!=digest:raise ValueError('Image module/policy changed')
        args+=['--'+key+'-sha256',digest]
    return [sys.executable,PATHS['discovery-helper'],*args]
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['metadata','samples','proxy'],default='samples')
    a=p.parse_args()
    if os.geteuid()!=0 or not Path('/run/oneplus13/ram-session').is_file():raise ValueError('Protected root RAM session required')
    command=arguments(json.loads(BINDINGS.read_text()),a.mode)
    print('IMAGE_SENSOR_INPUTS_VERIFIED no_private_host_overlay=yes mode='+a.mode,flush=True)
    return subprocess.run(command,timeout=135).returncode
if __name__=='__main__':raise SystemExit(main())
