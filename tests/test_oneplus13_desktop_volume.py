# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('volume',Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-desktop-volume-check.py')
volume=importlib.util.module_from_spec(spec);spec.loader.exec_module(volume)


class DesktopVolumeTests(unittest.TestCase):
    def measurements(self):
        return {name:{'amplitudes':[[x,x],[x/2,x/2]],'clipped_samples':0}
                for name,x in [('muted',1e-5),('20',1e-3),('50',.005),('100',.02)]}

    def test_real_response_must_increase_at_each_volume(self):
        self.assertTrue(volume.evaluate(self.measurements())['acoustic_system_volume_pass'])
        data=self.measurements();data['20']['amplitudes']=[[.04,.04],[.02,.02]]
        self.assertFalse(volume.evaluate(data)['acoustic_system_volume_pass'])

    def test_mute_and_clipping_are_independent_gates(self):
        data=self.measurements();data['muted']['amplitudes']=data['100']['amplitudes']
        self.assertFalse(volume.evaluate(data)['acoustic_system_volume_pass'])
        data=self.measurements();data['100']['clipped_samples']=1
        self.assertFalse(volume.evaluate(data)['acoustic_system_volume_pass'])

    def test_missing_capture_is_rejected(self):
        data=self.measurements();del data['50']
        with self.assertRaises(ValueError):volume.evaluate(data)

    def test_same_microphone_must_validate_both_tones(self):
        data=self.measurements()
        # Different channels responding to different tones cannot be cherry-picked.
        data['100']['amplitudes']=[[.02,.00001],[.00001,.02]]
        self.assertFalse(volume.evaluate(data)['acoustic_system_volume_pass'])


if __name__=='__main__':unittest.main()
