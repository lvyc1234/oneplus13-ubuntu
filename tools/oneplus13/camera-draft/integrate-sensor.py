#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Stage the reviewed S5KJN5 driver only; no phone node or power activation."""
import argparse,hashlib,json,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def integrate(kernel):
    lock=json.loads((HERE/'sources.json').read_text())
    pin=subprocess.check_output(['git','-C',str(kernel),'rev-parse','HEAD'],text=True,timeout=8).strip()
    if pin!=lock['kernel_pin']:raise ValueError('Wrong kernel pin for S5KJN5 integration')
    patches=lock['sensor_patches']+lock['sensor_compat_fixes']
    if len(patches)!=3:raise ValueError('Unexpected sensor patch chain')
    for item in patches:
        path=Path(item['file'])
        if path.is_absolute() or '..' in path.parts or sha(HERE/path)!=item['sha256']:
            raise ValueError('Sensor patch input changed')
    generated=lock['sensor_generated_files']
    if set(generated)!={'drivers/media/i2c/s5kjn5.c',
                       'Documentation/devicetree/bindings/media/i2c/samsung,s5kjn5.yaml'}:
        raise ValueError('Unexpected generated sensor file set')
    existing=[name for name in generated if (kernel/name).exists()]
    if existing:
        if set(existing)!=set(generated) or any(sha(kernel/name)!=digest for name,digest in generated.items()):
            raise ValueError('Refuse unknown or partial existing sensor implementation')
    else:
        for item in patches:
            subprocess.run(['patch','-d',str(kernel),'-p1','--batch','--forward','--fuzz=0',
                            '-i',str(HERE/item['file'])],check=True,timeout=15)
    if any(sha(kernel/name)!=digest for name,digest in generated.items()):
        raise ValueError('Generated sensor source mismatch')
    config=(kernel/'drivers/media/i2c/Kconfig').read_text()
    make=(kernel/'drivers/media/i2c/Makefile').read_text()
    if config.count('config VIDEO_S5KJN5\n')!=1 or make.count('obj-$(CONFIG_VIDEO_S5KJN5) += s5kjn5.o')!=1:
        raise ValueError('Sensor build entries are missing or duplicated')
    subprocess.run(['git','-C',str(kernel),'add','--intent-to-add',*generated],check=True,timeout=8)
    return {'scope':'SOURCE_ONLY_MANUAL_S5KJN5_DRIVER_INTEGRATION','kernel_pin':pin,
            'patches':patches,'generated_source_files':generated,'phone_dts_changed':False,
            'sensor_powered':False,'camera_capture_qualified':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel',required=True,type=Path)
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args();report=integrate(args.kernel.resolve())
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print('S5KJN5_SOURCE_ONLY_INTEGRATION_PASS')


if __name__=='__main__':main()
