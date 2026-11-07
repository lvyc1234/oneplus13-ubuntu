import ctypes,importlib.util,json,shutil,socket,subprocess,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
power=load('phone_blank','devices/oneplus13/display-power/display-power.py')
key=load('phone_key','devices/oneplus13/display-power/power-key.py')
stereo=load('phone_stereo','tools/oneplus13/stereo-orientation.py')
audio=load('phone_audio_route','tools/oneplus13/ram-desktop-audio.py')
session=load('phone_stereo_session','tools/oneplus13/stereo-session.py')

class BlankTests(unittest.TestCase):
    def light(self,root):
        p=root/'oneplus13-aa569';(p/'device/of_node').mkdir(parents=True)
        (p/'device/of_node/compatible').write_bytes(b'oneplus,dodge-aa569\0')
        for name,value in [('brightness','1'),('max_brightness','1'),('bl_power','0')]:
            (p/name).write_text(value+'\n')
        return p
    def test_reject_wrong_panel_or_nonbinary_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self.light(root)
            self.assertEqual(power.find_backlight(root),p/'bl_power')
            (p/'max_brightness').write_text('4095\n')
            with self.assertRaises(RuntimeError):power.find_backlight(root)
            (p/'max_brightness').write_text('1\n');(p/'device/of_node/compatible').write_bytes(b'led-backlight\0')
            with self.assertRaises(RuntimeError):power.find_backlight(root)
    def test_only_active_uid_or_root_can_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.light(Path(tmp));b=power.Backlight(p/'bl_power')
            with self.assertRaises(PermissionError):b.request('AUTO 3',1001,1000)
            self.assertEqual(b.mode(),0)
            self.assertEqual(b.request('AUTO 3',1000,1000),3)
            with self.assertRaises(PermissionError):b.request('KEY 0',1000,1000)
            self.assertEqual(b.request('KEY 0',0,1000),0)
    def test_manual_blank_survives_lock_animation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.light(Path(tmp));b=power.Backlight(p/'bl_power')
            self.assertEqual(b.request('KEY 3',0,1000),3)
            self.assertEqual(b.request('AUTO 0',1000,1000),3)
            self.assertEqual(b.request('KEY 0',0,1000),0)
            b.request('KEY 3',0,1000)
            self.assertEqual(b.request('AUTO 0',127,127),0) # new seat owner
    def test_manual_zero_brightness_can_wake(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.light(Path(tmp));b=power.Backlight(p/'bl_power');(p/'brightness').write_text('0\n')
            self.assertEqual(b.mode(),3);self.assertEqual(b.request('KEY 0',0,1000),0)
            self.assertEqual((p/'brightness').read_text().strip(),'1')
    def test_invalid_request_does_not_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.light(Path(tmp));b=power.Backlight(p/'bl_power')
            for request in ['AUTO 4','KEY -1','AUTO 3 extra','SET 3','AUTO 00']:
                with self.assertRaises(ValueError):b.request(request,0,1000)
            self.assertEqual(b.mode(),0)

class KeyTests(unittest.TestCase):
    session={'User':'1000','Class':'user','LockedHint':'no'}
    def test_lock_failure_does_not_blank(self):
        with patch.object(key,'active_session',return_value=self.session),patch.object(key,'power_mode',return_value=0) as mode,patch.object(key,'screensaver',side_effect=RuntimeError('lock failed')):
            with self.assertRaises(RuntimeError):key.toggle(unittest.mock.Mock())
            mode.assert_called_once_with(self.session)
    def test_wake_preserves_authentication(self):
        wake=unittest.mock.Mock()
        with patch.object(key,'active_session',return_value=self.session),patch.object(key,'power_mode',side_effect=[3,0]) as mode,patch.object(key,'screensaver') as saver,patch.object(key,'suspend') as suspend:
            key.toggle(wake);saver.assert_not_called();suspend.assert_not_called();wake.wake.assert_called_once()
            self.assertEqual(mode.call_args_list[-1].args,(self.session,0))
    def test_greeter_blank_never_deactivates_lock(self):
        session=dict(self.session,Class='greeter')
        with patch.object(key,'active_session',return_value=session),patch.object(key,'power_mode',side_effect=[0,3]),patch.object(key,'screensaver') as saver,patch.object(key,'suspend') as suspend:
            key.toggle(unittest.mock.Mock());saver.assert_not_called();suspend.assert_called_once()
        self.assertFalse(key.suspend_requested())
    def test_locked_user_blanks_only_after_confirmation(self):
        with patch.object(key,'active_session',return_value=self.session),patch.object(key,'power_mode',side_effect=[0,3]) as mode,patch.object(key,'screensaver',side_effect=['()', '(true,)']) as saver,patch.object(key,'suspend'):
            key.toggle(unittest.mock.Mock())
            self.assertEqual([x.args[1] for x in saver.call_args_list],['Lock','GetActive'])
            self.assertEqual(mode.call_args_list[-1].args,(self.session,3))

class StereoTests(unittest.TestCase):
    def test_all_matrices_are_unity_permutations(self):
        for transform in range(8):
            params=stereo.matrix(transform)['params'];values=params[1::2]
            self.assertTrue(all(x in (0.,1.) for x in values))
            a,b,c,d=values;self.assertEqual((a+b,c+d,a+c,b+d),(1.,1.,1.,1.))
        self.assertEqual(stereo.matrix(1)['params'][1::2],[0.,1.,1.,0.])
        self.assertEqual(stereo.matrix(3)['params'][1::2],[1.,0.,0.,1.])
    def test_external_primary_does_not_control_phone(self):
        monitors=[(0,0,1.,1,True,[('DP-1','v','p','s')],{}),(0,0,2.,3,False,[('DSI-1','v','p','s')],{})]
        self.assertEqual(stereo.phone_transform(monitors),3)
    def test_unknown_ambiguous_and_invalid_transforms_rejected(self):
        for value in [-1,8,None,True,'3']:
            with self.assertRaises(ValueError):stereo.matrix(value)
        with self.assertRaises(ValueError):stereo.phone_transform([])
        with self.assertRaises(ValueError):stereo.phone_transform([(0,0,1.,3,True,[('DP-1','v','p','s')],{})])
        row=(0,0,1.,3,True,[('DSI-1','v','p','s')],{})
        with self.assertRaises(ValueError):stereo.phone_transform([row,row])

    def test_native_streams_only_moved_while_muted_and_volume_is_bounded(self):
        calls=[]
        def run(argv,*args,**kwargs):
            calls.append(argv)
            if argv==['pw-dump']:
                return json.dumps([{'type':'PipeWire:Interface:Node','id':42,
                    'info':{'props':{'node.name':'oneplus13_stereo'}}},
                    {'type':'PipeWire:Interface:Node','id':59,
                     'info':{'props':{'node.name':'oneplus13_stereo_out','object.serial':101}}}])
            if argv==['pactl','--format=json','list','sinks']:
                return json.dumps([{'name':'oneplus13_speakers','index':7}])
            if argv==['pactl','--format=json','list','sink-inputs']:
                return json.dumps([{'index':90,'sink':7},{'index':91,'sink':8},
                    {'index':101,'sink':7},
                    {'index':102,'sink':7,'properties':{'node.name':'oneplus13_stereo_out'}}])
            return ''
        with patch.object(audio,'run',side_effect=run):
            self.assertEqual(audio.stereo_sink({'id':31,'name':'oneplus13_speakers'}),{'id':42,'name':'oneplus13_stereo'})
        self.assertEqual(calls[0],['/usr/libexec/oneplus13-stereo-session.py','prepare','31','oneplus13_speakers'])
        self.assertEqual(calls[1],['wpctl','set-mute','31','1'])
        self.assertIn(['pactl','move-sink-input','90','oneplus13_stereo'],calls)
        for index in ['91','101','102']:
            self.assertNotIn(['pactl','move-sink-input',index,'oneplus13_stereo'],calls)
        self.assertLess(calls.index(['pactl','set-default-sink','oneplus13_stereo']),calls.index(['wpctl','set-volume','--limit','1','31','1']))
        self.assertEqual(calls[-1],['wpctl','set-mute','31','0'])
        self.assertIn(['wpctl','set-volume','--limit','1','42','0.5'],calls)

    def test_ambiguous_native_pulse_target_never_unmutes_unity_sink(self):
        calls=[]
        def run(argv,*args,**kwargs):
            calls.append(argv)
            if argv==['pw-dump']:return json.dumps([{'type':'PipeWire:Interface:Node','id':42,'info':{'props':{'node.name':'oneplus13_stereo'}}},
                    {'type':'PipeWire:Interface:Node','id':59,
                     'info':{'props':{'node.name':'oneplus13_stereo_out','object.serial':101}}}])
            if argv==['pactl','--format=json','list','sinks']:return '[]'
            return ''
        with patch.object(audio,'run',side_effect=run):
            with self.assertRaises(ValueError):audio.stereo_sink({'id':31,'name':'oneplus13_speakers'})
        self.assertNotIn(['wpctl','set-mute','31','0'],calls)
        self.assertNotIn(['wpctl','set-volume','--limit','1','31','1'],calls)

    def test_ucm_name_is_bound_instead_of_guessed_fallback(self):
        name='alsa_output.platform-oneplus13.HiFi__Speaker__sink'
        node={'id':31,'info':{'props':{'node.name':name,'object.serial':77,'media.class':'Audio/Sink'}}}
        target=session.select_native([node],31,name)
        self.assertEqual(target,dict(id=31,name=name,serial=77))
        config=session.render_config('{"target.object": "oneplus13_speakers"}',target['name'])
        self.assertEqual(json.loads(config)['target.object'],name)
        # A reused numeric ID or an unexpected source node is refused.
        with self.assertRaises(ValueError):session.select_native([node],31,'oneplus13_speakers')
        node['info']['props']['media.class']='Audio/Source'
        with self.assertRaises(ValueError):session.select_native([node],31,name)

    def test_target_name_is_encoded_and_ambiguous_template_refused(self):
        marker='{"target.object": "oneplus13_speakers"}'
        name='speaker" \\ name'
        self.assertEqual(json.loads(session.render_config(marker,name))['target.object'],name)
        with self.assertRaises(ValueError):session.render_config(marker+marker,name)
        with self.assertRaises(ValueError):session.render_config('{}',name)

    def test_replacement_sink_has_different_identity(self):
        props={'node.name':'oneplus13_speakers','object.serial':77,'media.class':'Audio/Sink'}
        node={'id':31,'info':{'props':props}}
        old=session.identity(node)
        props['object.serial']=78
        self.assertNotEqual(session.identity(node),old)

@unittest.skipUnless(shutil.which('gcc'),'native C compiler required')
class AdapterTests(unittest.TestCase):
    def test_real_shared_adapter_uses_only_private_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'control';library=root/'adapter.so'
            subprocess.run(['gcc','-shared','-fPIC','-O2','-Wall','-Wextra','-Werror',
                '-DONEPLUS13_CONTROL_SOCKET='+json.dumps(str(path)),
                str(ROOT/'devices/oneplus13/display-power/gsd-backlight-compat.c'),'-o',str(library)],check=True)
            server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(path));server.listen();server.settimeout(2)
            requests=[]
            def receive():
                with server.accept()[0] as connection:
                    requests.append(connection.recv(32));connection.sendall(b'OK 3\n')
            worker=threading.Thread(target=receive);worker.start()
            adapter=ctypes.CDLL(str(library));setter=adapter.gsd_display_config_set_power_save_mode
            setter.argtypes=[ctypes.c_void_p,ctypes.c_int];setter.restype=None
            setter(None,3);worker.join(3);server.close()
            self.assertFalse(worker.is_alive());self.assertEqual(requests,[b'AUTO 3\n'])

if __name__=='__main__':unittest.main()
