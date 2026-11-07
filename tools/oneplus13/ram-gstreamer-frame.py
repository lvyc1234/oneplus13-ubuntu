#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded explicit V4L2 GStreamer path; no autoplug/software decoder fallback."""
import argparse, hashlib, json, os, re, sys, time
from pathlib import Path

WIDTH,HEIGHT,PTS=320,240,12345000000
ENCODED_CAPS=('video/x-h264,stream-format=byte-stream,alignment=au,parsed=(boolean)true,'
              'width=320,height=240,framerate=1/1,'
              'profile=constrained-baseline,level=(string)1.1,colorimetry=bt601')
# This CLI accepts only the hash-bound synthetic AU below. Its SPS is
# profile_idc=66, constraint_set0/1=1, level_idc=11; the generated limited-range
# test card uses BT.601. The driver's probed sink caps constrain all three
# fields, so parsed=true alone is insufficient for ACCEPT_CAPS.

def negotiated_caps(pipe):
    rows = []
    iterator = pipe.iterate_elements()
    # A changing bin can keep returning RESYNC. Bound the diagnostic itself so
    # collecting an error cannot prevent the pipeline's finally/NULL cleanup.
    for attempt in range(16):
        result, element = iterator.next()
        if result == 0:
            break
        if result == 2:
            iterator.resync()
            continue
        if result != 1:
            break
        pads = {}
        for direction, getter in (('sink', element.get_static_pad),
                                  ('src', element.get_static_pad)):
            pad = getter(direction)
            if pad is None:
                continue
            current = pad.get_current_caps()
            queried = pad.query_caps(None)
            pads[direction] = {
                'current': current.to_string()[:2048] if current else None,
                'query': queried.to_string()[:2048] if queried else None,
            }
        rows.append({'element': element.get_name()[:128], 'pads': pads})
        if len(rows) >= 8:
            break
    return rows


def private_pipeline_error(message, pipe=None):
    error,debug=message.parse_error()
    print('GSTREAMER_PRIVATE_ERROR '+json.dumps({
        'domain':str(error.domain),'code':int(error.code),
        'message':str(error.message)[:1024],'debug':str(debug)[:4096],
        'negotiated_caps':negotiated_caps(pipe) if pipe is not None else [],
        'raw_diagnostic_publishable':False},sort_keys=True),file=sys.stderr,flush=True)
    return 'GStreamer pipeline error '+str(error.domain)+':'+str(error.code)

def verify_pixels(data,stride,offset=0):
    if (type(stride) is not int or type(offset) is not int or
        not WIDTH<=stride<=2048 or not 0<=offset<=65536 or
        offset+(HEIGHT-1)*stride+WIDTH>len(data)):
        raise ValueError('Invalid NV12 layout')
    for y in range(8,HEIGHT-8):
        for bar,value in enumerate((81,145,41,235)):
            row=data[offset+y*stride+bar*80+8:offset+y*stride+(bar+1)*80-8]
            if any(abs(pixel-value)>6 for pixel in row):
                raise ValueError('Decoded pixels differ from synthetic card')

def gstreamer():
    import gi
    gi.require_version('Gst','1.0');gi.require_version('GstVideo','1.0')
    from gi.repository import Gst,GstVideo
    Gst.init(None)
    return Gst,GstVideo

def select_decoder(Gst,device):
    plugin=Gst.Registry.get().find_plugin('video4linux2')
    if plugin is None:raise ValueError('Missing video4linux2 plugin')
    Gst.Plugin.load_file(plugin.get_filename())
    factories=sorted(Gst.ElementFactory.list_get_elements(
        Gst.ELEMENT_FACTORY_TYPE_DECODER,Gst.Rank.NONE),key=lambda item:item.get_name())
    for factory in factories:
        name=factory.get_name()
        if (not re.fullmatch(r'v4l2(?:video[0-9]+)?h264dec',name) or
            factory.get_plugin_name()!='video4linux2'):continue
        candidate=Gst.ElementFactory.make(name,None)
        # Dynamic factories bind a device at registration; their inherited
        # device property is read-only. Select by that exact registered path.
        if (candidate is not None and candidate.find_property('device') and
            candidate.get_property('device')==device):
            return candidate,name
    raise ValueError('No explicit V4L2 H264 decoder factory for guarded device')

