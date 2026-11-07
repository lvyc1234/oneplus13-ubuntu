#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile synthetic parser controls and actual power functions, without devices."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    here=Path(__file__).resolve().parent
    source=(here/'aw88461.c').read_text()
    defines='\n'.join(line for line in source.splitlines() if line.startswith('#define AW88461_'))
    body=source[source.index('static void aw88461_reset('):source.index('static int aw88461_load_profile(')]
    generated=(here/'power-test-prefix.c').read_text()+'\n'+defines+'\n'+body+'\n'+(here/'power-test-main.c').read_text()
    (out/'power-test.c').write_text(generated)
    results=[]
    for name,path in [('profile-test',here/'profile-test.c'),('power-test',out/'power-test.c')]:
        exe=out/name
        subprocess.run(['gcc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-O1',
                        '-fsanitize=address,undefined','-fno-omit-frame-pointer','-I',str(here),
                        str(path),'-o',str(exe)],check=True)
        run=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
        print(run.stdout,end='')
        results.append({'name':name,'returncode':run.returncode,'output':run.stdout.strip()})
    files=['aw88461.c','aw88461-profile.h','power-test-prefix.c','power-test-main.c','profile-test.c','check.py']
    report={'scope':'OFFLINE_SYNTHETIC_ONLY','phone_operations':0,'audio_supported':False,
            'stream_eligible':False,'generated_from_actual_driver':True,'tests':results,
            'source_sha256':{n:hashlib.sha256((here/n).read_bytes()).hexdigest() for n in files},
            'generated_power_sha256':hashlib.sha256(generated.encode()).hexdigest()}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
