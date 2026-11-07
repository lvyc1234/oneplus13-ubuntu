"""A partial first-user startup must still verify amplifier shutdown."""
import importlib.util,shutil,tempfile,types,unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('first_user_audio',ROOT/'tools/oneplus13/first-user-audio.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class PartialStartupCleanup(unittest.TestCase):
    def exercise(self,powered):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);module=root/'module';module.mkdir();lib=root/'lib';lib.mkdir()
            shutil.copyfile(ROOT/'tools/oneplus13/ram-audio-check.py',lib/'oneplus13-ram-audio-check.py')
            i2c=root/'i2c';i2c.mkdir()
            for channel in [0,1]:
                p=i2c/str(channel);p.mkdir()
                (p/'phone_state').write_text(f'id=2308 channel={channel} volume=0 powered={powered} faulted=0 sysst=0000 sysctrl=0000 attenuation=1023\n')
            desktop=types.SimpleNamespace(RULE=root/'absent-rule',NODE=root/'absent-node',MODULE=root/'absent-owner')
            with mock.patch.object(m,'LIB',lib),mock.patch.object(m.time,'monotonic',side_effect=[0,10]):
                return m.stop(desktop,module_path=module,i2c_root=i2c)

    def test_no_desktop_route_does_not_hide_a_powered_amplifier(self):
        with self.assertRaises(ValueError):self.exercise(powered=1)

    def test_unpowered_native_amplifiers_are_verified_without_a_desktop_route(self):
        result=self.exercise(powered=0)
        self.assertTrue(result['cleanup_complete']);self.assertTrue(result['amplifiers_off'])
        self.assertFalse(result['desktop_route_was_enabled'])
