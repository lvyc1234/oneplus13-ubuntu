#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the real dormant SAR integration; never build a phone boot image."""
import argparse,hashlib,importlib.util,json,os,platform,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    if platform.machine() not in ('aarch64','arm64'):raise ValueError('Native ARM64 only')
    out=a.output.resolve()
    if out.exists():raise ValueError('Preserve previous build evidence')
    out.mkdir(parents=True);log=out/'compile.log'
    env={k:v for k,v in os.environ.items() if k in ['PATH','HOME','TMPDIR']};env.update(LANG='C.UTF-8',LC_ALL='C.UTF-8')
    def run(argv,timeout=180):
        argv=[str(x) for x in argv];r=subprocess.run(argv,capture_output=True,env=env,timeout=timeout)
        with log.open('ab') as f:f.write(json.dumps(argv).encode()+b'\n'+r.stdout+r.stderr+b'\n')
        if r.returncode:raise RuntimeError('Native WiFi integration compile failed; see compile.log')
        return r.stdout.decode()
    lock=json.loads((HERE/'driver-sources.json').read_text());kernel=out/'linux'
    run(['git','init',kernel]);run(['git','-C',kernel,'remote','add','origin','https://github.com/'+lock['kernel_repository']+'.git'])
    run(['git','-C',kernel,'fetch','--depth','1','origin',lock['kernel_pin']],240)
    run(['git','-C',kernel,'checkout','--detach','FETCH_HEAD'])
    if run(['git','-C',kernel,'rev-parse','HEAD']).strip()!=lock['kernel_pin']:raise ValueError('Wrong kernel')
    spec=importlib.util.spec_from_file_location('integration',HERE/'integrate-driver.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    binding=module.integrate(kernel)
    (out/'integration.json').write_text(json.dumps(binding,indent=2)+'\n')
    run(['make','-C',kernel,'ARCH=arm64','defconfig'])
    for name in ['WLAN','WLAN_VENDOR_ATH','PCI']:
        run([kernel/'scripts/config','--file',kernel/'.config','--enable',name])
    for name in ['CFG80211','MAC80211','ATH12K']:
        run([kernel/'scripts/config','--file',kernel/'.config','--module',name])
    run(['make','-C',kernel,'ARCH=arm64','olddefconfig'])
    config=(kernel/'.config').read_text()
    if 'CONFIG_ATH12K=m\n' not in config:raise ValueError('ath12k disabled')
    base='drivers/net/wireless/ath/ath12k/'
    targets=[base+n for n in ['phone-sar.o','sar-wire.o','wmi.o','core.o']]
    # wmi_base layout changed: rebuild the complete common + WiFi7 family,
    # including its relocatable composite links, not just four leaf objects.
    run(['make','-C',kernel,'ARCH=arm64','-j'+str(min(os.cpu_count() or 2,8)),'W=1','KCFLAGS=-Werror',base],600)
    targets += [base+'ath12k.o',base+'wifi7/ath12k_wifi7.o']
    objects={}
    for name in targets:
        data=(kernel/name).read_bytes()
        if data[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(data[18:20],'little')!=183:raise ValueError('Not AArch64 object')
        objects[name]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    (out/'kernel.config').write_text(config)
    proof={'scope':'DORMANT_PHONE_SAR_KERNEL_OBJECTS_ONLY','phone_operations':0,'integration':binding,
           'aarch64_objects':objects,'warnings_as_errors':True,'complete_common_and_wifi7_recompiled':True,
           'relocatable_composite_links_tested':True,'module_modpost_or_runtime_tested':False,
           'boot_image_created':False,'candidate_modified':False,'private_inputs_included':False,
           'effective_phone_power_policy':False,'current_wifi_qualified':False}
    (out/'verification.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))

if __name__=='__main__':main()
