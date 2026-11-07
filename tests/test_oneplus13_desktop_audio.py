"""Desktop audio preserves all unrelated denials and caps hardware/software gain."""
import hashlib,importlib.util,unittest,tempfile,types,subprocess
from unittest import mock
from pathlib import Path
R=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('desktop_audio',R/'tools/oneplus13/ram-desktop-audio.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class DesktopAudio(unittest.TestCase):
    def test_real_first_user_is_used_instead_of_legacy_account(self):
        owner=types.SimpleNamespace(pw_uid=1000,pw_name='ownerchoice')
        with mock.patch.object(m.pwd,'getpwuid',return_value=owner) as lookup, \
                mock.patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'ok','')) as execute:
            self.assertEqual(m.run(['pactl','info'],user=True),'ok')
        lookup.assert_called_once_with(1000)
        self.assertEqual(execute.call_args.args[0][:4],['runuser','-u','ownerchoice','--'])

    def test_replaced_user_bus_never_receives_previous_sessions_numeric_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bus=root/'bus';bus.touch()
            module=root/'module';module.write_text('12\n')
            node=root/'node';node.write_text('34\n')
            session=root/'session';session.write_text(str(bus.stat().st_ino+1)+'\n')
            calls=[]
            def execute(args,*a,**kw):
                self.assertFalse(kw.get('user',False),'Stale IDs reached the new user server')
                calls.append(args);return ''
            with mock.patch.multiple(m,MODULE=module,NODE=node,SESSION=session,RULE=root/'absent'), \
                    mock.patch.object(m,'user_bus',return_value=bus),mock.patch.object(m,'run',side_effect=execute):
                m.disable('0')
            self.assertFalse(module.exists());self.assertFalse(node.exists());self.assertFalse(session.exists())
            self.assertEqual(len(calls),3)
            self.assertTrue(all(args[0]=='amixer' for args in calls))

    def test_sink_binding_follows_pipewire_device_id(self):
        objects=[
            {'id':7,'type':'PipeWire:Interface:Device','info':{'props':{
                'api.alsa.card':'0','api.alsa.card.name':'OnePlus13-Audio-Probe'}}},
            {'id':10,'type':'PipeWire:Interface:Node','info':{'props':{
                'media.class':'Audio/Sink','device.id':7,'node.name':'phone_speaker'}}},
            {'id':11,'type':'PipeWire:Interface:Node','info':{'props':{
                'media.class':'Audio/Sink','device.id':99,'node.name':'other'}}},
        ]
        result=m.desktop_sinks(objects,'0')
        self.assertEqual([item['name'] for item in result],['phone_speaker'])
        self.assertNotIn('OnePlus13-Audio-Probe',str(result))

    def test_native_fallback_binds_explicit_alsa_path(self):
        objects=[{'id':12,'type':'PipeWire:Interface:Node','info':{'props':{
            'media.class':'Audio/Sink','api.alsa.path':'hw:0,0',
            'node.name':'oneplus13_speakers'}}}]
        result=m.desktop_sinks(objects,'0')
        self.assertEqual([item['id'] for item in result],[12])
        source=(R/'tools/oneplus13/ram-desktop-audio.py').read_text()
        self.assertIn("'spa-node-factory'",source)
        self.assertIn("'factory.name=api.alsa.pcm.sink'",source)

    def test_only_original_or_exact_display_prerequisite_policy_is_allowed(self):
        original=b'install cec /bin/false\ninstall ubwc_config /bin/false\ninstall snd_soc_aw88461 /bin/false\n'
        digest=hashlib.sha256(original).hexdigest()
        m.validate_policy(original,digest)
        display=original.replace(b'install cec /bin/false',b'install cec /bin/true').replace(b'install ubwc_config /bin/false',b'install ubwc_config /bin/true')
        m.validate_policy(display,digest)
        with self.assertRaises(ValueError):m.validate_policy(display.replace(b'install snd_soc_aw88461 /bin/false',b'install snd_soc_aw88461 /bin/true'),digest)

    def test_phone_ucm_uses_qualified_mi2s_and_restores_mute(self):
        text=(R/'devices/oneplus13/audio/HiFi.conf').read_text()
        self.assertIn("name='Secondary MI2S Audio Mixer MultiMedia1' 1,1",text)
        self.assertIn("name='Secondary MI2S Audio Mixer MultiMedia1' 0,0",text)
        for channel in (0,1):
            self.assertIn("name='Amp"+str(channel)+" Playback Volume' 1023",text)
            self.assertIn("name='Amp"+str(channel)+" Playback Volume' 0",text)
        self.assertNotIn('Secondary TDM',text)

    def test_desktop_volume_is_soft_and_never_amplifies_above_unity(self):
        text=(R/'devices/oneplus13/audio/95-oneplus13-desktop-audio.conf').read_text()
        self.assertIn('api.alsa.soft-mixer = true',text)
        self.assertIn('channelmix.max-volume = 1.0',text)
        self.assertIn('audio.format = "S16LE"',text)
        self.assertIn('OnePlus13-Audio-Probe',text)
        self.assertIn('device.disabled = true',(R/'devices/oneplus13/audio/90-oneplus13-manual-audio.conf').read_text())

if __name__=='__main__':unittest.main()
