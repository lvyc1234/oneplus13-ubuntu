"""Count touchscreen event types only; no grabs, raw positions or key capture."""
from pathlib import Path
import argparse,collections,json,os,select,struct,time
EVENT=struct.Struct('<qqHHi')  # Linux ARM64 input_event; independent of host ABI.

def consume(pending,counts):
    while len(pending)>=EVENT.size:
        _,_,kind,code,value=EVENT.unpack(pending[:EVENT.size]);pending=pending[EVENT.size:]
        if kind==3:counts['absolute_events']+=1
        if kind==0 and code==0:counts['syn_reports']+=1
        if kind==1 and code==330 and value==1:counts['touch_downs']+=1
    return pending

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,choices=[30,60,90,120],default=90)
    duration=parser.parse_args().seconds
    node=None
    for p in Path('/sys/class/input').glob('event*/device/name'):
        if 'S3910' in p.read_text():node=Path('/dev/input')/p.parent.parent.name;break
    if node is None:
        print(json.dumps({'touch_node_found':False,'events':0}));return
    counts=collections.Counter();end=time.monotonic()+duration
    fd=os.open(node,os.O_RDONLY|os.O_NONBLOCK);pending=b''
    try:
        while time.monotonic()<end:
            ready,_,_=select.select([fd],[],[],min(.5,max(0,end-time.monotonic())))
            if not ready:continue
            pending+=os.read(fd,EVENT.size*64)
            pending=consume(pending,counts)
    finally:os.close(fd)
    print(json.dumps({'touch_node_found':True,'duration_seconds':duration,'counts':dict(counts),'raw_positions_saved':False,'keys_saved':False}))

if __name__=='__main__':main()
