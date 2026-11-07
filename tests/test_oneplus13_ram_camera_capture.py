# SPDX-License-Identifier: GPL-2.0-only
import importlib
import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/oneplus13"))
capture = importlib.import_module("ram-camera-capture")


TOPOLOGY = """
- entity 1: s5kjn5 1-002d (1 pad, 1 link)
  device node name /dev/v4l-subdev0
  pad0: Source
- entity 2: msm_csiphy1 (2 pads, 2 links)
  device node name /dev/v4l-subdev1
  pad0: Sink
  pad1: Source
- entity 3: msm_csid1 (2 pads, 2 links)
  device node name /dev/v4l-subdev2
  pad0: Sink
  pad1: Source
- entity 4: msm_vfe1_rdi0 (2 pads, 2 links)
  device node name /dev/v4l-subdev3
  pad0: Sink
  pad1: Source
- entity 5: msm_vfe1_video0 (1 pad, 1 link)
  device node name /dev/video7
  pad0: Sink
"""


class CameraCaptureRuntime(unittest.TestCase):
    def test_cleanup_alarm_still_attempts_policy_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp)/'policy'
            policy.write_bytes(b'temporary')
            with mock.patch.object(capture, 'POLICY', policy), \
                 mock.patch.object(capture, 'unload', side_effect=capture.CaptureDeadline):
                with self.assertRaises(capture.CaptureDeadline):
                    capture.cleanup_capture(['owned'], b'original')
            self.assertEqual(policy.read_bytes(), b'original')

    def test_cleanup_deadline_cannot_be_renewed(self):
        with mock.patch.object(capture, 'DEADLINE', 1), \
             mock.patch.object(capture, 'CLEANUP_STARTED', False), \
             mock.patch.object(capture, 'arm_deadline') as arm:
            capture.begin_cleanup()
            capture.begin_cleanup()
            arm.assert_called_once_with(capture.CLEANUP_SECONDS)

    def test_cleanup_checkpoint_failure_still_runs_hardware_teardown(self):
        tree = ast.parse(Path(capture.__file__).read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        lifecycle = next(node for node in main.body if isinstance(node, ast.Try) and node.finalbody)
        cleanup = mock.Mock(return_value={'cleanup_pass': True})
        class StopOutput(Exception):
            pass
        env = dict(capture.__dict__, DEADLINE=None, TRACE_OWNERS=[], report={}, loaded=[],
                   original_policy=b'original', cleanup_capture=cleanup,
                   progress=mock.Mock(side_effect=OSError('checkpoint failed')),
                   STATE=mock.Mock())
        # Stop after teardown; no paths, processes or phone transports are used.
        env['STATE'] = mock.MagicMock()
        env['STATE'].__truediv__.side_effect = StopOutput
        module = ast.Module(body=lifecycle.finalbody, type_ignores=[])
        with self.assertRaises(StopOutput):
            exec(compile(ast.fix_missing_locations(module), '<cleanup-only>', 'exec'), env)
        cleanup.assert_called_once_with([], b'original')
        self.assertEqual(env['report']['cleanup_checkpoint_error'], 'OSError')

    def test_signal_during_trace_setup_restores_registered_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp)
            for name, value in {'available_filter_functions': 'csid_isr\n',
                'set_ftrace_filter': '', 'current_tracer': 'nop\n',
                'tracing_on': '0\n', 'trace': ''}.items():
                (trace/name).write_text(value)
            original = Path.write_text
            fired = []
            def interrupted(path, value, *args, **kwargs):
                result = original(path, value, *args, **kwargs)
                if path == trace/'current_tracer' and value == 'function\n' and not fired:
                    fired.append(True)
                    raise capture.CaptureDeadline()
                return result
            with mock.patch.object(capture, 'TRACE_OWNERS', []), \
                 mock.patch.object(capture, 'Path', return_value=trace), \
                 mock.patch.object(Path, 'write_text', interrupted):
                with self.assertRaises(capture.CaptureDeadline):
                    capture.stream_trace_start()
                self.assertEqual(len(capture.TRACE_OWNERS), 1)
            self.assertEqual((trace/'current_tracer').read_text(), 'nop\n')
            self.assertEqual((trace/'set_ftrace_filter').read_text(), '')
            self.assertEqual((trace/'tracing_on').read_text(), '0\n')

    def test_common_deadline_clips_all_subprocess_work(self):
        with mock.patch.object(capture, "DEADLINE", 12), \
             mock.patch.object(capture.time, "monotonic", return_value=10), \
             mock.patch.object(capture.subprocess, "run") as run:
            run.return_value.returncode = 0
            capture.run(["fixture"], timeout=20)
            self.assertEqual(run.call_args.kwargs["timeout"], 2)
        with mock.patch.object(capture, "DEADLINE", 10), \
             mock.patch.object(capture.time, "monotonic", return_value=10), \
             mock.patch.object(capture.subprocess, "run") as run:
            with self.assertRaises(capture.CaptureDeadline):
                capture.run(["fixture"])
            run.assert_not_called()

    def test_fallback_exception_handlers_cannot_swallow_deadline(self):
        self.assertFalse(issubclass(capture.CaptureDeadline, Exception))

    def test_media_scan_rejects_excess_nodes_before_commands(self):
        dev = mock.Mock()
        dev.glob.return_value = [Path('/dev/media' + str(i)) for i in range(9)]
        with mock.patch.object(capture, "Path", return_value=dev), \
             mock.patch.object(capture, "run") as run:
            with self.assertRaisesRegex(ValueError, "node limit"):
                capture.find_media()
            run.assert_not_called()

    def test_stream_termination_is_bounded_and_failure_propagates(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.communicate.side_effect = capture.subprocess.TimeoutExpired('fixture', 5)
        with mock.patch.object(capture, "DEADLINE", None):
            with self.assertRaises(capture.subprocess.TimeoutExpired):
                capture.reap_stream(process)
        process.kill.assert_called_once()
        process.communicate.assert_called_once_with(timeout=5)

    def test_signal_wrapper_restores_handlers_after_failed_work(self):
        with mock.patch.object(capture.signal, 'SIGALRM', 14, create=True), \
             mock.patch.object(capture.signal, 'ITIMER_REAL', 0, create=True), \
             mock.patch.object(capture.signal, 'signal', return_value=0) as handler, \
             mock.patch.object(capture.signal, 'setitimer', create=True) as alarm, \
             mock.patch.object(capture, 'parse_args', return_value=mock.Mock(profile='full-diagnostics')), \
             mock.patch.object(capture, 'main', side_effect=capture.CaptureDeadline):
            with self.assertRaises(capture.CaptureDeadline):
                capture.bounded_main()
        self.assertIsNone(capture.DEADLINE)
        self.assertEqual(alarm.call_args.args, (0, 0))
        self.assertEqual(handler.call_count, 4)

    def test_module_timeout_keeps_attempt_owned_for_cleanup(self):
        attempted = []
        with mock.patch.object(capture, "module_loaded", return_value=False), \
             mock.patch.object(capture, "run", side_effect=
                 capture.subprocess.TimeoutExpired("modprobe", 20)):
            with self.assertRaises(capture.subprocess.TimeoutExpired):
                capture.load_capture_module("i2c_qcom_cci", attempted)
        self.assertEqual(attempted, ["i2c_qcom_cci"])

    def test_preexisting_module_is_not_owned_or_reinserted(self):
        attempted = []
        with mock.patch.object(capture, "module_loaded", return_value=True), \
             mock.patch.object(capture, "run") as run:
            capture.load_capture_module("qcom_camss", attempted)
        self.assertEqual(attempted, [])
        run.assert_not_called()

    def test_debug_insertion_failure_keeps_attempt_owned(self):
        attempted = []
        debug = mock.Mock()
        debug.is_file.return_value = True
        with mock.patch.object(capture, "module_loaded", return_value=False), \
             mock.patch.object(capture, "DEBUG_SENSOR_MODULE", debug), \
             mock.patch.object(capture, "run", side_effect=RuntimeError("insert failed")):
            with self.assertRaisesRegex(RuntimeError, "insert failed"):
                capture.load_capture_module("s5kjn5", attempted)
        self.assertEqual(attempted, ["s5kjn5"])

    def test_full_size_frame_from_failed_stream_cannot_qualify(self):
        frame = mock.Mock()
        frame.is_file.return_value = True
        frame.stat.return_value.st_size = capture.MIN_BYTES
        self.assertTrue(capture.stream_frame_ready(frame, {"returncode": 0, "stderr": ""}))
        for result in ({"returncode": 1},
                       {"returncode": 0, "timed_out": True},
                       {"returncode": 0, "stderr": "VIDIOC_STREAMON returned -1"}):
            with self.subTest(result=result):
                self.assertFalse(capture.stream_frame_ready(frame, result))

    def test_short_or_absent_frame_cannot_qualify_successful_exit(self):
        frame = mock.Mock()
        frame.is_file.return_value = False
        self.assertFalse(capture.stream_frame_ready(frame, {"returncode": 0}))
        frame.is_file.return_value = True
        frame.stat.return_value.st_size = capture.MIN_BYTES - 1
        self.assertFalse(capture.stream_frame_ready(frame, {"returncode": 0}))

    def test_unload_timeout_still_restores_policy_and_unloads_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / "policy"
            policy.write_bytes(b"temporary policy")
            with mock.patch.object(capture, "POLICY", policy), \
                 mock.patch.object(capture, "unload", side_effect=[
                     capture.subprocess.TimeoutExpired("modprobe", 15), None]) as unload, \
                 mock.patch.object(capture, "module_loaded", return_value=False):
                report = capture.cleanup_capture(["first", "second"], b"original policy")
            self.assertEqual(unload.call_args_list, [mock.call("second"), mock.call("first")])
            self.assertEqual(policy.read_bytes(), b"original policy")
            self.assertTrue(report["policy_restored"])
            self.assertFalse(report["cleanup_pass"])

    def test_media_cleanup_timeout_emits_failed_cleanup_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "policy"
            policy.write_bytes(b"original")
            dev = mock.Mock()
            dev.glob.return_value = ["/dev/media0"]
            with mock.patch.object(capture, "POLICY", policy), \
                 mock.patch.object(capture, "Path", return_value=dev), \
                 mock.patch.object(capture, "run", side_effect=capture.subprocess.TimeoutExpired("media-ctl", 20)):
                report = capture.cleanup_capture([], b"original")
            self.assertFalse(report["media_graph_removed"])
            self.assertFalse(report["cleanup_pass"])
            self.assertIn("media:TimeoutExpired", report["cleanup_errors"])

    def test_camss_retry_enables_and_selectively_removes_its_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "tracing"
            trace.mkdir()
            original = "p:other/unrelated other_symbol\n"
            (trace / "kprobe_events").write_text(original)
            (trace / "tracing_on").write_text("0\n")
            (trace / "trace").write_text("")
            driver = root / "driver"
            driver.mkdir()
            (driver / "bind").write_text("")
            device = root / "device"
            device.mkdir()
            names = ("camss_probe", "camss_icc_get", "camss_configure_pd",
                     "camss_init_subdevices", "dma_set_mask_and_coherent",
                     "camss_parse_ports", "camss_register_entities", "camss_link_entities")
            for name in names:
                event = trace / "events/kprobes" / ("oneplus13_" + name)
                event.mkdir(parents=True)
                (event / "enable").write_text("0\n")
            def path(value):
                if value == "/sys/kernel/debug/tracing": return trace
                if value == "/sys/bus/platform/drivers/qcom-camss": return driver
                return device / "driver" if str(value).endswith("/driver") else device
            def sleeping(_):
                self.assertTrue(all((trace / "events/kprobes" /
                    ("oneplus13_" + name) / "enable").read_text() == "1\n" for name in names))
            with mock.patch.object(capture, "Path", side_effect=path), \
                 mock.patch.object(capture.time, "sleep", side_effect=sleeping):
                report = capture.camss_probe_trace()
            self.assertTrue(report["cleanup_pass"])
            self.assertTrue((trace / "kprobe_events").read_text().startswith(original))
            self.assertNotIn("-:other/unrelated", (trace / "kprobe_events").read_text())
            for name in names:
                self.assertIn("-:kprobes/oneplus13_" + name, (trace / "kprobe_events").read_text())

    def test_function_cleanup_attempts_all_restorations_after_failure(self):
        trace, tracer, selector = mock.MagicMock(), mock.Mock(), mock.Mock()
        tracer.write_text.side_effect = OSError("restore denied")
        errors = capture.restore_function_trace(dict(trace=trace, tracer=tracer,
            selector=selector, previous="nop"))
        self.assertEqual(errors, ["restore denied"])
        selector.write_text.assert_called_once_with("")
        (trace / "tracing_on").write_text.assert_called_once_with("0\n")

    def test_function_setup_failure_rolls_back_and_reports_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp)
            for name, value in {"available_filter_functions": "csid_isr\n",
                "set_ftrace_filter": "", "current_tracer": "nop\n"}.items():
                (trace / name).write_text(value)
            (trace / "trace").mkdir()
            with mock.patch.object(capture, "Path", return_value=trace):
                report = capture.stream_trace_stop(capture.stream_trace_start())
            self.assertTrue(report["cleanup_pass"])
            self.assertEqual((trace / "set_ftrace_filter").read_text(), "")
            self.assertEqual((trace / "tracing_on").read_text(), "0\n")

    def test_owned_function_tracer_is_refused_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp)
            for name, value in {"available_filter_functions": "csid_isr\n",
                "set_ftrace_filter": "somebody_else\n", "current_tracer": "nop\n"}.items():
                (trace / name).write_text(value)
            with mock.patch.object(capture, "Path", return_value=trace):
                report = capture.stream_trace_stop(capture.stream_trace_start())
            self.assertFalse(report["cleanup_pass"])
            self.assertEqual((trace / "set_ftrace_filter").read_text(), "somebody_else\n")

    def test_rdi_sensor_frame_qualifies_after_primary_failure(self):
        report = {"rdi_fallbacks": [{"frame_verified": True}]}
        with mock.patch.object(capture, "validate_frame", return_value={"bytes": capture.MIN_BYTES}):
            capture.accept_sensor_frame(report, Path("sensor.raw"), {"returncode": 1, "stderr": ""})
        self.assertTrue(report["frame_verified"])

    def test_tpg_frame_never_qualifies_failed_sensor_stream(self):
        report = {"tpg_fallback": {"frame_verified": True}}
        with mock.patch.object(capture, "validate_frame") as validate:
            with self.assertRaisesRegex(ValueError, "V4L2 capture failed"):
                capture.accept_sensor_frame(report, Path("sensor.raw"), {"returncode": 1, "stderr": ""})
        validate.assert_not_called()

    def test_kprobe_trace_start_stop_keeps_mode_and_returns_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp)
            (trace / "kprobe_events").write_text("")
            (trace / "tracing_on").write_text("0\n")
            (trace / "trace").write_text("")
            for suffix in ("stream_enter", "stream_exit", "csid_irq",
                           "vfe_irq", "vfe_buf_done", "cphy_irq"):
                event = trace / "events/kprobes" / ("oneplus13_" + suffix)
                event.mkdir(parents=True)
                (event / "enable").write_text("0\n")
            with mock.patch.object(capture, "Path", return_value=trace):
                started = capture.stream_trace_start()
            for name in started["state"]["created"]:
                self.assertEqual(
                    (trace / "events/kprobes" / name / "enable").read_text(), "1\n"
                )
            (trace / "trace").write_text(
                "worker-1 [000] 1.0: oneplus13_stream_enter: (sensor)\n"
                "worker-1 [000] 1.1: oneplus13_stream_exit: (sensor) ret=-110\n"
            )
            report = capture.stream_trace_stop(started)
            self.assertEqual(report["mode"], "kprobe")
            self.assertEqual(report["counts"], {"stream_enter": 1, "stream_exit": 1})
            self.assertEqual((trace / "tracing_on").read_text(), "0\n")
            self.assertTrue(report["cleanup_pass"])
            for name in started["state"]["created"]:
                self.assertEqual(
                    (trace / "events/kprobes" / name / "enable").read_text(), "0\n"
                )
                self.assertIn("-:kprobes/" + name + "\n",
                              (trace / "kprobe_events").read_text())

    def test_existing_camera_probe_is_not_taken_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp)
            original = "p:kprobes/oneplus13_stream_enter sensor\n"
            (trace / "kprobe_events").write_text(original)
            with mock.patch.object(capture, "Path", return_value=trace):
                started = capture.stream_trace_start()
            self.assertIsNone(started["state"])
            self.assertIn("already exist", started["report"]["error"])
            self.assertEqual((trace / "kprobe_events").read_text(), original)

    def test_probe_creation_appends_and_partial_enable_failure_is_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp)
            original = "p:other_group/unrelated another_symbol\n"
            (trace / "kprobe_events").write_text(original)
            (trace / "tracing_on").write_text("0\n")
            with mock.patch.object(capture, "Path", return_value=trace):
                started = capture.stream_trace_start()
            self.assertIsNone(started["state"])
            report = capture.stream_trace_stop(started)
            self.assertIn("FileNotFoundError", report["error"])
            self.assertEqual(report["created"], ["oneplus13_stream_enter"])
            self.assertTrue((trace / "kprobe_events").read_text().startswith(original))
            self.assertIn("-:kprobes/oneplus13_stream_enter\n",
                          (trace / "kprobe_events").read_text())
            self.assertNotIn("-:other_group/unrelated",
                             (trace / "kprobe_events").read_text())

    def test_probe_cleanup_keeps_trying_after_disable_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp)
            events = trace / "kprobe_events"
            events.write_text("")
            errors = capture.remove_stream_probes(trace, events, ["oneplus13_stream_enter"])
            self.assertTrue(errors)
            self.assertEqual(events.read_text(), "-:kprobes/oneplus13_stream_enter\n")

    def test_trace_read_error_still_disables_and_deletes_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp)
            event = trace / "events/kprobes/oneplus13_stream_enter/enable"
            event.parent.mkdir(parents=True)
            event.write_text("1\n")
            events = trace / "kprobe_events"
            events.write_text("")
            state = {"mode": "kprobe", "trace": trace, "events": events,
                     "created": ["oneplus13_stream_enter"]}
            report = capture.stream_trace_stop(
                {"state": state, "report": {"mode": "kprobe"}}
            )
            self.assertIn("FileNotFoundError", report["error"])
            self.assertTrue(report["cleanup_pass"])
            self.assertEqual(event.read_text(), "0\n")

    def test_function_trace_fallback_still_collects_and_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp)
            for name, text in {"available_filter_functions": "csid_isr\nother\n",
                               "set_ftrace_filter": "", "current_tracer": "nop\n",
                               "trace": "", "tracing_on": "0\n"}.items():
                (trace / name).write_text(text)
            with mock.patch.object(capture, "Path", return_value=trace):
                started = capture.stream_trace_start()
            (trace / "trace").write_text("worker-1 [000] 1.0: csid_isr\n")
            report = capture.stream_trace_stop(started)
            self.assertEqual(report["counts"], {"csid_isr": 1})
            self.assertEqual((trace / "current_tracer").read_text(), "nop\n")
            self.assertEqual((trace / "tracing_on").read_text(), "0\n")

    def test_media_parser_selects_video_node(self):
        entities = capture.parse_media(TOPOLOGY)
        self.assertEqual(set(entities), {
            "s5kjn5 1-002d",
            "msm_csiphy1",
            "msm_csid1",
            "msm_vfe1_rdi0",
            "msm_vfe1_video0",
        })
        self.assertEqual(capture.entity_node(entities, "msm_vfe1_video0"),
                         "/dev/video7")
        self.assertEqual(entities["msm_csiphy1"]["pads"], {0, 1})
        self.assertEqual(
            capture.entity_subdev_node(entities, "s5kjn5 1-002d"),
            "/dev/v4l-subdev0",
        )

    def test_vfe_subdevice_is_not_the_capture_device(self):
        entities = capture.parse_media(TOPOLOGY)
        with self.assertRaises(ValueError):
            capture.entity_node(entities, "msm_vfe1_rdi0")

    def test_raw10_uses_packed_four_pixel_five_byte_layout(self):
        self.assertEqual(capture.MIN_BYTES, 15728640)

    def test_frame_validation_rejects_uniform_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.raw"
            path.write_bytes(b"\0" * capture.MIN_BYTES)
            with self.assertRaises(ValueError):
                capture.validate_frame(path)

    def test_frame_validation_accepts_varied_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.raw"
            path.write_bytes(bytes(range(256)) * (capture.MIN_BYTES // 256))
            report = capture.validate_frame(path)
            self.assertEqual(report["bytes"], capture.MIN_BYTES)
            self.assertGreaterEqual(report["sampled_unique_values"], 8)

    def test_camera_policy_removes_only_reviewed_dependency_denials(self):
        text = "\n".join(
            (
                "# policy",
                "blacklist mc",
                "install mc /bin/false",
                "blacklist unrelated",
                "install unrelated /bin/false",
            )
        ) + "\n"
        changed = capture.policy_without_camera_denials(text, ["mc"])
        self.assertNotIn("blacklist mc\n", changed)
        self.assertNotIn("install mc /bin/false\n", changed)
        self.assertIn("blacklist unrelated\n", changed)
        self.assertIn("install unrelated /bin/false\n", changed)

    def test_camera_policy_rejects_oversized_or_unsafe_closure(self):
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            capture.policy_without_camera_denials(
                "", ["safe", "../unsafe"]
            )
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            capture.policy_without_camera_denials(
                "", ["module" + str(index) for index in range(65)]
            )


if __name__ == "__main__":
    unittest.main()
