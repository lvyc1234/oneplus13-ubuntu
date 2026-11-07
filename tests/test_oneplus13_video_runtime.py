# SPDX-License-Identifier: GPL-2.0-only
import copy, importlib.util, json, os, shutil, subprocess, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('video_check',ROOT/'tools/oneplus13/ram-video-check.py')
video=importlib.util.module_from_spec(spec);spec.loader.exec_module(video)


class VideoRuntime(unittest.TestCase):
    def test_video_requires_active_bounded_awake_guard_before_activation(self):
        receipt={'scope':'ONE_MANUAL_RAM_GUNYAH_AWAKE_VIDEO_GUARD',
                 'active_wait_required':True,'idle_or_suspend_recovery_qualified':False,
                 'guard_running':True,'guard_seconds':90,'guard_deadline_monotonic':190,
                 'armed':True,'countdown_verified':True,'keepalive_verified':True,
                 'suspend_requested':False,'reset_observed':False,
                 'set_timeout_readback':12,'get_timeout_readback':12,
                 'first_timeleft':12,'second_timeleft':10,'third_timeleft':12}
        video.validate_awake_guard(receipt,110)
        for key,value in [('scope','ONE_MANUAL_RAM_GUNYAH_AWAKE_RESET'),
                          ('active_wait_required',False),('guard_running',False),
                          ('guard_seconds',180),('guard_deadline_monotonic',150),
                          ('guard_deadline_monotonic',float('nan')),('guard_deadline_monotonic',float('inf')),
                          ('guard_deadline_monotonic',210),('first_timeleft',True),
                          ('countdown_verified',False),('third_timeleft',10)]:
            changed={**receipt,key:value}
            with self.subTest(key=key),self.assertRaises(ValueError):
                video.validate_awake_guard(changed,110)

    def test_public_fixture_is_exact_and_foreign_or_changed_streams_are_rejected(self):
        value=json.loads((ROOT/'devices/oneplus13/video/synthetic-avc-bars.json').read_text())
        self.assertEqual(len(video.fixture_bytes(value)),905)
        for key,new in [('capture_input',True),('frames',2),('width',640),
                        ('hex','00'*905),('sha256','a'*64)]:
            changed=copy.deepcopy(value);changed[key]=new
            with self.subTest(key=key),self.assertRaises(ValueError):video.fixture_bytes(changed)

    def test_device_or_open_success_cannot_substitute_for_verified_pixels(self):
        result={'frame_verified':True,'streams_stopped':True,'software_fallback':False,
                'frame_width':320,'frame_height':240}
        def text(value):return 'IRIS_FIRMWARE_SESSION_OPENED\nIRIS_FRAME_RESULT '+json.dumps(value)+'\n'
        self.assertEqual(video.parse_frame(text(result)),result)
        for key,new in [('frame_verified',False),('streams_stopped',False),
                        ('software_fallback',True),('frame_width',384)]:
            changed={**result,key:new}
            with self.subTest(key=key),self.assertRaises(ValueError):video.parse_frame(text(changed))
        for output in ('IRIS_FIRMWARE_SESSION_OPENED\n',text(result)+text(result),
                       text(result).replace('IRIS_FIRMWARE_SESSION_OPENED','')):
            with self.assertRaises(ValueError):video.parse_frame(output)

    @unittest.skipUnless(os.name=='posix' and shutil.which('gcc'),'Linux C selftests')
    def test_real_queue_allocator_driver_sizes_and_partial_cleanup(self):
        source=ROOT/'tools/oneplus13/video-draft/test-queue.c'
        with tempfile.TemporaryDirectory() as tmp:
            for sanitizer in (False,True):
                binary=Path(tmp)/('queue-sanitized' if sanitizer else 'queue')
                flags=['-fsanitize=address,undefined'] if sanitizer else []
                subprocess.run(['gcc','-std=c11','-O1','-Wall','-Wextra','-Werror',
                                *flags,str(source),'-o',str(binary)],check=True,capture_output=True)
                result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.stdout.strip(),'VIDEO_QUEUE_ALLOCATOR_SELFTEST_PASS_NO_DEVICE_OPEN')

    @unittest.skipUnless(os.name=='posix' and shutil.which('gcc'),'Linux C selftests')
    def test_native_pixel_bounds_and_corruption_checks_under_sanitizers(self):
        source=ROOT/'tools/oneplus13/video-draft/decode-one-frame.c'
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'validator'
            subprocess.run(['gcc','-std=c11','-O1','-Wall','-Wextra','-Werror',
                            '-fsanitize=address,undefined',str(source),'-o',str(binary)],check=True,capture_output=True)
            run=subprocess.run([str(binary),'--selftest'],check=True,capture_output=True,text=True,timeout=10)
            self.assertEqual(run.stdout.strip(),'VIDEO_PIXEL_VALIDATOR_SELFTEST_PASS_NO_DEVICE_OPEN')
            # The hardware path has no unguarded fallback and rejects before opening a device.
            environment={k:v for k,v in os.environ.items() if k!='ONEPLUS13_GUARDED_VIDEO_TEST'}
            run=subprocess.run([str(binary),'--decode','/dev/nonexistent','/dev/nonexistent'],
                               capture_output=True,env=environment,timeout=10)
            self.assertEqual(run.returncode,2)


if __name__=='__main__':unittest.main()
