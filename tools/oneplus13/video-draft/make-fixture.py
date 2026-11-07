#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate a public synthetic AVC frame; no camera or recording input."""
import argparse, hashlib, json, subprocess
from pathlib import Path


def pixels():
    # Limited-range BT.601 red, green, blue, white, as four vertical bars.
    planes=[]
    for width,height,values in ((320,240,(81,145,41,235)),
                               (160,120,(90,54,240,128)),
                               (160,120,(240,34,110,128))):
        planes.append(bytes(v for v in values for _ in range(width//4))*height)
    return b''.join(planes)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Preserve the recorded fixture')
    command=[args.ffmpeg,'-hide_banner','-loglevel','error','-f','rawvideo',
             '-pixel_format','yuv420p','-video_size','320x240','-framerate','1',
             '-i','pipe:0','-map_metadata','-1','-frames:v','1','-c:v','libx264',
             '-profile:v','baseline','-pix_fmt','yuv420p','-tune','zerolatency',
             '-x264-params','bframes=0:keyint=1:min-keyint=1:scenecut=0:threads=1:repeat-headers=1:aud=1',
             '-qp','20','-flags','+bitexact','-fflags','+bitexact','-f','h264','pipe:1']
    raw=subprocess.run(command,input=pixels(),capture_output=True,timeout=20,check=True).stdout
    if not 64<len(raw)<16384:raise ValueError('Unexpected synthetic stream size')
    value={'format':'ONEPLUS13_SYNTHETIC_AVC_BARS_V1','width':320,'height':240,
           'frames':1,'annex_b':True,'capture_input':False,
           'expected_luma_bars':[81,145,41,235],'maximum_luma_error':6,
           'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'hex':raw.hex(),
           'encoder_version':subprocess.check_output([args.ffmpeg,'-version'],text=True).splitlines()[0]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({key:value[key] for key in ('sha256','bytes','width','height')}))


if __name__=='__main__':main()
