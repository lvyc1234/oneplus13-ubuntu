# SPDX-License-Identifier: GPL-2.0-only
import importlib.util,struct,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-audio-check.py'
s=importlib.util.spec_from_file_location('ram_audio',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class AudioRuntime(unittest.TestCase):
    def test_only_exact_reviewed_display_policy_can_precede_audio(self):
        import hashlib
        original=b'install cec /bin/false\ninstall ubwc_config /bin/false\ninstall snd_soc_aw88461 /bin/false\ninstall qcom_iris /bin/false\n'
        digest=hashlib.sha256(original).hexdigest()
        m.validate_policy(original,digest)
        display=original.replace(b'install cec /bin/false',b'install cec /bin/true').replace(b'install ubwc_config /bin/false',b'install ubwc_config /bin/true')
        m.validate_policy(display,digest)
        bad=[display.replace(b'install snd_soc_aw88461 /bin/false',b'install snd_soc_aw88461 /bin/true'),
             display.replace(b'install qcom_iris /bin/false',b'install qcom_iris /bin/true'),
             display.replace(b'install cec /bin/true',b'install cec /bin/false'),
             display+b'install cec /bin/true\n',display+b'\n']
        for raw in bad:
            with self.subTest(policy=raw),self.assertRaises(ValueError):m.validate_policy(raw,digest)

    def test_bounded_nonzero_signal_has_stereo_ramps_and_quiet_peak(self):
        blob=m.quiet_tone();values=struct.unpack('<'+str(len(blob)//2)+'h',blob)
        self.assertEqual(len(blob),48000*2*2*2)
        self.assertEqual(values[::2],values[1::2])
        self.assertEqual(values[0],0);self.assertEqual(values[-1],0)
        self.assertLessEqual(max(abs(v) for v in values),4125)
        self.assertGreater(max(values),4000);self.assertLess(min(values),-4000)
        row='id=2308 channel=0 volume=256 powered=1 faulted=0 sysst=0311 sysctrl=2000 attenuation=767\n'
        self.assertTrue(m.active_state(m.amp_state(row,256)))
        for old,new in [('attenuation=767','attenuation=766'),('volume=256','volume=257')]:
            with self.assertRaises(ValueError):m.amp_state(row.replace(old,new),256)
    def test_real_state_parser_waits_for_bounded_dapm_stop(self):
        now=[0.0]
        def clock():return now[0]
        def sleep(seconds):now[0]+=seconds
        row='id=2308 channel=0 volume=0 powered=1 faulted=0 sysst=0211 sysctrl=2000 attenuation=1023\n'
        class Status:
            def __init__(self,off_at):self.off_at=off_at
            def read_text(self):return row.replace('powered=1','powered=0') if clock()>=self.off_at else row
        result=m.wait_off([Status(5)],clock,sleep)
        self.assertFalse(result[0]['powered']);self.assertGreaterEqual(clock(),5)
        now[0]=0
        with self.assertRaises(ValueError):m.wait_off([Status(10)],clock,sleep)
        self.assertLess(clock(),6.3)
    def test_dependency_order_and_missing_or_cyclic_inventory(self):
        text='kernel/a.ko: kernel/b.ko kernel/c.ko\nkernel/b.ko: kernel/c.ko\nkernel/c.ko:\n'
        self.assertEqual(m.module_order(text,['a']),['c','b','a'])
        for bad in [text.replace('kernel/c.ko:\n',''),text.replace('kernel/c.ko:\n','kernel/c.ko: kernel/a.ko\n'),
                    text.replace('kernel/b.ko','../b.ko')]:
            with self.assertRaises(ValueError):m.module_order(bad,['a'])

    def test_live_amp_state_requires_zero_volume_and_maximum_attenuation(self):
        row='id=2308 channel=0 volume=0 powered=1 faulted=0 sysst=0311 sysctrl=2000 attenuation=1023\n'
        self.assertTrue(m.active_state(m.amp_state(row)))
        self.assertTrue(m.active_state(m.amp_state(row.replace('sysst=0311','sysst=0211'))))
        for old,new in [('id=2308','id=2309'),('volume=0','volume=1'),
                        ('faulted=0','faulted=1'),('attenuation=1023','attenuation=1000')]:
            with self.assertRaises(ValueError):m.amp_state(row.replace(old,new))
        for old,new in [('powered=1','powered=0'),('sysst=0311','sysst=0000'),
                        ('sysst=0311','sysst=4311'),('sysctrl=2000','sysctrl=2003')]:
            self.assertFalse(m.active_state(m.amp_state(row.replace(old,new))))

if __name__=='__main__':unittest.main()
