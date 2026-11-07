#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Explicit CEC core and UBWC configuration data prerequisites for display.

The caller binds the exact kernel module and policy hashes before this load.
After registration, ordinary dependency requests become a no-op. Every
other peripheral denial remains byte-identical in the RAM overlay.
UBWC exports a static SoC data table and has no hardware initialization function.
No display module, adapter, message, unload or retry is requested here.
"""
import argparse,hashlib,json,os,re,subprocess
from pathlib import Path

def command(args):
    return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=8).strip()

def no_adapter(root):
    if any((root/'dev').glob('cec[0-9]*')):
        raise ValueError('Hardware CEC adapter unexpectedly exists')
    bus=root/'sys/bus/cec/devices'
    if bus.exists() and any(bus.iterdir()):
        raise ValueError('CEC bus unexpectedly contains an adapter')

POLICY='etc/modprobe.d/91-oneplus13-unreviewed-peripherals.conf'
MODULES={'cec':'kernel/drivers/media/cec/core/cec.ko',
         'ubwc_config':'kernel/drivers/soc/qcom/ubwc_config.ko'}

def validate_ram(root, run):
    if not (root/'run/oneplus13/ram-session').is_file():
        raise ValueError('RAM marker missing')
    if not any(len(parts:=line.split())>=3 and parts[1:3]==['/','overlay']
               for line in (root/'proc/mounts').read_text().splitlines()):
        raise ValueError('RAM overlay missing')
    blocks=[p for p in (root/'sys/class/block').iterdir() if not p.name.startswith(('ram','zram','loop'))]
    if not blocks or any((p/'ro').read_text().strip()!='1' for p in blocks):
        raise ValueError('Physical block protection missing')
    for unit in ['oneplus13-ram-guard.service','oneplus13-ram-deadline.timer']:
        if run(['/usr/bin/systemctl','is-active',unit])!='active':
            raise ValueError('RAM recovery service inactive')


def validate(root,release,digest,policy_sha,ubwc_digest,run=command,*,validate_boot=validate_ram):
    root=Path(root)
    if not re.fullmatch(r'[A-Za-z0-9._+-]{1,160}',release) or not all(re.fullmatch(r'[0-9a-f]{64}',value) for value in [digest,policy_sha,ubwc_digest]):
        raise ValueError('Invalid bound module identity')
    validate_boot(root, run)
    policy=root/POLICY
    if policy.is_symlink() or hashlib.sha256(policy.read_bytes()).hexdigest()!=policy_sha:
        raise ValueError('Exact original peripheral policy required')
    effective=[line.strip() for line in run(['/usr/sbin/modprobe','-c']).splitlines()]
    for name,digest in [('cec',digest),('ubwc_config',ubwc_digest)]:
        module=root/('usr/lib/modules/'+release+'/'+MODULES[name])
        if module.is_symlink() or module.stat().st_size>1024*1024:
            raise ValueError('Unexpected prerequisite module file')
        if hashlib.sha256(module.read_bytes()).hexdigest()!=digest:
            raise ValueError('Bound prerequisite module hash mismatch')
        if not run(['/usr/sbin/modinfo','-k',release,'-F','vermagic',name]).startswith(release+' '):
            raise ValueError('Prerequisite module release mismatch')
        if run(['/usr/sbin/modinfo','-k',release,'-F','depends',name]):
            raise ValueError('Prerequisite unexpectedly has dependencies')
        rule='install '+name+' /bin/false'
        rules=[line for line in effective if line.startswith('install '+name+' ')]
        if rules!=[rule] or policy.read_bytes().count((rule+'\n').encode())!=1:
            raise ValueError('Single exact prerequisite denial required')
        if (root/'sys/module'/name).exists():
            raise ValueError('Prerequisite already loaded before explicit attempt')
    no_adapter(root)

def prepare(root,release,digest,policy_sha,ubwc_digest,run=command,*,validate_boot=validate_ram):
    root=Path(root)
    if run is command and (root!=Path('/') or os.uname().release!=release):
        raise ValueError('Real loading requires current root and release')
    validate(root,release,digest,policy_sha,ubwc_digest,run,validate_boot=validate_boot)
    marker=root/'run/oneplus13/cec-core-load-attempted'
    # Retain this marker even when kmod fails. Never retry an attempted load.
    with marker.open('x'):pass
    for name in MODULES:
        run(['/usr/sbin/modprobe','--ignore-install',name])
        if not (root/'sys/module'/name).is_dir():
            raise ValueError('Display prerequisite did not register')
        no_adapter(root)
    # kmod executes install rules even for an already loaded dependency.
    # Permit only a no-op after the exact generic core is visibly registered.
    policy=root/POLICY;original=policy.read_bytes()
    updated=original
    for name in MODULES:
        updated=updated.replace(('install '+name+' /bin/false\n').encode(),('install '+name+' /bin/true\n').encode(),1)
    policy.write_bytes(updated)
    effective=[line.strip() for line in run(['/usr/sbin/modprobe','-c']).splitlines()]
    for name in MODULES:
        if [line for line in effective if line.startswith('install '+name+' ')]!=['install '+name+' /bin/true']:
            raise ValueError('Registered prerequisite no-op not effective')
    return {'scope':'EXPLICIT_DISPLAY_SHARED_CORES_ONLY','cec_module_sha256':digest,
        'ubwc_config_module_sha256':ubwc_digest,'ubwc_configuration_data_registered':True,
        'ordinary_ubwc_requests_noop_after_registration':True,
        'ordinary_cec_requests_noop_after_registration':True,'other_peripheral_denials_unchanged':True,
        'original_policy_sha256':policy_sha,'runtime_policy_sha256':hashlib.sha256(updated).hexdigest(),'hardware_cec_adapters':0,
        'display_loaded':False,'cec_message_or_adapter_control_requested':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--release',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--policy-sha256',required=True);p.add_argument('--ubwc-sha256',required=True);a=p.parse_args()
    try:
        if os.geteuid()!=0:raise ValueError('Root required')
        result=prepare(Path('/'),a.release,a.sha256,a.policy_sha256,a.ubwc_sha256)
        print('RAM_DISPLAY_PREREQUISITE_RESULT '+json.dumps(result,sort_keys=True))
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        print('RAM_DISPLAY_PREREQUISITE_FAILED '+type(error).__name__)
        return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
