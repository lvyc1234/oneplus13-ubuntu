# SPDX-License-Identifier: GPL-2.0-only
import importlib.util,math,struct,sys,tempfile,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=HERE.parent
TOOLS=REPO/'tools/oneplus13'
sys.path.insert(0,str(REPO/'tools/oneplus13'))
from stock import Fdt,cells,strings
def load(name):
 s=importlib.util.spec_from_file_location(name,TOOLS/(name+'.py'))
 m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
dtb=load('microphone-dtb');capture=load('ram-microphone-check')
acoustic=load('ram-acoustic-check')
class MicrophoneCandidate(unittest.TestCase):
 def test_acoustic_capture_uses_small_flush_period(self):
  source=(REPO/'tools/oneplus13/ram-acoustic-check.py').read_text()
  self.assertIn("'--period-size','1024'",source)
  self.assertIn("'--buffer-size','4096'",source)
  self.assertIn("captures[phase_name]=raw.read_bytes()",source)
  self.assertNotIn('initial frames',source)
 def test_exact_abl_identity_and_live_power_wiring_are_required(self):
  base=(REPO/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes()
  tree=Fdt(dtb.derive(base)[0])
  files={'compatible':tree.nodes['/']['compatible'],'model':tree.nodes['/']['model']}
  for path,keys in {
    '/wcd939x':['vdd-px-supply','reset-gpios',*[f'qcom,micbias{n}-microvolt' for n in range(1,5)]],
    dtb.SUPPLY:['phandle','regulator-min-microvolt','regulator-max-microvolt'],
    dtb.audio.TLMM:['phandle'],dtb.CAPTURE+'/cpu':['sound-dai']}.items():
   for k in keys:files[(path+'/'+k).lstrip('/')]=tree.nodes[path][k]
  for scenario in ('source','abl','other-board','missing-compatible','wrong-px','wrong-bias','wrong-reset','wrong-port'):
   with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as temp:
    values=dict(files)
    if scenario!='source':values.update(compatible=capture.ABL_COMPAT,model=capture.ABL_MODEL)
    if scenario=='other-board':values['model']=capture.ABL_MODEL.replace(b'dodge',b'erhai')
    if scenario=='missing-compatible':values['compatible']=capture.ABL_COMPAT.replace(b'qcom,sunp\0',b'')
    if scenario=='wrong-px':values[dtb.SUPPLY.lstrip('/')+'/regulator-min-microvolt']=struct.pack('>I',1800000)
    if scenario=='wrong-bias':values['wcd939x/qcom,micbias1-microvolt']=struct.pack('>I',1800000)
    if scenario=='wrong-reset':values['wcd939x/reset-gpios']=values['wcd939x/reset-gpios'][:-8]+struct.pack('>II',102,1)
    if scenario=='wrong-port':values[(dtb.CAPTURE+'/cpu/sound-dai').lstrip('/')]=struct.pack('>II',1,119)
    root=Path(temp)
    for path,raw in values.items():
     target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    if scenario in ('source','abl'):
     self.assertEqual(capture.validate_board(root),'SOURCE_ONEPLUS13' if scenario=='source' else 'C93_ABL_SUN_DODGE')
    else:
     with self.assertRaises(ValueError):capture.validate_board(root)
 def test_acoustic_signal_separates_channels_and_requires_volume_contrast(self):
  wave=acoustic.signal();values=struct.unpack('<'+str(len(wave)//2)+'h',wave)
  self.assertEqual(len(wave),48000*2*2*2)
  self.assertLessEqual(max(abs(x) for x in values),16423)
  self.assertGreater(max(abs(x) for x in values),16000)
  left=values[0:96000:2];right=values[1:96000:2]
  self.assertEqual(left,right)
  for samples,freq,other in ((left,1000,1500),(right,1000,1500),
                             (values[96000:192000:2],1500,1000),
                             (values[96001:192000:2],1500,1000)):
   match=2*abs(sum(v*math.sin(2*math.pi*freq*i/48000) for i,v in enumerate(samples)))/48000
   cross=2*abs(sum(v*math.sin(2*math.pi*other*i/48000) for i,v in enumerate(samples)))/48000
   self.assertGreater(match,4000);self.assertLess(cross,5)
  def make(contrast=True):
   levels={'muted':10,'amp0_only':100000 if contrast else 10,
           'amp1_only':100000 if contrast else 10,'both':70000 if contrast else 10}
   result={}
   for phase,level in levels.items():
    out=bytearray()
    for i in range(48000*2):
     v=round(10*math.sin(2*math.pi*997*i/48000))
     v+=round(level*math.sin(2*math.pi*(1000 if i<48000 else 1500)*i/48000))
     out.extend(struct.pack('<ii',v,v//2))
    result[phase]=bytes(out)
   return result
  result=acoustic.analyze(make())
  self.assertTrue(result['correlated_two_speaker_response_pass'])
  self.assertTrue(all(v['pass'] for v in result['amp_isolation']))
  self.assertFalse(result['human_listening_performed'])
  self.assertFalse(acoustic.analyze(make(False))['correlated_two_speaker_response_pass'])
  bad=make();bad['muted']=b''
  with self.assertRaises(ValueError):acoustic.analyze(bad)
 def test_alternative_changes_only_reviewed_audio_nodes(self):
  base=(REPO/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes()
  speaker,_=dtb.audio.derive(base);candidate,proof=dtb.derive(base)
  old,new=Fdt(speaker),Fdt(candidate)
  self.assertEqual(set(old.nodes),set(new.nodes))
  for path in old.nodes:
   if path not in {dtb.WCD,'/sound',dtb.CAPTURE}:self.assertEqual(old.nodes[path],new.nodes[path],path)
  wcd=new.nodes[dtb.WCD]
  self.assertEqual(wcd['vdd-px-supply'],new.nodes[dtb.SUPPLY]['phandle'])
  self.assertEqual(cells(wcd['qcom,micbias4-microvolt']),[2700000])
  self.assertEqual(strings(new.nodes[dtb.CAPTURE]['convert-sample-format']),['s24_le'])
  self.assertEqual(strings(new.nodes['/sound/wcd-playback-dai-link']['status']),['disabled'])
  self.assertEqual(strings(new.nodes['/sound/oneplus13-speaker-playback-dai-link'].get('status',b'okay\0')),['okay'])
  self.assertEqual(new.reservations,old.reservations)
  self.assertFalse(proof['microphone_or_acoustic_acceptance'])
 def test_real_s24_container_signed_decode_and_failed_channel(self):
  frames=48000*2
  raw=b''.join(struct.pack('<ii',round(8388608*.01*math.sin(2*math.pi*440*n/48000)),
                                round(8388608*.02*math.cos(2*math.pi*440*n/48000))) for n in range(frames))
  result=capture.summarize(raw)
  self.assertTrue(result['bounded_nonzero_capture_pass'])
  self.assertAlmostEqual(result['channels'][0]['rms'],.01/math.sqrt(2),places=6)
  self.assertAlmostEqual(result['channels'][1]['rms'],.02/math.sqrt(2),places=6)
  self.assertFalse(result['physical_microphone_or_acoustic_response_accepted'])
  zero=bytes(len(raw));self.assertFalse(capture.summarize(zero)['bounded_nonzero_capture_pass'])
  one_dead=b''.join(struct.pack('<ii',1000 if n%2 else -1000,0) for n in range(frames))
  self.assertFalse(capture.summarize(one_dead)['bounded_nonzero_capture_pass'])
  clipped=struct.pack('<ii',8388607,-8388608)*frames
  self.assertFalse(capture.summarize(clipped)['bounded_nonzero_capture_pass'])
  with self.assertRaises(ValueError):capture.summarize(raw[:-8])
if __name__=='__main__':unittest.main()
