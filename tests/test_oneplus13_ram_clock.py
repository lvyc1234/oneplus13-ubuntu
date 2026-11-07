"""Offline clock tests: no host or phone clock changes."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
spec=importlib.util.spec_from_file_location('bridge',Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-clock-bridge.py')
clock_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(clock_module)

class Clock(unittest.TestCase):

    def fake(self, before, after):
        return SimpleNamespace(time=Mock(side_effect=[before, after]), monotonic=Mock(side_effect=[10, 10.1]), CLOCK_REALTIME=0, clock_settime=Mock())

    def test_old_ram_time_changes_only_realtime_clock(self):
        clock = self.fake(1789567200, 1792944000)
        r = clock_module.bridge(1792944000, 'x / overlay rw 0 0', clock)
        clock.clock_settime.assert_called_once_with(clock.CLOCK_REALTIME, 1792944000)
        self.assertTrue(r['changed'])
        self.assertFalse(r['rtc_write_requested'])

    def test_current_time_no_write(self):
        clock = self.fake(1792943999, 1792944000)
        self.assertFalse(clock_module.bridge(1792944000, 'x / overlay rw 0 0', clock)['changed'])
        clock.clock_settime.assert_not_called()

    def test_non_ram_and_invalid_epoch_refused_before_settime(self):
        for epoch, mounts in [(0, 'x / overlay rw 0 0'), (True, 'x / overlay rw 0 0'), (1792944000, 'x / ext4 rw 0 0')]:
            clock = self.fake(0, 0)
            with self.assertRaises(ValueError):
                clock_module.bridge(epoch, mounts, clock)
            clock.clock_settime.assert_not_called()

    def test_failed_clock_change_refused(self):
        clock = self.fake(1789567200, 1789567200)
        with self.assertRaises(ValueError):
            clock_module.bridge(1792944000, 'x / overlay rw 0 0', clock)

if __name__=='__main__':unittest.main()
