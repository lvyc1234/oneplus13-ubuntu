# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the installed capture lifecycle without phone access."""
import ast
import contextlib
import hashlib
import importlib
import io
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/oneplus13'))
capture = importlib.import_module('ram-camera-capture')


class SinglePrimaryTests(unittest.TestCase):
    def test_profile_parsing_is_fixed_and_requires_policy_binding(self):
        base = ['capture', '--release', 'fixture', '--profile', 'single-primary',
                '--policy-sha256', '0' * 64]
        with mock.patch.object(sys, 'argv', base):
            args = capture.parse_args()
        self.assertEqual((args.frame_timeout, args.sensor_test_pattern), (25, -1))
        for changed in (base[:-2], base + ['--frame-timeout', '24'],
                        base + ['--sensor-test-pattern', '0']):
            with mock.patch.object(sys, 'argv', changed), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    capture.parse_args()

    def test_exact_dependency_policy_and_unowned_modules_required(self):
        policy = b'install s5kjn5 /bin/false\n'
        expected = hashlib.sha256(policy).hexdigest()
        with mock.patch.object(capture, 'module_loaded', return_value=False), \
             mock.patch.object(capture.Path, 'exists', return_value=False):
            capture.validate_single_primary(list(capture.SINGLE_MODULES), policy, expected)
            for ordered in (capture.SINGLE_MODULES[:-1], capture.SINGLE_MODULES + ('extra',),
                            tuple(reversed(capture.SINGLE_MODULES))):
                with self.assertRaisesRegex(ValueError, 'dependency closure'):
                    capture.validate_single_primary(ordered, policy, expected)
            with self.assertRaisesRegex(ValueError, 'module policy'):
                capture.validate_single_primary(capture.SINGLE_MODULES, policy, '0' * 64)
        with mock.patch.object(capture.Path, 'exists', return_value=True):
            with self.assertRaisesRegex(ValueError, 'debug module'):
                capture.validate_single_primary(capture.SINGLE_MODULES, policy, expected)
        with mock.patch.object(capture.Path, 'exists', return_value=False), \
             mock.patch.object(capture, 'module_loaded', return_value=True):
            with self.assertRaisesRegex(ValueError, 'unowned camera'):
                capture.validate_single_primary(capture.SINGLE_MODULES, policy, expected)

    def test_late_debug_replacement_is_refused_before_insertion(self):
        with mock.patch.object(capture, 'PROFILE', 'single-primary'), \
             mock.patch.object(capture, 'module_loaded', return_value=False), \
             mock.patch.object(capture.Path, 'is_file', return_value=True), \
             mock.patch.object(capture, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'debug module'):
                capture.load_capture_module('s5kjn5', [])
        run.assert_not_called()

    def test_discovery_one_sweep_caps_each_read_and_common_deadline(self):
        with mock.patch.object(capture, 'PROFILE', 'single-primary'), \
             mock.patch.object(capture, 'media_nodes', return_value=['a', 'b']), \
             mock.patch.object(capture.time, 'monotonic', side_effect=[0, 0, 61]), \
             mock.patch.object(capture, 'run', return_value=SimpleNamespace(stdout='')) as run:
            with self.assertRaises(capture.CaptureDeadline):
                capture.find_media()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs['timeout'], 6)

    def test_default_configuration_timeout_is_eight_seconds(self):
        with mock.patch.object(capture, 'PROFILE', 'single-primary'), \
             mock.patch.object(capture, 'DEADLINE', None), \
             mock.patch.object(capture.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            capture.run(['media-ctl'])
        self.assertEqual(run.call_args.kwargs['timeout'], 8)

    def test_cleanup_failed_topology_read_cannot_claim_graph_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / 'policy'
            with mock.patch.object(capture, 'POLICY', policy), \
                 mock.patch.object(capture, 'PROFILE', 'single-primary'), \
                 mock.patch.object(capture, 'media_nodes', return_value=['a']), \
                 mock.patch.object(capture, 'run', return_value=SimpleNamespace(returncode=1, stdout='')) as run:
                report = capture.cleanup_capture([], b'original')
            self.assertFalse(report['cleanup_pass'])
            self.assertTrue(report['policy_restored'])
            self.assertFalse(report['media_graph_removed'])
            self.assertLessEqual(run.call_args.kwargs['timeout'], 6)

    def test_profile_has_distinct_work_cleanup_and_output_bounds(self):
        self.assertEqual((capture.SINGLE_WORK_SECONDS, capture.SINGLE_CLEANUP_SECONDS,
                          capture.SINGLE_OUTPUT_SECONDS), (660, 330, 30))
        with mock.patch.object(capture, 'PROFILE', 'single-primary'), \
             mock.patch.object(capture, 'DEADLINE', 1), \
             mock.patch.object(capture, 'CLEANUP_STARTED', False), \
             mock.patch.object(capture, 'arm_deadline') as arm:
            capture.begin_cleanup()
            capture.begin_cleanup()
        arm.assert_called_once_with(330)

    def test_failed_primary_lifecycle_attempts_once_and_always_cleans(self):
        self.lifecycle_case(frame_ok=False)

    def test_successful_primary_lifecycle_requires_real_frame_and_cleanup(self):
        self.lifecycle_case(frame_ok=True)

    def test_owned_trace_rejects_capture_before_stream_and_still_cleans(self):
        self.lifecycle_case(frame_ok=False, trace_owned=True)

    def test_reserved_probe_receipt_explicitly_rejects_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = 'p:kprobes/oneplus13_stream_enter s5kjn5_enable_streams\n'
            (root/'kprobe_events').write_text(original)
            with mock.patch.object(capture, 'Path', return_value=root):
                started = capture.stream_trace_start()
            self.assertIs(started['report']['cleanup_pass'], False)
            self.assertEqual((root/'kprobe_events').read_text(), original)

    def test_cleanup_deadline_preserves_failed_receipt_and_output(self):
        tree = ast.parse(Path(capture.__file__).read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        lifecycle = next(node for node in main.body if isinstance(node, ast.Try) and node.finalbody)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cleanup = mock.Mock(side_effect=capture.CaptureDeadline('expired'))
            env = dict(capture.__dict__, report={'frame_verified': False}, loaded=['owned'],
                original_policy=b'original', TRACE_OWNERS=[], STATE=root, DEADLINE=1,
                PROFILE='single-primary', begin_cleanup=mock.Mock(), progress=mock.Mock(),
                cleanup_capture=cleanup, arm_deadline=mock.Mock())
            with contextlib.redirect_stdout(io.StringIO()) as output:
                exec(compile(ast.fix_missing_locations(ast.Module(body=lifecycle.finalbody,
                     type_ignores=[])), '<actual-camera-cleanup>', 'exec'), env)
            self.assertFalse(env['report']['cleanup_pass'])
            self.assertTrue(env['report']['cleanup_deadline_expired'])
            self.assertTrue((root/'result.json').is_file())
            self.assertIn('CAMERA_CAPTURE_RESULT ', output.getvalue())
            env['arm_deadline'].assert_called_once_with(30)
            cleanup.assert_called_once_with(['owned'], b'original')

    def lifecycle_case(self, frame_ok, trace_owned=False):
        tree = ast.parse(Path(capture.__file__).read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        lifecycle = next(node for node in main.body if isinstance(node, ast.Try) and node.finalbody)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            policy, frame = root / 'policy', root / 'frame.raw'
            policy.write_bytes(b'original')
            if frame_ok:
                frame.write_bytes(bytes(range(256)) * (capture.MIN_BYTES // 256))
            process = mock.Mock(returncode=0 if frame_ok else 1)
            def communicate(**kwargs):
                if frame_ok:
                    frame.write_bytes(bytes(range(256)) * (capture.MIN_BYTES // 256))
                return ('', '' if frame_ok else 'fixture STREAMON failure')
            process.communicate.side_effect = communicate
            selected = "Width/Height : 4096/3072\nPixel Format : 'pGAA'\n"
            run = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout=selected, stderr=''))
            cleanup = mock.Mock(return_value={'cleanup_pass': True})
            env = dict(capture.__dict__, args=SimpleNamespace(profile='single-primary',
                frame_timeout=25, sensor_test_pattern=-1), ordered=[], loaded=[],
                original_policy=b'original', report={'frame_verified': False},
                POLICY=policy, FRAME=frame, STATE=root, DEADLINE=None, TRACE_OWNERS=[],
                progress=mock.Mock(), module_diagnostics=mock.Mock(return_value={}),
                load_capture_module=mock.Mock(), run=run,
                find_media=mock.Mock(return_value=('media', {}, 'sensor')),
                entity_subdev_node=mock.Mock(return_value='sensor-node'),
                entity_node=mock.Mock(return_value='video-node'),
                set_link=mock.Mock(), set_format=mock.Mock(),
                stream_trace_start=mock.Mock(return_value=(
                    {'state': None, 'report': {'cleanup_pass': False}} if trace_owned else None)),
                stream_trace_stop=mock.Mock(return_value={'cleanup_pass': True}),
                camss_probe_trace=mock.Mock(side_effect=AssertionError('bind retry forbidden')),
                begin_cleanup=mock.Mock(), cleanup_capture=cleanup)
            with mock.patch.object(capture.subprocess, 'Popen', return_value=process) as popen, \
                 mock.patch.object(capture.time, 'sleep'), \
                 mock.patch.object(capture.Path, 'read_text', return_value=''), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = compile(ast.fix_missing_locations(ast.Module(body=[lifecycle], type_ignores=[])),
                               '<actual-camera-lifecycle>', 'exec')
                if trace_owned:
                    with self.assertRaisesRegex(RuntimeError, 'trace ownership'):
                        exec(code, env)
                elif frame_ok:
                    exec(code, env)
                else:
                    with self.assertRaisesRegex(ValueError, 'V4L2 capture failed'):
                        exec(code, env)
            self.assertEqual(popen.call_count, 0 if trace_owned else 1)
            self.assertEqual(process.communicate.call_count, 0 if trace_owned else 1)
            env['find_media'].assert_called_once()
            env['camss_probe_trace'].assert_not_called()
            self.assertNotIn('rdi_fallbacks', env['report'])
            self.assertNotIn('tpg_fallback', env['report'])
            self.assertEqual(env['report']['frame_verified'], frame_ok)
            cleanup.assert_called_once_with([], b'original')
            if not trace_owned:
                self.assertEqual(len(env['report']['stream_attempts']), 1)


if __name__ == '__main__':
    unittest.main()
