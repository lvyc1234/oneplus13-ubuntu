#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the production SPR machine guard against OF fixtures, without MMIO."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc',nargs='+',default=['gcc'])
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--sanitize',action='store_true')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    runtime = (repo/'devices/oneplus13/kernel/spr-aa569/dpu_aa569_spr_runtime.c').read_text()
    spec = importlib.util.spec_from_file_location('display_check',Path(__file__).with_name('check-display.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    function = module.extract_function(runtime,'static bool aa569_spr_machine(')
    cases = json.loads((repo/'devices/oneplus13/test-fixtures/native-identity.json').read_text())
    source = r'''
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
struct device_node {int unused;};
static struct device_node root;
static const char *model,*chassis,*compat[8];
static int node_gets,puts_count;
static struct device_node *of_find_node_by_path(const char *path) {if(strcmp(path,"/"))return NULL;node_gets++;return &root;}
static int of_property_read_string(struct device_node *node,const char *key,const char **out) {
 if(node!=&root)return -1;
 *out=!strcmp(key,"model")?model:!strcmp(key,"chassis-type")?chassis:NULL;return *out?0:-1;
}
static bool of_device_is_compatible(struct device_node *node,const char *value) {
 if(node!=&root)
  return false;
 for(int i=0;compat[i];i++)
  if(!strcmp(compat[i],value))
   return true;
 return false;
}
static void of_node_put(struct device_node *node) {if(node==&root)puts_count++;}
'''+function+'\nint main(void) {\n'
    literal = lambda value: 'NULL' if value is None else json.dumps(value)
    for index,case in enumerate(cases):
        source += 'model='+literal(case['model'])+';chassis='+literal(case['chassis'])+';\n'
        source += 'memset(compat,0,sizeof(compat));'
        for i,compatible in enumerate(case['compatible']):
            source += 'compat[%d]=%s;'%(i,literal(compatible))
        source += '\nif(aa569_spr_machine()!=%s||node_gets!=%d||puts_count!=%d)return %d;\n'%(
            str(case['accept']).lower(),index+1,index+1,index+1)
    source += 'puts("Native identity C fixtures passed; OF references balanced");return 0;}\n'
    with tempfile.TemporaryDirectory() as temp:
        c,exe = Path(temp)/'identity.c',Path(temp)/'identity.exe'
        c.write_text(source)
        command = args.cc+['-std=gnu11','-Wall','-Wextra','-Werror']
        if args.sanitize:command += ['-fsanitize=address,undefined','-fno-omit-frame-pointer']
        subprocess.run(command+[str(c),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'scope':'HOST_C_OF_IDENTITY_ONLY','hardware_tested':False,
        'cases':len(cases),'of_reference_balance_checked':True,'sanitizers':args.sanitize,
        'guard_sha256':hashlib.sha256(function.encode()).hexdigest()},indent=2)+'\n')

if __name__=='__main__':main()
