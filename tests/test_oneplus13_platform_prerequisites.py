# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the actual shell thermal gate on saved files; never call main."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'tools/oneplus13/ram-platform-prerequisites.sh'
class ThermalGateTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
    def zone(self,index,kind,value=None):
        path=self.root/f'class/thermal/thermal_zone{index}';path.mkdir(parents=True)
        (path/'type').write_text(kind)
        if value is not None:(path/'temp').write_text(str(value))
    def gate(self):
        return subprocess.run(['bash','-c','source "$1"; platform_thermal_gate "$2"','gate',str(SCRIPT),str(self.root)],capture_output=True).returncode
    def test_pending_battery_does_not_replace_cpu_gate(self):
        self.zone(0,'qcom-battmgr-bat');self.zone(1,'cpu-0-4-0-thermal',42000)
        self.assertEqual(self.gate(),0)
    def test_pending_cpu_temperature_refuses_activation(self):
        self.zone(0,'cpu-0-4-0-thermal');self.zone(1,'qcom-battmgr-bat',30000)
        self.assertNotEqual(self.gate(),0)
    def test_no_cpu_sensors_refuses_activation(self):
        self.zone(0,'qcom-battmgr-bat',30000)
        self.assertNotEqual(self.gate(),0)
    def test_hot_cpu_refuses_activation(self):
        self.zone(0,'cpuss-0-0-thermal',70000)
        self.assertNotEqual(self.gate(),0)
    def test_malformed_temperature_refuses_activation(self):
        self.zone(0,'aoss0-thermal','not-a-temperature')
        self.assertNotEqual(self.gate(),0)

    def test_warm_start_continues_after_temperature_recovers(self):
        self.zone(0, 'cpu-0-0-0-thermal', 70300)
        with subprocess.Popen(
            ['bash', '-c', 'source "$1"; platform_wait_thermal_gate "$2" 3',
             'thermal', str(SCRIPT), str(self.root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as process:
            self.assertIn('Waiting for thermal readings', process.stderr.readline())
            (self.root / 'class/thermal/thermal_zone0/temp').write_text('42000')
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)

    def test_wait_refuses_persistently_hot_sensor(self):
        self.zone(0, 'cpu-0-0-0-thermal', 70300)
        result = subprocess.run(
            ['bash', '-c', 'source "$1"; platform_wait_thermal_gate "$2" 1',
             'thermal', str(SCRIPT), str(self.root)], capture_output=True, timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)


class ScalingReadinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'bus/platform/devices/16430000.mailbox/driver').mkdir(parents=True)
        identity = self.root / 'devices/soc0/soc_id'
        identity.parent.mkdir(parents=True)
        identity.write_text('618')

    def policy(self, index, complete=True):
        policy = self.root / f'devices/system/cpu/cpufreq/policy{index}'
        policy.mkdir(parents=True)
        (policy / 'scaling_driver').write_text('scmi')
        (policy / 'scaling_min_freq').write_text('300000')
        if complete:
            (policy / 'scaling_max_freq').write_text('4000000')
        return policy

    def ready(self):
        return subprocess.run(
            ['bash', '-c', 'source "$1"; platform_scaling_ready "$2"',
             'ready', str(SCRIPT), str(self.root)], capture_output=True,
        ).returncode == 0

    def test_first_policy_does_not_complete_async_initialization(self):
        self.policy(0)
        self.assertFalse(self.ready())
        self.policy(6)
        self.assertTrue(self.ready())

    def test_partially_published_policy_becomes_ready(self):
        self.policy(0)
        policy = self.policy(6, complete=False)
        self.assertFalse(self.ready())
        (policy / 'scaling_max_freq').write_text('4000000')
        self.assertTrue(self.ready())

    def test_soc_identity_must_finish_initializing(self):
        self.policy(0)
        self.policy(6)
        identity = self.root / 'devices/soc0/soc_id'
        identity.unlink()
        self.assertFalse(self.ready())
        identity.write_text('618')
        self.assertTrue(self.ready())


if __name__=='__main__':unittest.main()
