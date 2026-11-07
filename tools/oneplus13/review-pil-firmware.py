#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host-only split PIL metadata, segment and reserved-memory review.

Follows mdt_loader.c at 7a81d184e3098624d4342d8f4eff5e23674bcc9f.
This stricter check never authenticates, loads or starts firmware. It cannot
establish DSP ABI, board electrical compatibility, protection or sensor safety.
"""
import argparse,hashlib,json,re,struct
from pathlib import Path
from stock import Fdt,strings

TYPE_MASK=7<<24
TYPE_HASH=2<<24
RELOCATABLE=1<<27
EHDR=struct.Struct('<16sHHIIIIIHHHHHH')
PHDR=struct.Struct('<8I')
LIMIT=96*1024*1024
MAX_CARVEOUT=LIMIT*4

def require(condition,message):
    if not condition:raise ValueError(message)

def inspect_split(directory,stem,mem_phys,mem_size):
    require(re.fullmatch(r'(?:adsp|modem)(?:_dtb)?',stem),'Unsupported firmware role')
    require(mem_phys>0 and 0<mem_size<MAX_CARVEOUT,'Invalid carveout')
    data=(directory/(stem+'.mdt')).read_bytes()
    require(EHDR.size<=len(data)<1024*1024,'Invalid MDT size')
    h=EHDR.unpack_from(data)
    require(h[0][:7]==b'\x7fELF\x01\x01\x01' and h[3]==1,'Expected ELF32 little endian version one')
    require(h[8]==EHDR.size and h[9]==PHDR.size and 2<=h[10]<=96,'Invalid program header geometry')
    require(h[5]>=EHDR.size and h[5]+h[9]*h[10]<=len(data),'Truncated program headers')
    require(h[11]==h[12]==0,'Unexpected section table in split MDT')
    headers=[PHDR.unpack_from(data,h[5]+i*h[9]) for i in range(h[10])]
    require(headers[0][0]!=1,'Metadata header must not be loadable')
    hashes=[(i,p) for i,p in enumerate(headers) if p[6]&TYPE_MASK==TYPE_HASH]
    require(len(hashes)==1 and hashes[0][0]>0,'One hash segment is required')
    hash_index,hash_header=hashes[0]
    header_size=headers[0][4];hash_size=hash_header[4]
    require(header_size>=h[5]+PHDR.size*h[10] and hash_size>0,'Invalid metadata sizes')
    require(header_size+hash_size==len(data),'Expected tightly packed split metadata')
    expected={stem+'.mdt'}
    sizes={};digests={stem+'.mdt':hashlib.sha256(data).hexdigest()}
    for index,p in enumerate(headers):
        if not p[4]:continue
        require(p[4]<=LIMIT,'Oversized split segment')
        name=stem+'.b'+str(index).zfill(2);expected.add(name)
        path=directory/name
        require(path.is_file() and not path.is_symlink(),'Missing or linked split segment')
        require(path.stat().st_size==p[4],'Split segment length mismatch')
        blob=path.read_bytes();sizes[name]=len(blob);digests[name]=hashlib.sha256(blob).hexdigest()
        if index==0:require(blob==data[:header_size],'Header segment differs from MDT')
        if index==hash_index:require(blob==data[header_size:],'Hash segment differs from MDT')
    actual={p.name for p in directory.iterdir() if re.fullmatch(re.escape(stem)+r'\.(?:mdt|b[0-9]{2})',p.name)}
    require(actual==expected,'Extra or missing split firmware files')
    require(sum(sizes.values())<LIMIT,'Split set exceeds bounded size')
    loads=[(i,p) for i,p in enumerate(headers) if p[0]==1 and p[6]&TYPE_MASK!=TYPE_HASH and p[5]]
    require(loads,'No loadable segments')
    relocate=any(p[6]&RELOCATABLE for _,p in loads)
    base=min(p[3] for _,p in loads) if relocate else mem_phys
    ranges=[]
    for index,p in loads:
        require(p[4]<=p[5],'File data exceeds segment memory size')
        offset=p[3]-base
        require(0<=offset and offset+p[5]<=mem_size,'Segment outside reserved memory')
        require(p[3]+p[5]<=2**32,'ELF32 address wrap')
        ranges.append((offset,offset+p[5]))
    ranges.sort()
    require(all(a[1]<=b[0] for a,b in zip(ranges,ranges[1:])),'Overlapping loadable segments')
    span=((max(p[3]+p[5] for _,p in loads)+4095)&~4095)-min(p[3] for _,p in loads)
    require(span<=mem_size,'Rounded firmware span exceeds carveout')
    return {'files':digests,'program_headers':len(headers),'loadable_segments':len(loads),
        'split_files':len(expected),'relocatable':relocate,'required_span_bytes':span,
        'reserved_bytes':mem_size,'all_segment_lengths_and_ranges_pass':True,
        'metadata_and_split_header_hash_bytes_match':True,'signature_authenticated':False}

def review(directory,dtb,stock_hashes):
    tree=Fdt(dtb)
    node=tree.nodes['/soc@0/remoteproc@6800000']
    require('qcom,sm8750-adsp-pas' in strings(node['compatible']),'Wrong ADSP controller')
    require(strings(node.get('status',b'okay\0'))==['okay'],'ADSP must be enabled for a loader review')
    names=strings(node['firmware-name'])
    require(len(names)==2 and [Path(x).name for x in names]==['adsp.mdt','adsp_dtb.mdt'],'Wrong firmware roles')
    require(all(not x.startswith('/') and '..' not in Path(x).parts for x in names),'Unsafe firmware lookup path')
    refs=struct.unpack('>2I',node['memory-region'])
    regions=[]
    for ref in refs:
        entries=[(p,v) for p,v in tree.nodes.items() if v.get('phandle')==struct.pack('>I',ref)]
        require(len(entries)==1 and entries[0][0].startswith('/reserved-memory/'),'Ambiguous carveout')
        path,props=entries[0]
        require('no-map' in props and len(props['reg'])==16,'Expected fixed no-map 64-bit carveout')
        regions.append(struct.unpack('>QQ',props['reg']))
    require(regions[0][0]+regions[0][1]<=regions[1][0] or regions[1][0]+regions[1][1]<=regions[0][0],
        'ADSP and DTB carveouts overlap')
    sets={stem:inspect_split(directory,stem,*region) for stem,region in zip(['adsp','adsp_dtb'],regions)}
    digests={name:digest for group in sets.values() for name,digest in group.pop('files').items()}
    require(digests==stock_hashes,'Firmware set does not exactly match current stock hashes')
    return {'scope':'HOST_STOCK_PIL_STRUCTURE_AND_IDENTITY_ONLY','phone_operations':0,
        'stock_hash_matches':len(digests),'complete_stock_set_match':True,
        'firmware_lookup_names':names,'dtb_sha256':hashlib.sha256(dtb).hexdigest(),'roles':sets,
        'firmware_files':digests,'signature_authenticated':False,'dsp_abi_verified':False,
        'sensor_attach_or_audio_stream_eligible':False,'hardware_functional_acceptance':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--firmware',required=True,type=Path)
    p.add_argument('--dtb',required=True,type=Path);p.add_argument('--stock-hashes',required=True,type=Path)
    p.add_argument('--report',required=True,type=Path);a=p.parse_args()
    stock=json.loads(a.stock_hashes.read_text())
    require(stock['baseline_match'] and stock['stock_firmware_read_only'],'Unverified stock evidence')
    result=review(a.firmware,a.dtb.read_bytes(),stock['files'])
    a.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['firmware_files','firmware_lookup_names']},indent=2))

if __name__=='__main__':main()
