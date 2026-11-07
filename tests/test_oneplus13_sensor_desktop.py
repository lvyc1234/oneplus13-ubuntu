# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the protected RAM sensor setup and desktop orientation gates."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'sensor_desktop', ROOT/'tools/oneplus13/ram-sensor-desktop.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


def digest(value):
    return hashlib.sha256(value).hexdigest()


class DesktopSensor(unittest.TestCase):
    def test_transitions_require_sensor_and_mutter_movement(self):
        now = [0.0]
        orientation = lambda: ('bottom-up' if now[0] < 7 else
                               'left-up' if now[0] < 14 else 'right-up')
        transforms = lambda: ((0,) if now[0] < 7 else
                              (3,) if now[0] < 14 else (1,))
        result = desktop.collect(
            20, orientation, transforms,
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay + .1))
        self.assertTrue(result['automatic_rotation_pass'])
        self.assertEqual(result['distinct_orientations'],
                         ['bottom-up', 'left-up', 'right-up'])
        self.assertEqual(result['distinct_display_transforms'], [0, 1, 3])
        self.assertFalse(result['physical_axes_calibration_accepted'])

    def test_static_pose_cannot_pass(self):
        now = [0.0]
        result = desktop.collect(
            20, lambda: 'normal', lambda: (0,),
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + 1))
        self.assertFalse(result['automatic_rotation_pass'])

    def test_only_bounded_twenty_or_forty_second_windows_are_reviewed(self):
        for seconds in (True, 0, 19, 21, 120):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                desktop.collect(seconds, lambda: 'normal', lambda: (0,))

    def test_forty_second_window_correlates_vertical_and_flat_poses(self):
        now=[0.0]
        poses=[('normal','vertical',(0,)),('left-up','vertical',(1,)),
               ('right-up','vertical',(3,)),('undefined','face-up',(0,)),
               ('undefined','face-down',(0,))]
        def pose():return poses[min(int(now[0]//8),4)]
        result=desktop.collect(40,lambda:pose()[0],lambda:pose()[2],
            clock=lambda:now[0],sleep=lambda delay:now.__setitem__(0,now[0]+delay),
            posture=lambda:pose()[:2])
        self.assertEqual(result['samples'],160)
        self.assertEqual(result['seconds'],40)
        self.assertEqual({r['tilt'] for r in result['posture_transform_sequence']},
                         {'vertical','face-up','face-down'})
        self.assertEqual(result['normal_portrait_transforms'],[0])
        self.assertFalse(result['physical_axes_calibration_accepted'])

    def test_static_right_up_requires_sensor_and_nonzero_transform(self):
        now = [0.0]
        result = desktop.collect_static_right_up(
            8, lambda: 'right-up', lambda: (3,),
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + .25))
        self.assertTrue(result['static_pose_pass'])
        self.assertFalse(result['automatic_rotation_pass'])
        self.assertEqual(result['orientations'], ['right-up'])
        self.assertEqual(result['distinct_display_transforms'], [3])

    def test_static_pose_with_normal_transform_fails(self):
        now = [0.0]
        result = desktop.collect_static_right_up(
            8, lambda: 'right-up', lambda: (0,),
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + .25))
        self.assertFalse(result['static_pose_pass'])

    def test_flat_pose_with_zero_transform_is_the_correct_static_state(self):
        now = [0.0]
        result = desktop.collect_static_right_up(
            8, lambda: 'undefined', lambda: (0,),
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + .25),
            posture=lambda: ('undefined', 'face-down'))
        self.assertFalse(result['automatic_rotation_pass'])
        self.assertTrue(result['static_pose_pass'])
        self.assertTrue(result['flat_zero_transform_pass'])
        self.assertEqual(result['tilts'], ['face-down'])

    def test_undefined_without_flat_tilt_still_fails(self):
        now = [0.0]
        result = desktop.collect_static_right_up(
            8, lambda: 'undefined', lambda: (0,),
            clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + .25),
            posture=lambda: ('undefined', 'vertical'))
        self.assertFalse(result['static_pose_pass'])

    def test_expected_left_up_requires_exact_transform(self):
        for orientation, transform, passed in [('left-up',1,True), ('left-up',3,False),
                                                ('right-up',3,False), ('undefined',0,False)]:
            now = [0.0]
            result = desktop.collect_static_right_up(
                8, lambda: orientation, lambda: (transform,),
                clock=lambda: now[0],
                sleep=lambda delay: now.__setitem__(0, now[0]+.25),
                posture=lambda: (orientation,'face-down'), expected_orientation='left-up')
            self.assertEqual(result['static_pose_pass'], passed)
            self.assertFalse(result['automatic_rotation_pass'])

    def test_mutter_transform_is_read_from_logical_monitor_tuple(self):
        physical=[('eDP-1','vendor','product','serial',[],{})]
        logical=[(0,0,1.0,3,True,[('eDP-1','vendor','product','serial')],{})]
        self.assertEqual(desktop.parse_display_transforms((7,physical,logical,{})),(3,))

    def test_orientation_uses_the_same_getall_shape_as_the_claim_client(self):
        class Value:
            @staticmethod
            def unpack():
                return ({'AccelerometerOrientation': 'right-up',
                         'AccelerometerTilt': 'vertical'},)

        class Bus:
            call = None

            @classmethod
            def call_sync(cls, *args):
                cls.call = args
                return Value()

        self.assertEqual(desktop.system_orientation(Bus), 'right-up')
        self.assertEqual(Bus.call[3], 'GetAll')
        self.assertEqual(desktop.system_posture(Bus), ('right-up', 'vertical'))

    def test_setup_installs_only_inside_protected_ram_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'run/oneplus13').mkdir(parents=True)
            (root/'run/oneplus13/ram-session').write_text('RAM\n')
            (root/'proc').mkdir()
            (root/'proc/mounts').write_text('tmpfs / overlay rw 0 0\n')
            archive = root/'runtime.tar.gz'; archive.write_bytes(b'archive')
            manifest_bytes = b'{"native_elf_count": 10, "text_data_files": 922}\n'
            manifest_path = root/'manifest.json'; manifest_path.write_bytes(manifest_bytes)
            bindings = root/'bindings.json'
            bindings.write_text(json.dumps({
                'format': 'ONEPLUS13_IMAGE_SENSOR_INPUTS_V1',
                'inputs': {
                    'archive': {'path': str(archive), 'sha256': digest(b'archive')},
                    'manifest': {'path': str(manifest_path), 'sha256': digest(manifest_bytes)},
                },
            }))
            entries = {
                'usr/lib/test': (b'ELF', 0o755, None),
                'var/lib/test': (b'data', 0o640, None),
                'usr/share/mapped': (None, 0o777, '/var/lib/test'),
            }

            class FakeFileserver:
                @staticmethod
                def archive_entries(path, manifest):
                    self.assertEqual(path, archive)
                    return entries

                @staticmethod
                def install(target, files, chown):
                    for name, (content, mode, link) in files.items():
                        path = target/name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        if link is not None:
                            path.symlink_to(link)
                        else:
                            path.write_bytes(content)
                            path.chmod(mode)

            marker = root/'run/oneplus13/sensor-runtime-ready.json'
            with patch.object(desktop, 'load', return_value=FakeFileserver):
                result = desktop.setup(root, bindings, marker, chown=lambda *args: None)
            self.assertEqual(result['scope'], 'ONEPLUS13_RAM_DESKTOP_SENSOR_RUNTIME_READY')
            self.assertFalse(result['attach_started'])
            self.assertEqual((root/'var/lib/test').read_bytes(), b'data')
            self.assertEqual(str((root/'usr/share/mapped').readlink()), '/var/lib/test')

    def test_service_configuration_stays_manual_and_restricted(self):
        service = ROOT/'devices/oneplus13/sensors'
        fileserver = (service/'oneplus13-sensor-fileserver.service').read_text()
        proxy = (service/'oneplus13-sensor-proxy.service').read_text()
        udev = (service/'10-oneplus13-fastrpc.rules').read_text()
        bringup = (ROOT/'devices/oneplus13/desktop/qualified-bringup').read_text()
        self.assertIn('User=65534', fileserver)
        self.assertIn('DevicePolicy=closed', fileserver)
        self.assertIn('/dev/fastrpc-adsp-secure rw', fileserver)
        self.assertNotIn('User=', proxy)
        self.assertIn('PrivateDevices=yes', proxy)
        self.assertIn('CapabilityBoundingSet=', proxy)
        self.assertNotIn('WantedBy=', fileserver+proxy)
        self.assertIn('ENV{IIO_SENSOR_PROXY_TYPE}="ssc-accel ssc-compass ssc-gyro"', udev)
        # Physical upright was observed as bottom-up with transform2. The SSC
        # already exports handset X/Z axes; invert only the pitch axis once
        # so upright maps to normal while left-up/right-up retain transforms1/3.
        self.assertIn('ENV{ACCEL_MOUNT_MATRIX}="1, 0, 0; 0, -1, 0; 0, 0, 1"', udev)
        self.assertIn('modprobe --ignore-install fastrpc', bringup)
        self.assertIn('orientation-lock false', bringup)


if __name__ == '__main__':
    unittest.main()
