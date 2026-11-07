#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual SPR decoder/planner C; no device, MMIO or DMA is accessed."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from spr import encode_profile, parse_xml, register_plan


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--sanitize', action='store_true')
    p.add_argument('--private-xml', type=Path, help='Local-only comparison, never uploaded or printed')
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    component = repo/'devices/oneplus13/kernel/spr-aa569'
    synthetic = (repo/'devices/oneplus13/test-fixtures/spr-synthetic.xml').read_bytes()
    blobs = [synthetic]
    for inc in (0, 15):
        for repeat in (0, 7):
            for pba in ('0,0,0,0,0,0,0', '7,7,7,2,1,0,2'):
                tree = ET.fromstring(synthetic)
                mode = tree.find('SprConfig/Mode')
                mode.find('PhaseInc').text = str(inc)
                mode.find('PhaseRepeat').text = str(repeat)
                mode.find('Pba').text = pba
                mode.find('FilterCoeffs').text = ','.join(['-1024', '1023']*8)
                mode.find('ColorPhaseOffsetsRed').text = '-16,15,-16,15,-16,15'
                blobs.append(ET.tostring(tree))
    if a.private_xml:
        blobs.append(a.private_xml.read_bytes())
    headers = {
        'types.h': '#include <stdint.h>\n#include <stddef.h>\ntypedef uint8_t u8; typedef uint32_t u32;\n',
        'bits.h': '#include <stdint.h>\n#define BIT(n) (UINT32_C(1)<<(n))\n'
                  '#define GENMASK(h,l) ((UINT32_MAX>>(31-(h)))&(UINT32_MAX<<(l)))\n',
        'string.h': '#include <string.h>\n',
        # glibc errno.h includes linux/errno.h; recursively including it from
        # this shim leaves EINVAL undefined. The shim needs only Linux values.
        'errno.h': '#define EINVAL 22\n#define EBADMSG 74\n',
        'unaligned.h': '#include <stdint.h>\nstatic inline uint32_t get_unaligned_le32(const void *p) {'
                       'const uint8_t *b=p;return (uint32_t)b[0]|((uint32_t)b[1]<<8)|'
                       '((uint32_t)b[2]<<16)|((uint32_t)b[3]<<24);}\n',
        'crc32.h': '#include <stdint.h>\n#include <stddef.h>\n'
                   'static inline uint32_t crc32_le(uint32_t crc,const uint8_t *p,size_t len) {'
                   'while(len--){crc^=*p++;for(int i=0;i<8;i++)'
                   'crc=(crc>>1)^((crc&1)?0xedb88320U:0);}return crc;}\n',
    }
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <linux/crc32.h>
#include "dpu_aa569_spr.h"
#define CHECK(x) do {if(!(x)){fprintf(stderr,"SPR check failed at line %d\n",__LINE__);return 1;}}while(0)
static void put32(u8 *p,u32 v) {for(int i=0;i<4;i++)p[i]=(u8)(v>>(8*i));}
static void fixcrc(u8 *b) {put32(b+28,crc32_le(~0U,b+40,AA569_SPR_PROFILE_SIZE-40)^~0U);}
static int rejected(const u8 *b,size_t len) {
 struct aa569_spr_profile out,original;
 memset(&out,0xa5,sizeof(out));original=out;
 return aa569_spr_decode(b,len,&out)<0&&!memcmp(&out,&original,sizeof(out));
}
int main(int argc,char **argv) {
 CHECK(argc==2||argc==3);
 u8 b[AA569_SPR_PROFILE_SIZE+4],bad[sizeof(b)];
 FILE *file=fopen(argv[1],"rb");CHECK(file);
 size_t len=fread(b,1,sizeof(b),file);CHECK(!ferror(file));fclose(file);
 CHECK(len==AA569_SPR_PROFILE_SIZE);
 struct aa569_spr_profile p;
 struct aa569_spr_plan plan;
 CHECK(!aa569_spr_decode(b,len,&p));CHECK(!aa569_spr_plan(&p,&plan));
 if(argc==2) {
  for(u32 i=0;i<plan.count;i++)printf("%u %x %x %x\n",plan.writes[i].path,
   plan.writes[i].offset,plan.writes[i].value,plan.writes[i].preserve_mask);
  return 0;
 }
 CHECK((crc32_le(~0U,(const u8 *)"123456789",9)^~0U)==0xcbf43926U);
 for(size_t i=0;i<len;i++)CHECK(rejected(b,i));
 CHECK(rejected(b,len+1));
 for(size_t i=0;i<len;i++){memcpy(bad,b,len);bad[i]^=0x80;CHECK(rejected(bad,len));}
 for(int i=0;i<AA569_SPR_WORDS;i++) {
  memcpy(bad,b,len);bad[40+i*4+3]|=0x80;fixcrc(bad);CHECK(rejected(bad,len));
 }
 /* A correct CRC does not make reserved, UDC, route or PBA values valid. */
 const int indices[]={31,32,33,30,31};
 const u32 values[]={p.words[31]|(1U<<25),0,1,(p.words[30]&~3U)|3U,p.words[31]&~(1U<<26)};
 for(size_t i=0;i<sizeof(indices)/sizeof(indices[0]);i++) {
  memcpy(bad,b,len);put32(bad+40+indices[i]*4,values[i]);fixcrc(bad);CHECK(rejected(bad,len));
 }
 for(int i=0;i<4;i++) {
  memcpy(bad+i,b,len);CHECK(!aa569_spr_decode(bad+i,len,&p));
 }
 CHECK(aa569_spr_decode(NULL,len,&p)<0&&aa569_spr_decode(b,len,NULL)<0);
 struct aa569_spr_plan original;
 memset(&plan,0xa5,sizeof(plan));original=plan;
 p.words[3]|=1U<<10;
 CHECK(aa569_spr_plan(&p,&plan)<0&&!memcmp(&plan,&original,sizeof(plan)));
 CHECK(aa569_spr_plan(NULL,&plan)<0&&aa569_spr_plan(&p,NULL)<0);
 puts("SPR malformed input, unaligned access and unchanged failure outputs passed");
 return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='oneplus13-spr-') as temp:
        temp = Path(temp)
        (temp/'linux').mkdir()
        for name, content in headers.items():
            (temp/'linux'/name).write_text(content)
        (temp/'harness.c').write_text(harness)
        exe = temp/'check-spr.exe'
        command = ['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror']
        if a.sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command+['-I'+str(temp), '-I'+str(component),
                       str(component/'dpu_aa569_spr.c'), str(temp/'harness.c'), '-o', str(exe)], check=True)
        for i, blob in enumerate(blobs):
            profile = temp/'profile.bin'
            profile.write_bytes(encode_profile(blob))
            if i == 0:
                subprocess.run([str(exe), str(profile), 'self-test'], check=True)
            default, modes = parse_xml(blob)
            expected = []
            paths = {'regdma': 1, 'direct-before-regdma-kickoff': 0, 'separate-pu-regdma': 2}
            for op in register_plan(modes[default]):
                for j, word in enumerate(op['values']):
                    expected.append((paths[op['transport']], op['relative_offset']+4*j,
                                     word, op['preserve_mask']))
            output = subprocess.check_output([str(exe), str(profile)], text=True)
            actual = [tuple(int(v, 16 if k else 10) for k, v in enumerate(line.split()))
                      for line in output.splitlines()]
            if actual != expected:
                raise ValueError('C transaction plan differs from independently checked Python packing')
            if any(off >= 0x200 or off == 0x54 or off & 3 for _, off, _, _ in actual):
                raise ValueError('Unexpected SPR register offset')
    report = {'scope': 'HOST_C_SPR_PROFILE_AND_TRANSACTION_PLAN_ONLY',
              'hardware_tested': False, 'sanitizers': a.sanitize,
              'synthetic_profiles': 9, 'private_profile_checked': bool(a.private_xml),
              'profile_size': 176, 'truncated_lengths_rejected': 176,
              'single_byte_corruptions_rejected': 176, 'reserved_word_cases_rejected': 34,
              'semantic_invalid_cases_rejected': 5, 'unaligned_inputs_checked': 4,
              'oversized_input_rejected': True, 'failure_outputs_unchanged': True,
              'udc_bit25_preserved_in_plan': True, 'typed_direct_init_pu_paths': True,
              'legacy_cfg14_write_absent': True, 'runtime_exercised_by_this_check': False,
              'mmio_or_dma_submitted': False,
              'core_sha256': hashlib.sha256((component/'dpu_aa569_spr.c').read_bytes()).hexdigest()}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2)+'\n')
    print('SPR C decoder/planner and synthetic profile comparisons passed; no hardware accessed.')


if __name__ == '__main__':
    main()
