#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the bounded video client and exercise its pixel rejection checks."""
import argparse, hashlib, json, platform, subprocess
from pathlib import Path
REPO=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--cross-compile',choices=('aarch64-linux-gnu-',))
    args=parser.parse_args()
    native=platform.machine() in ('aarch64','arm64')
    if not native and not args.cross_compile:raise ValueError('Native ARM64 or explicit cross compiler required')
    out=args.output.resolve()
    if out.exists():raise ValueError('Fresh video output required')
    out.mkdir(parents=True)
    source=REPO/'tools/oneplus13/video-draft/decode-one-frame.c'
    binary=out/'decode-one-frame'
    compiler=(args.cross_compile or '')+'gcc'
    subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror','-static',
                    '-ffile-prefix-map='+str(REPO)+'=.',str(source),'-o',str(binary)],check=True)
    raw=binary.read_bytes()
    if raw[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(raw[18:20],'little')!=183:
        raise ValueError('Video client must be AArch64 ELF')
    result=subprocess.run(([str(binary)] if native else ['qemu-aarch64',str(binary)])+['--selftest'],
                          capture_output=True,text=True,check=True,timeout=10)
    if result.stdout.strip()!='VIDEO_PIXEL_VALIDATOR_SELFTEST_PASS_NO_DEVICE_OPEN':raise ValueError('Missing pixel selftest')
    sanitized=out/'decode-one-frame-sanitized-host'
    subprocess.run(['gcc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror',
                    '-fsanitize=address,undefined',str(source),'-o',str(sanitized)],check=True)
    subprocess.run([str(sanitized),'--selftest'],check=True,timeout=10)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
    report={'scope':'VIDEO_CLIENT_BUILD_AND_PIXEL_VALIDATOR_NOT_HARDWARE',
            'integration_commit':commit,'native_arm64_selftest':native,
            'pixel_validator_negative_controls_passed':True,'asan_ubsan_pass':True,
            'phone_operations':0,'hardware_tested':False,
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'binary_sha256':hashlib.sha256(raw).hexdigest()}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
