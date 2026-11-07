# SPDX-License-Identifier: GPL-2.0-only
"""Run the actual WiFi/sensor shell boundary with inert command substitutes."""
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRINGUP = ROOT / 'devices/oneplus13/desktop/qualified-bringup'


class WifiBringupTests(unittest.TestCase):
    def run_boundary(self, mode):
        source = BRINGUP.read_text()
        begin = source.index('prepare_desktop_sensors() {')
        end = source.index('    prepare_desktop_sensors\n') + len('    prepare_desktop_sensors\n')
        body = source[begin:end].replace('/usr/sbin/iw', 'inert_iw') + 'fi\n'
        substitutes = '''set -eu
timeout() { printf 'INERT_TIMEOUT\n'; }
seq() { printf '1\n'; }
sleep() { :; }
modprobe() { printf 'SENSOR_LOAD_REFUSED\n'; return 89; }
inert_iw() { printf 'Interface offline-test\n'; }
'''
        return subprocess.run(['bash', '-c', substitutes + 'mode=$1\n' + body,
                               'bringup-boundary', mode], capture_output=True, text=True)

    def test_wifi_does_not_attach_or_wait_for_failed_sensor(self):
        result = self.run_boundary('--wifi')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('QUALIFIED_WIFI_DRIVER_READY', result.stdout)
        self.assertNotIn('SENSOR_LOAD_REFUSED', result.stdout)

    def test_all_retains_sensor_gate_after_wifi_is_ready(self):
        result = self.run_boundary('--all')
        self.assertEqual(result.returncode, 89, result.stderr)
        self.assertLess(result.stdout.index('QUALIFIED_WIFI_DRIVER_READY'),
                        result.stdout.index('SENSOR_LOAD_REFUSED'))

    def test_adsp_start_stays_before_the_sensor_function_and_wlan(self):
        source = BRINGUP.read_text()
        self.assertLess(source.index('test "$(cat "$ADSP/state")" = running'),
                        source.index('prepare_desktop_sensors() {'))
        subprocess.run(['bash', '-n', str(BRINGUP)], check=True)


if __name__ == '__main__':
    unittest.main()
