#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Review public phone C-PHY tables as data; never register a driver or use MMIO."""
from pathlib import Path
import argparse,decimal,hashlib,json,platform,re,subprocess

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cross-compile',choices=('aarch64-linux-gnu-',),default='')
    parser.add_argument('--emit-only',action='store_true',
                        help='Generate source data only; no compiler or test execution')
    parser.add_argument('--compile-only',action='store_true',
                        help='Compile generated phone tables without synthetic checks')
    args=parser.parse_args()
    if args.emit_only and args.compile_only:
        raise ValueError('--emit-only and --compile-only are mutually exclusive')
    if not args.emit_only and not args.cross_compile and platform.machine() not in ('aarch64','arm64'):
        raise ValueError('Native ARM64 or explicit AArch64 cross compiler required')
    source=args.source
    if source.is_symlink() or not source.is_file() or source.stat().st_size>1048576:
        raise ValueError('Bounded regular public source required')
    raw=source.read_bytes();assert hashlib.sha256(raw).hexdigest()=='350e2c9188c5db163cdc7fbb7a893cbd4089175a1adfa8aa6f5881c7c454c501'
    s=raw.decode();arrays={m[1]:m[2] for m in re.finditer(r'struct csiphy_reg_t\s+(\w+)\[\]\s*=\s*\{(.*?)\n\};',s,re.S)}
    def rows(name):
        text=re.sub(r'/\*.*?\*/','',arrays[name],flags=re.S)
        pattern=r'\{\s*(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),\s*(CSIPHY_[A-Z0-9_]+)\s*\}\s*,?'
        matches=list(re.finditer(pattern,text));left=re.sub(pattern,'',text)
        assert not left.strip(),name
        result=[(int(m[1],16),int(m[2],16),int(m[3],16),m[4]) for m in matches]
        assert all(a%4==0 and a<=0x1184 and v<=0xff and d<=10000 for a,v,d,t in result)
        return result
    table=re.search(r'static struct data_rate_reg_info_t data_rate_settings_2_3_0_dodge_ultrawide\[\]\s*=\s*\{(.*?)\n\};',s,re.S)[1]
    rates=[]
    for block in re.findall(r'\{([^{}]+)\}',table):
        bandwidth=int(re.search(r'\.bandwidth\s*=\s*(\d+)',block)[1])
        refs=re.findall(r'\.data_rate_reg_array\[(\d+)\]\[0\]\s*=\s*(\w+)',block)
        assert [int(i) for i,n in refs]==list(range(8)) and len({n for i,n in refs})==1
        name=refs[0][1];m=re.fullmatch(r'datarate_230_(\d+)(?:p(\d+))?([MG])sps_dodge_ultrawide',name);assert m
        symbol=int(decimal.Decimal(m[1]+('.'+m[2] if m[2] else ''))*(10**6 if m[3]=='M' else 10**9))
        assert bandwidth==symbol*228//100
        data=rows(name);assert len(data)==36
        counts={typ:sum(t==typ for a,v,d,t in data) for typ in {r[3] for r in data}}
        assert counts=={'CSIPHY_DEFAULT_PARAMS':27,'CSIPHY_CDR_LN_SETTINGS':3,'CSIPHY_SETTLE_CNT_LOWER_BYTE':3,'CSIPHY_SETTLE_CNT_HIGHER_BYTE':3}
        for trio in range(3):
            group=[r for r in data if (r[0]-0x200)//0x400==trio]
            assert len(group)==12
            assert all(sum(r[3]==typ for r in group)==1 for typ in ('CSIPHY_CDR_LN_SETTINGS','CSIPHY_SETTLE_CNT_LOWER_BYTE','CSIPHY_SETTLE_CNT_HIGHER_BYTE'))
        rates.append({'name':name,'max_symbol_rate':symbol,'vendor_rounded_bandwidth':bandwidth,
                      'exact_effective_rate_ceiling':(symbol*16+6)//7,'registers':data})
    assert len(rates)==29 and [r['max_symbol_rate'] for r in rates]==sorted({r['max_symbol_rate'] for r in rates})
    common=rows('csiphy_common_reg_2_3_0_dodge_ultrawide');base=rows('csiphy_3ph_v2_3_0_reg_dodge_ultrawide')
    irq=rows('csiphy_irq_reg_2_3_0_dodge_ultrawide')
    assert len(common)==4 and len(base)==72
    assert len(irq)==11 and [r[0] for r in irq]==list(range(0x102c,0x1058,4))
    assert all(r[3]=='CSIPHY_DEFAULT_PARAMS' for r in common+base+irq)
    assert re.search(r'\.refgen_status_offset\s*=\s*0x1184',s)
    assert re.search(r'\.mipi_csiphy_interrupt_status0_addr\s*=\s*0x1138',s)
    assert re.search(r'\.mipi_csiphy_interrupt_clear0_addr\s*=\s*0x1058',s)
    reset=[r for r in rows('csiphy_reset_exit_reg_2_3_0_dodge_ultrawide') if r[3]=='CSIPHY_3PH_REGS']
    assert reset==[(0x1000,0x0e,3048,'CSIPHY_3PH_REGS')]
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    params={'CSIPHY_DEFAULT_PARAMS':0,'CSIPHY_CDR_LN_SETTINGS':1,'CSIPHY_SETTLE_CNT_LOWER_BYTE':2,'CSIPHY_SETTLE_CNT_HIGHER_BYTE':3}
    header='''/* SPDX-License-Identifier: GPL-2.0-only */
    /* Generated from OnePlusOSS GPL CSIPHY v2.3.0 dodge ultrawide tables.
     * Copyright (c) 2023-2024, Qualcomm Innovation Center, Inc. All rights reserved.
     * Data-only review: no MMIO, driver registration or hardware activation.
     */
    struct phone_cphy_reg { unsigned int address,value,delay_us,type; };
    struct phone_cphy_rate { unsigned long long max_symbol_rate,vendor_bandwidth; const struct phone_cphy_reg *registers; unsigned int count; };
    '''
    for i,rate in enumerate(rates):
        header+=f'static const struct phone_cphy_reg rate_{i}[] = {{\n'
        for a,v,d,t in rate['registers']:header+=f'{{0x{a:04x},0x{v:02x},{d},{params[t]}}},\n'
        header+='};\n'
    for name,data in [('phone_common',common),('phone_base',base),('phone_irq',irq)]:
        header+=f'static const struct phone_cphy_reg {name}[] = {{\n'
        for a,v,d,t in data:header+=f'{{0x{a:04x},0x{v:02x},{d},0}},\n'
        header+='};\n'
    header+='static const struct phone_cphy_rate rates[] = {\n'
    for i,r in enumerate(rates):header+=f'{{{r["max_symbol_rate"]}ULL,{r["vendor_rounded_bandwidth"]}ULL,rate_{i},36}},\n'
    header+='};\nstatic int phone_cphy_rate_index(unsigned long long rate)\n{\n if (!rate) return -1;\n for (unsigned int i=0;i<29;i++) if (rate<=rates[i].max_symbol_rate) return i;\n return -1;\n}\n'
    header+='#include "phone-sequence.h"\n'
    (out/'catalog.h').write_text(header)
    sequence=Path(__file__).with_name('phone-sequence.h')
    (out/'phone-sequence.h').write_bytes(sequence.read_bytes())
    if args.emit_only:
        report={'scope':'SOURCE_GENERATION_ONLY_NO_COMPILATION_OR_TESTS',
                'source_sha256':hashlib.sha256(raw).hexdigest(),
                'generated_header_sha256':hashlib.sha256(header.encode()).hexdigest(),
                'sequence_source_sha256':hashlib.sha256(sequence.read_bytes()).hexdigest(),
                'rate_tables':len(rates),'compiled':False,'tests_executed':False,
                'phone_operations':0,'hardware_qualified':False}
        (out/'source-generation.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
        return
    if args.compile_only:
        source=out/'catalog-compile.c'
        source.write_text('''#include <stdint.h>
#include "catalog.h"
int catalog_compile_reference(void)
{
    struct phone_cphy_plan plan;
    int result = phone_cphy_build_plan(&plan, rates[0].max_symbol_rate,
                                      400000000U, 1000U, 3000U, 7U);
    return result ? result : phone_cphy_execute(&plan, 0, 0);
}
''')
        target=out/'catalog-arm64.o'
        compiler=(args.cross_compile+'gcc') if args.cross_compile else 'gcc'
        subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-c',
                        str(source),'-o',str(target)],check=True)
        obj=target.read_bytes()
        if obj[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(obj[18:20],'little')!=183:
            raise ValueError('Expected an AArch64 phone C-PHY table object')
        report={'scope':'PUBLIC_GPL_PHONE_CPHY_TABLE_COMPILE_ONLY',
                'source_sha256':hashlib.sha256(raw).hexdigest(),
                'generated_header_sha256':hashlib.sha256(header.encode()).hexdigest(),
                'rate_tables':len(rates),'aarch64_object_sha256':hashlib.sha256(obj).hexdigest(),
                'compiled':True,'tests_executed':False,'phone_operations':0,
                'hardware_qualified':False}
        (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
        return
    harness='''#include <assert.h>
    #include <stdint.h>
    #include "catalog.h"
    struct fake { unsigned int calls,fail_at,released; unsigned int registers[0x1200/4]; };
    static int operation(void *context,const struct phone_cphy_op *op) {
     struct fake *f=context;
     unsigned int i=f->calls++;
     if(i==f->fail_at) return -123;
     assert(op->address%4==0 && op->address<=0x1184);
     if(op->type==PHONE_POLL) {
      assert(i==0 && op->address==0x1184 && op->mask==0x80);
      assert(op->delay_us==500 && op->timeout_us==10000);
     } else {
      if(op->type==PHONE_UPDATE) {
       assert(i==1 && op->address==0x1014 && op->mask==0x2a);
       f->registers[op->address/4]=(f->registers[op->address/4]&~op->mask)|op->value;
      } else f->registers[op->address/4]=op->value;
      if(op->address==0x1000) { assert(i==125 && op->delay_us==3048); f->released++; }
     }
     return 0;
    }
    int main(void) {
     assert(phone_cphy_rate_index(0)==-1);
     for(unsigned int i=0;i<29;i++) {
      assert(phone_cphy_rate_index(rates[i].max_symbol_rate)==(int)i);
      assert(phone_cphy_rate_index(rates[i].max_symbol_rate+1)==(i==28?-1:(int)i+1));
      assert(rates[i].count==36);
      for(unsigned int j=0;j<36;j++) assert(rates[i].registers[j].value<=255);
     }
     assert(phone_cphy_rate_index(UINT64_MAX)==-1);
     struct phone_cphy_plan plan;
     for(unsigned int i=0;i<29;i++) {
      assert(phone_cphy_build_plan(&plan,rates[i].max_symbol_rate,400000000,1000,3000,7)==0);
      assert(plan.count==126 && plan.rate_index==i && plan.settle_count==790);
      struct fake f={.fail_at=UINT32_MAX}; f.registers[0x1014/4]=0x80;
      assert(phone_cphy_execute(&plan,operation,&f)==0 && f.calls==126 && f.released==1);
      assert(f.registers[0x1014/4]==0xaa);
      for(unsigned int trio=0;trio<3;trio++) {
       unsigned int base=0x200+trio*0x400;
       assert(f.registers[(base+0x0c)/4]==0x16 && f.registers[(base+0x08)/4]==3);
       assert(f.registers[(base+0x78)/4]==rates[i].registers[4+trio*8].value);
      }
     }
     for(unsigned int fail=0;fail<126;fail++) {
      struct fake f={.fail_at=fail};
      assert(phone_cphy_execute(&plan,operation,&f)==-123 && f.calls==fail+1 && !f.released);
     }
     assert(phone_cphy_build_plan(&plan,0,400000000,1000,3000,7)==-1 && !plan.count);
     assert(phone_cphy_build_plan(&plan,UINT64_MAX,400000000,1000,3000,7)==-1 && !plan.count);
     assert(phone_cphy_build_plan(&plan,80000000,200000000,1000,3000,7)==-1 && !plan.count);
     assert(phone_cphy_build_plan(&plan,80000000,400000000,0,3000,7)==-1 && !plan.count);
     assert(phone_cphy_build_plan(&plan,80000000,400000000,1000,0,7)==-1 && !plan.count);
     assert(phone_cphy_build_plan(&plan,80000000,400000000,1000000,3000,7)==-1 && !plan.count);
     assert(phone_cphy_build_plan(&plan,80000000,400000000,1,3,7)==-1 && !plan.count);
     assert(phone_cphy_build_plan(&plan,80000000,400000000,1000,3000,3)==-1 && !plan.count);
     assert(phone_cphy_execute(&plan,operation,0)==-1);
     return 0;
    }
    '''
    (out/'test.c').write_text(harness)
    for name,flags in [('release',['-O2']),('sanitized',['-O1','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
        target=out/('test-'+name)
        subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(out/'test.c'),'-o',str(target)],check=True)
        subprocess.run([target],check=True,timeout=10)
    subprocess.run([args.cross_compile+'gcc','-std=c11','-Wall','-Wextra','-Werror','-c',str(out/'test.c'),'-o',str(out/'catalog-arm64.o')],check=True)
    obj=(out/'catalog-arm64.o').read_bytes();assert obj[:6]==b'\x7fELF\x02\x01' and int.from_bytes(obj[18:20],'little')==183
    report={'scope':'PUBLIC_GPL_PHONE_CPHY_TABLES_DATA_ONLY_REVIEW','source_sha256':hashlib.sha256(raw).hexdigest(),
            'rate_tables':29,'records_per_rate':36,'base_records':72,'common_records':4,'trios':3,
            'reset_release_delay_us':3048,'reset_release_value':14,
            'vendor_bandwidth_uses_228_over_100':True,'generic_formula_is_16_over_7':True,
            'exact_threshold_naive_selection_would_advance_table':all(r['exact_effective_rate_ceiling']>r['vendor_rounded_bandwidth'] for r in rates),
            'symbol_rate_boundary_checks':60,'release_and_sanitizers_pass':True,
            'phone_sequence_operations':126,'phone_sequence_rate_cases':29,
            'phone_sequence_abort_injections':126,'phone_sequence_negative_cases':8,
            'settle_high_byte_and_default_cdr_checked':True,
            'refgen_poll_before_writes':True,'production_mmio_adapter_present':False,
            'sequence_source_sha256':hashlib.sha256(sequence.read_bytes()).hexdigest(),
            'aarch64_object_sha256':hashlib.sha256((out/'catalog-arm64.o').read_bytes()).hexdigest(),
            'generated_header_sha256':hashlib.sha256(header.encode()).hexdigest(),
            'phone_operations':0,'kernel_driver_changed':False,'hardware_qualified':False}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))

if __name__=='__main__':main()
