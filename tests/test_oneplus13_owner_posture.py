"""The optional owner window must stay bounded and release its D-Bus claim."""
import importlib.util,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('proxy',Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-ssc-proxy.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Posture(unittest.TestCase):
    def test_transitions_and_release_in_bounded_window(self):
        now=[0.];events=[]
        def properties():
            orientation='normal' if now[0]<4 else 'left-up' if now[0]<8 else 'right-up'
            return {'AccelerometerOrientation':orientation,'AccelerometerTilt':'vertical'}
        result=m.collect_posture(12,lambda:events.append('claim'),properties,lambda:events.append('release'),
            clock=lambda:now[0],sleep=lambda delay:now.__setitem__(0,now[0]+delay))
        self.assertEqual(events,['claim','release'])
        self.assertLessEqual(result['samples'],64)
        self.assertEqual([v['orientation'] for v in result['transitions']],['normal','left-up','right-up'])
        self.assertFalse(result['physical_axis_calibration_accepted'])

    def test_failed_property_read_releases_claim(self):
        events=[]
        def fail():raise RuntimeError('bus failed')
        with self.assertRaises(RuntimeError):
            m.collect_posture(12,lambda:events.append('claim'),fail,lambda:events.append('release'))
        self.assertEqual(events,['claim','release'])

    def test_invalid_duration_never_claims(self):
        for duration in (True,0,1,13,1200):
            with self.subTest(duration=duration),self.assertRaises(ValueError):
                m.collect_posture(duration,lambda:self.fail('claimed'),lambda:{},lambda:self.fail('released'))

    def test_unknown_posture_releases_claim(self):
        events=[]
        with self.assertRaises(ValueError):
            m.collect_posture(12,lambda:events.append('claim'),lambda:{'AccelerometerOrientation':'other','AccelerometerTilt':'vertical'},lambda:events.append('release'))
        self.assertEqual(events,['claim','release'])

if __name__=='__main__':unittest.main()
