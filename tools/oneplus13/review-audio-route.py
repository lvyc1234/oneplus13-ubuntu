"""Offline route review only: parses saved controls, never runs amixer/device IO."""
import argparse, copy, hashlib, json, re
from pathlib import Path

EXPECTED = [
    ('WSA RX0 MUX', 'AIF1_PB', 'macro source'),
    ('WSA RX1 MUX', 'AIF1_PB', 'macro source'),
    ('WSA_RX0 INP0', 'RX0', 'interpolator input'),
    ('WSA_RX1 INP0', 'RX1', 'interpolator input'),
    ('SpkrLeft DAC Switch', 'on', 'slave DAC port'),
    ('SpkrRight DAC Switch', 'on', 'slave DAC port'),
    ('WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1', 'on,on', 'FE to BE'),
]

def parse_dump(text):
    if 'FULL_MIXER_BEGIN' in text:
        text=text.split('FULL_MIXER_BEGIN',1)[1].split('FULL_MIXER_END',1)[0]
    result={}
    for block in re.split(r'(?m)(?=^numid=)',text):
        name=re.search(r"name='([^']+)'",block)
        if not name: continue
        value=re.search(r'(?m)^ *: values=(.*)$',block)
        kind=re.search(r'type=([A-Z]+)',block)
        count=re.search(r'values=(\d+)',block)
        result[name[1]]={'type':kind[1] if kind else None,
                        'count':int(count[1]) if count else None,
                        'value':value[1].strip() if value else None,
                        'enum':re.findall(r"Item #[0-9]+ '([^']+)'",block)}
    return result

def review(controls):
    changes=[];failures=[]
    for name,target,role in EXPECTED:
        c=controls.get(name)
        if c is None:
            failures.append({'control':name,'reason':'missing control'});continue
        if c['enum']:
            valid=c['type']=='ENUMERATED' and target in c['enum'] and c['count']==1
            try: current=c['enum'][int(c['value'])]
            except (ValueError,TypeError,IndexError): current=None;valid=False
        else:
            target_count=len(target.split(','))
            valid=c['type']=='BOOLEAN' and c['count']==target_count
            vals=(c['value'] or '').split(',')
            valid=valid and len(vals)==target_count and all(v in ('off','on','0','1') for v in vals)
            current=','.join('on' if v=='1' else 'off' if v=='0' else v for v in vals)
        if not valid:failures.append({'control':name,'reason':'type/count/value/enum mismatch'});continue
        changes.append({'control':name,'role':role,'current':current,'proposed':target,
                        'already_matches':current==target,'restore_exact':c['value']})
    return {'scope':'OFFLINE_SAVED_EVIDENCE_ONLY_NO_APPLY_COMMAND',
            'control_schema_valid':not failures,'schema_failures':failures,
            'route_state_ready':not failures and all(x['already_matches'] for x in changes),
            'incomplete_controls':[x['control'] for x in changes if not x['already_matches']],
            'proposed_route':changes,'phone_operations':0,'gain_changes':0,
            'eligible_for_live_stream':False,
            'reason_stream_gate_closed':'Phone SoundWire timing/port behavior and protected output still need independent review; route completeness alone is not stream safety.'}

def tests(controls):
    assert review(controls)['control_schema_valid']
    bad=copy.deepcopy(controls);del bad['SpkrLeft DAC Switch']
    assert not review(bad)['control_schema_valid']
    bad=copy.deepcopy(controls);bad['WSA RX0 MUX']['enum']=['ZERO','PadTDM']
    assert not review(bad)['control_schema_valid']
    bad=copy.deepcopy(controls);bad[EXPECTED[-1][0]]['count']=1
    assert not review(bad)['control_schema_valid']
    ready=copy.deepcopy(controls)
    for name,target,_ in EXPECTED:
        c=ready[name];c['value']=str(c['enum'].index(target)) if c['enum'] else target
    result=review(ready);assert result['route_state_ready'] and not result['eligible_for_live_stream']
    assert all('Volume' not in name for name,_,_ in EXPECTED)
    return ['actual dump schema','missing codec gate','wrong board enum rejection','stereo boolean arity rejection','complete route does not authorize stream','no gain controls']

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dump',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();controls=parse_dump(a.dump.read_text(encoding='utf-8'));result=review(controls)
    result['tests']=tests(controls);result['input_sha256']=hashlib.sha256(a.dump.read_bytes()).hexdigest()
    a.output.parent.mkdir(exist_ok=True,parents=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'control_schema_valid':result['control_schema_valid'],'incomplete_controls':result['incomplete_controls'],'tests':len(result['tests']),'live_stream_gate_open':False},indent=2))
