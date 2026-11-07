"""Read existing NetworkManager AP cache; no scan or connection request."""
import argparse,json,subprocess
from pathlib import Path

def classify(text,config):
    names=[config['ssid'],config['comparison_ssid']]
    result={'scope':'READ_ONLY_TWO_TARGET_CACHE','requested_scan':False,'targets':[]}
    rows=[line.rpartition(':') for line in text.splitlines()]
    for name in names:
        frequencies=sorted({int(normalized) for ssid,sep,freq in rows
                            if sep and ssid==name
                            and (normalized:=freq.strip().removesuffix('MHz').strip()).isdecimal()})
        result['targets'].append({'seen':bool(frequencies),'frequencies_mhz':frequencies})
    result['cache_rows']=len(rows)
    frequencies=[int(value) for _,sep,freq in rows if sep and (value:=freq.strip().removesuffix('MHz').strip()).isdecimal()]
    result['band_rows']={name:sum(low<=f<high for f in frequencies)
                         for name,low,high in [('2.4GHz',2400,2501),('5GHz',4900,5925),('6GHz',5925,7126)]}
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args()
    config=json.loads(a.config.read_text());a.config.unlink()
    assert ' / overlay ' in Path('/proc/mounts').read_text()
    dev=subprocess.run(['nmcli','--wait','4','-t','-f','DEVICE,TYPE','device'],capture_output=True,text=True,timeout=6)
    interfaces=[x.rsplit(':',1)[0] for x in dev.stdout.splitlines() if x.endswith(':wifi')]
    assert dev.returncode==0 and len(interfaces)==1
    result=subprocess.run(['nmcli','--wait','4','--escape','no','-t','-f','SSID,FREQ','device','wifi','list','ifname',interfaces[0],'--rescan','no'],capture_output=True,text=True,timeout=6)
    assert result.returncode==0
    print('WIFI_BAND_CACHE_RESULT '+json.dumps(classify(result.stdout,config)))

if __name__=='__main__':main()