def pipeline_frame(raw,device=None):
    Gst,GstVideo=gstreamer()
    pipe=Gst.Pipeline.new('oneplus13-synthetic-hardware-frame')
    source=Gst.ElementFactory.make('appsrc','synthetic-source')
    sink=Gst.ElementFactory.make('appsink','verified-frame')
    if source is None or sink is None:raise ValueError('Missing appsrc/appsink')
    decoder=None;factory_name=None
    if device is not None:
        decoder,factory_name=select_decoder(Gst,device)
    source.set_property('format',Gst.Format.TIME)
    source.set_property('block',False)
    source.set_property('max-bytes',16384 if decoder else 262144)
    caps=(ENCODED_CAPS
          if decoder else 'video/x-raw,format=NV12,width=320,height=240,framerate=1/1')
    source.set_property('caps',Gst.Caps.from_string(caps))
    sink.set_property('caps',Gst.Caps.from_string('video/x-raw,format=NV12'))
    sink.set_property('sync',False);sink.set_property('max-buffers',1)
    sink.set_property('wait-on-eos',False)
    elements=[source,decoder,sink] if decoder else [source,sink]
    for element in elements:pipe.add(element)
    for first,second in zip(elements,elements[1:]):
        if not first.link(second):raise ValueError('Explicit pipeline link failed')
    report={'frame_verified':False,'pipeline_stopped':False,'software_fallback':False,
            'hardware_decoder_factory':factory_name,'scope':'GSTREAMER_EXPLICIT_V4L2_FRAME' if decoder else 'GSTREAMER_RAW_PIPELINE_SELFTEST_NO_DECODE_REQUEST'}
    try:
        bus=pipe.get_bus()
        if pipe.set_state(Gst.State.PLAYING)==Gst.StateChangeReturn.FAILURE:
            error=bus.timed_pop_filtered(0,Gst.MessageType.ERROR)
            raise ValueError(private_pipeline_error(error,pipe) if error else 'Pipeline PLAYING failed')
        buffer=Gst.Buffer.new_allocate(None,len(raw),None);buffer.fill(0,raw)
        buffer.pts=PTS;buffer.dts=PTS;buffer.duration=Gst.SECOND
        if source.emit('push-buffer',buffer)!=Gst.FlowReturn.OK:
            raise ValueError('Synthetic AU queue failed')
        if source.emit('end-of-stream')!=Gst.FlowReturn.OK:raise ValueError('Drain request failed')
        until=time.monotonic()+12;sample=None
        while time.monotonic()<until:
            error=bus.timed_pop_filtered(0,Gst.MessageType.ERROR)
            if error:
                raise ValueError(private_pipeline_error(error,pipe))
            sample=sink.emit('try-pull-sample',100000000)
            if sample is not None:break
        if sample is None:
            error=bus.timed_pop_filtered(0,Gst.MessageType.ERROR)
            raise ValueError(private_pipeline_error(error,pipe) if error else 'Decoded frame timeout')
        info=GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        if info.width!=WIDTH or info.height!=HEIGHT or info.finfo.name!='NV12':
            raise ValueError('Unexpected decoded frame format')
        out=sample.get_buffer()
        if out.pts!=PTS or out.get_size()>2097152:raise ValueError('Wrong timestamp or buffer bound')
        meta=GstVideo.buffer_get_video_meta(out)
        stride=int(meta.stride[0] if meta else info.stride[0])
        offset=int(meta.offset[0] if meta else info.offset[0])
        verify_pixels(out.extract_dup(0,out.get_size()),stride,offset)
        report.update(frame_verified=True,width=info.width,height=info.height,
                      stride=stride,pts_ns=int(out.pts))
    finally:
        changed=pipe.set_state(Gst.State.NULL)
        result,state,pending=pipe.get_state(3*Gst.SECOND)
        report['pipeline_stopped']=(changed!=Gst.StateChangeReturn.FAILURE and
                                    result!=Gst.StateChangeReturn.FAILURE and state==Gst.State.NULL)
        # Also report teardown after failed negotiation/decode. A failure must
        # not erase evidence of whether the framework actually closed its FD.
        print('GSTREAMER_PIPELINE_STATUS '+json.dumps({
            'scope':report['scope'],'frame_verified':report['frame_verified'],
            'pipeline_stopped':report['pipeline_stopped'],
            'software_fallback':False},sort_keys=True),flush=True)
        if not report['pipeline_stopped']:raise ValueError('Pipeline teardown failed')
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selftest',action='store_true')
    parser.add_argument('--device');parser.add_argument('--fixture',type=Path)
    args=parser.parse_args()
    if args.selftest:
        if args.device or args.fixture:raise ValueError('Selftest cannot open device/input')
        raw=b''.join(bytes((81,145,41,235)[x//80] for x in range(WIDTH)) for y in range(HEIGHT))
        raw+=bytes([128])*(WIDTH*HEIGHT//2)
        result=pipeline_frame(raw)
    else:
        if (os.geteuid()!=0 or os.environ.get('ONEPLUS13_GUARDED_VIDEO_TEST')!='1' or
            not args.device or not re.fullmatch(r'/dev/video[0-9]+',args.device) or not args.fixture):
            raise ValueError('Guarded explicit device and synthetic fixture required')
        if args.fixture.is_symlink() or not args.fixture.is_file() or args.fixture.stat().st_size!=905:
            raise ValueError('Invalid fixture input')
        raw=args.fixture.read_bytes()
        if hashlib.sha256(raw).hexdigest()!='9abde2249b25b41fdd959f5d94901a0c865c5be2d242617991a77875cf3cb4d0':
            raise ValueError('Foreign fixture rejected')
        result=pipeline_frame(raw,args.device)
    print('GSTREAMER_FRAME_RESULT '+json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
