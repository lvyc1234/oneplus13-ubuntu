#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).resolve().parents[1] / "tools/oneplus13/ram-gnss-check.py"
SPEC = importlib.util.spec_from_file_location("ram_gnss", PATH)
GNSS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GNSS)


def sentence(payload):
    checksum = 0
    for char in payload:
        checksum ^= ord(char)
    return "$" + payload + f"*{checksum:02X}"


class GnssNmeaTests(unittest.TestCase):
    def test_failed_start_with_acknowledged_stop_is_clean_not_qualified(self):
        with patch.object(GNSS, "run", return_value={
            "returncode": 1, "stdout": "LOCATION_SESSION_STOPPED nmea=0\n",
            "stderr": "LOCATION_SESSION_ERROR operation=start-result error=QMI protocol error (46): 'GeneralError'\n",
        }):
            result = GNSS.collect_native_session("/native", 25)
        self.assertTrue(result["cleanup_complete"])
        self.assertTrue(result["stopped"])
        self.assertFalse(result["started"])
        self.assertFalse(result["no_session_started"])
        self.assertFalse(result["engine_control_qualified"])
        self.assertFalse(result["fix_acquired"])

    def test_stop_marker_cannot_hide_timeout_crash_or_inconsistent_exit(self):
        cases = [
            (1, True, ""), (-9, False, ""), (124, False, ""),
            (1, False, "LOCATION_SESSION_STARTED seconds=25\n"),
        ]
        for code, timed_out, extra in cases:
            with self.subTest(code=code, timed_out=timed_out, extra=extra), patch.object(
                GNSS, "run", return_value={"returncode": code, "timed_out": timed_out,
                    "stdout": extra + "LOCATION_SESSION_STOPPED nmea=0\n"}
            ):
                result = GNSS.collect_native_session("/native", 25)
                self.assertFalse(result["cleanup_complete"])
                self.assertFalse(result["engine_control_qualified"])

    def test_failed_or_malformed_stop_is_not_cleanup_acknowledgement(self):
        for output in ("", "LOCATION_SESSION_STOPPED_ERROR\n",
                       "LOCATION_SESSION_STOPPED nmea=garbage\n"):
            with self.subTest(output=output), patch.object(GNSS, "run", return_value={
                "returncode": 1, "stdout": output,
                "stderr": "LOCATION_SESSION_ERROR operation=stop-result error=GeneralError\n",
            }):
                self.assertFalse(GNSS.collect_native_session("/native", 25)["cleanup_complete"])

    def test_failed_start_stop_requires_unambiguous_native_protocol_result(self):
        error = "LOCATION_SESSION_ERROR operation=start-result error=QMI protocol error (46): 'GeneralError'\n"
        marker = "LOCATION_SESSION_STOPPED nmea=0\n"
        for stdout, stderr in ((marker, ""), (marker, "unknown failure\n"),
                (marker, error.replace("(46): 'GeneralError'", "(71): 'Timeout'")),
                (marker, error.replace("(46): 'GeneralError'", "(4): 'Aborted'")),
                (marker, error + "LOCATION_SESSION_ERROR operation=stop-result error=bad\n"),
                (marker + marker, error),
                (marker + "LOCATION_SESSION_NO_SESSION start_requested=0\n", error)):
            with self.subTest(stdout=stdout, stderr=stderr), patch.object(GNSS, "run", return_value={
                "returncode": 1, "stdout": stdout, "stderr": stderr,
            }):
                self.assertFalse(GNSS.collect_native_session("/native", 25)["cleanup_complete"])

    def test_selected_endpoint_snapshot_is_prioritized_with_bounded_inventory(self):
        lines = [f"LOCATION_SESSION_LOC_SERVICE node={i} port=42 version=2 instance=0 selected_snapshot=0"
                 for i in range(20)]
        selected = "LOCATION_SESSION_LOC_SERVICE node=0 port=90 version=3 instance=0 selected_snapshot=1"
        result = GNSS.loc_service_lines(lines + [selected])
        self.assertEqual(len(result), 16)
        self.assertEqual(result[0], selected)

    def test_loc_endpoint_selection_evidence_survives_long_output(self):
        marker = "LOCATION_SESSION_LOC_SERVICE node=0 port=42 version=2 instance=0 selected_snapshot=1"
        bounded = GNSS.bounded_stdout(marker + "\n" + "x" * 10000)
        self.assertIn(marker, bounded)
        with patch.object(GNSS, "run", return_value={
            "returncode": 1, "stdout": bounded + "\nLOCATION_SESSION_NO_SESSION start_requested=0\n",
        }):
            report = GNSS.collect_native_session("/native", 25)
        self.assertEqual(report["loc_services"], [marker])
        self.assertFalse(report["fix_acquired"])
        self.assertTrue(report["cleanup_complete"])

    def test_explicit_prestart_failure_is_clean_but_not_qualified(self):
        with patch.object(GNSS, "run", return_value={
            "returncode": 1, "stdout": "LOCATION_SESSION_NO_SESSION start_requested=0\n",
            "stderr": "register-events failed",
        }):
            result = GNSS.collect_native_session("/native", 25)
        self.assertTrue(result["cleanup_complete"])
        self.assertTrue(result["no_session_started"])
        self.assertFalse(result["engine_control_qualified"])
        self.assertFalse(result["fix_acquired"])

    def test_prestart_marker_cannot_hide_started_session_or_timeout(self):
        for extra in ("LOCATION_SESSION_STARTED seconds=25\n", ""):
            with self.subTest(extra=extra), patch.object(GNSS, "run", return_value={
                "returncode": 1, "timed_out": not extra,
                "stdout": "LOCATION_SESSION_NO_SESSION start_requested=0\n" + extra,
            }):
                self.assertFalse(GNSS.collect_native_session("/native", 25)["cleanup_complete"])

    def test_satellite_position_requires_success_coordinates_and_start_stop(self):
        cases = [
            ({"status": "success", "satellite": True, "latitude": 12.3, "longitude": 45.6}, True),
            ({"status": "in-progress", "satellite": True, "latitude": 12.3, "longitude": 45.6}, False),
            ({"status": "success", "satellite": False, "latitude": 12.3, "longitude": 45.6}, False),
            ({"status": "success", "satellite": True, "latitude": 91, "longitude": 45.6}, False),
            ({"status": "success", "satellite": True, "latitude": float("nan"), "longitude": 45.6}, False),
            ({"status": "success", "satellite": True, "latitude": True, "longitude": 45.6}, False),
        ]
        for value, expected in cases:
            with self.subTest(value=value), patch.object(GNSS, "run", return_value={
                "returncode": 0, "stdout": "LOCATION_SESSION_STARTED seconds=25\n"
                "LOCATION_SESSION_POSITION " + json.dumps(value) + "\nLOCATION_SESSION_STOPPED nmea=0\n",
            }):
                result = GNSS.collect_native_session("/native", 25)
                self.assertEqual(result["fix_acquired"], expected)
                self.assertTrue(result["engine_control_qualified"])

    def test_long_native_nmea_output_keeps_start_and_default_markers(self):
        fix = sentence("GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
        raw = ("LOCATION_SESSION_NMEA_DEFAULTS reason=not-supported\n"
               "LOCATION_SESSION_STARTED seconds=180\n" +
               ("NMEA " + fix + "\n") * 360 +
               "LOCATION_SESSION_STOPPED nmea=360\n")
        child = Mock(returncode=0, stdout=raw, stderr="")
        with patch.object(GNSS.subprocess, "run", return_value=child) as run:
            result = GNSS.collect_native_session("/native/probe", 180)
        self.assertTrue(result["started"])
        self.assertTrue(result["stopped"])
        self.assertTrue(result["nmea_defaults_used"])
        self.assertTrue(result["fix_acquired"])
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual(run.call_args.kwargs["timeout"], 275)
        self.assertLessEqual(len(GNSS.bounded_stdout(raw)), 8192 + 3 * 513)

    def test_main_selects_executable_native_client(self):
        # Exercise the real entry point: collector-only tests cannot catch a
        # missing module import in the native-helper selection branch.
        response = {
            "returncode": 0,
            "stdout": (
                "LOCATION_SESSION_STARTED seconds=30\n"
                "NMEA " + sentence(
                    "GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"
                ) + "\nLOCATION_SESSION_STOPPED nmea=1\n"
            ),
            "stderr": "",
        }
        output = io.StringIO()
        with patch("sys.argv", [str(PATH), "--seconds", "30"]), patch.object(
            GNSS.Path, "is_file", return_value=True
        ), patch("os.access", return_value=True), patch.object(
            GNSS, "run", return_value=response
        ) as run, patch(
            "sys.stdout", output
        ):
            with self.assertRaises(SystemExit) as stopped:
                GNSS.main()
        self.assertEqual(stopped.exception.code, 0)
        run.assert_called_once_with(
            [str(Path("/usr/libexec/oneplus13-location-session")), "--seconds", "30"], 125
        )
        result = json.loads(output.getvalue().removeprefix("RAM_GNSS_RESULT "))
        self.assertTrue(result["pass"])
        self.assertTrue(result["cleanup_complete"])

    def test_rmc_and_gga_fix_are_parsed(self):
        fixes = GNSS.parse_fix(
            [
                sentence("GNRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W"),
                sentence("GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"),
            ]
        )
        self.assertEqual([row["type"] for row in fixes], ["RMC", "GGA"])
        self.assertAlmostEqual(fixes[0]["latitude"], 48.1173)
        self.assertAlmostEqual(fixes[0]["longitude"], 11.5166667)
        self.assertEqual(fixes[1]["satellites"], 8)

    def test_invalid_quality_is_ignored(self):
        self.assertEqual(
            GNSS.parse_fix(
                ["$GNGGA,123519,4807.038,N,01131.000,E,0,00,99.9,545.4,M,46.9,M,,*64"]
            ),
            [],
        )

    def test_malformed_and_non_nmea_lines_are_ignored(self):
        self.assertEqual(
            GNSS.parse_fix(
                [
                    "not nmea",
                    "$GNRMC,123519,A,48,N,01131.000,E,,,*00",
                    "$GPGGA,1,2,3*ZZ",
                ]
            ),
            [],
        )

    def test_bad_checksum_and_out_of_range_coordinates_are_not_fixes(self):
        self.assertEqual(GNSS.parse_fix([
            "$GNRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A",
            sentence("GNGGA,123519,9961.0,N,01131.0,E,1,08,0.9,1,M,1,M,,"),
            sentence("GNGGA,123519,4807.0,N,01131.0,E,1"),
            sentence("GNGGA,123519,NaN,N,01131.0,E,1,08,0.9,1,M,1,M,,"),
        ]), [])

    def test_missing_or_nonexecutable_native_never_starts_legacy_commands(self):
        for available, executable in ((False, False), (True, False)):
            def present(path):
                return str(path) == str(Path("/run/oneplus13/ram-session")) or available
            with self.subTest(available=available), patch("sys.argv", [str(PATH)]), patch.object(
                GNSS.Path, "is_file", present
            ), patch("os.access", return_value=executable), patch.object(GNSS, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "legacy fallback disabled"):
                    GNSS.main()
                run.assert_not_called()

    def test_native_session_preserves_nmea_and_cleanup(self):
        nmea = sentence(
            "GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"
        )
        response = {
            "returncode": 0,
            "stdout": (
                "LOCATION_SESSION_STARTED seconds=30\n"
                f"NMEA {nmea}\n"
                "LOCATION_SESSION_STOPPED nmea=1\n"
            ),
            "stderr": "",
        }
        with patch.object(GNSS, "run", return_value=response):
            result = GNSS.collect_native_session("/native/probe", 30)
        self.assertTrue(result["engine_control_qualified"])
        self.assertTrue(result["cleanup_complete"])
        self.assertTrue(result["fix_acquired"])
        self.assertEqual(result["nmea"], [nmea])

    def test_native_duration_matches_c_client_before_execution(self):
        for seconds in (4,181,240):
            with self.subTest(seconds=seconds), patch.object(GNSS,'run') as run:
                with self.assertRaisesRegex(ValueError,'180 seconds'):
                    GNSS.collect_native_session('/native/probe',seconds)
                run.assert_not_called()

    def test_native_default_nmea_selection_cannot_substitute_for_a_fix(self):
        with patch.object(GNSS, "run", return_value={
            "returncode": 0,
            "stdout": (
                "LOCATION_SESSION_NMEA_DEFAULTS reason=not-supported\n"
                "LOCATION_SESSION_STARTED seconds=30\n"
                "LOCATION_SESSION_STOPPED nmea=0\n"
            ),
            "stderr": "",
        }):
            result = GNSS.collect_native_session(Path("/native/probe"), 30)
        self.assertTrue(result["nmea_defaults_used"])
        self.assertTrue(result["engine_control_qualified"])
        self.assertTrue(result["cleanup_complete"])
        self.assertFalse(result["fix_acquired"])

    def test_start_profile_evidence_is_recorded_and_bounded(self):
        profile = ("LOCATION_SESSION_START_PROFILE accuracy=high "
                   "recurrence=periodic intermediate=on min_interval_ms=1000 session=1")
        with patch.object(GNSS, "run", return_value={
            "returncode": 1,
            "stdout": profile + "\nLOCATION_SESSION_STOPPED nmea=0\n",
            "stderr": "LOCATION_SESSION_ERROR operation=start-result "
                      "error=QMI protocol error (46): 'GeneralError'\n",
        }):
            result = GNSS.collect_native_session("/native", 25)
        self.assertEqual(result["start_profile"], profile.split(" ", 1)[1])
        self.assertTrue(result["cleanup_complete"])
        self.assertFalse(result["engine_control_qualified"])
        # The profile marker is evidence, never acceptance on its own.
        self.assertFalse(result["fix_acquired"])

    def test_start_profile_marker_survives_long_nmea_output(self):
        fix = sentence("GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
        profile = ("LOCATION_SESSION_START_PROFILE accuracy=high "
                   "recurrence=periodic intermediate=on min_interval_ms=1000 session=1")
        raw = (profile + "\nLOCATION_SESSION_STARTED seconds=180\n" +
               ("NMEA " + fix + "\n") * 400 +
               "LOCATION_SESSION_STOPPED nmea=400\n")
        with patch.object(GNSS.subprocess, "run",
                          return_value=Mock(returncode=0, stdout=raw, stderr="")):
            result = GNSS.collect_native_session("/native/probe", 180)
        self.assertEqual(result["start_profile"], profile.split(" ", 1)[1])
        self.assertTrue(result["engine_control_qualified"])
        self.assertTrue(result["fix_acquired"])
        self.assertLessEqual(len(GNSS.bounded_stdout(raw)), 8192 + 4 * 513)

    def test_missing_start_profile_is_reported_as_absent(self):
        with patch.object(GNSS, "run", return_value={
            "returncode": 1, "stdout": "LOCATION_SESSION_STOPPED nmea=0\n",
            "stderr": "LOCATION_SESSION_ERROR operation=start-result "
                      "error=QMI protocol error (46): 'GeneralError'\n",
        }):
            self.assertIsNone(GNSS.collect_native_session("/native", 25)["start_profile"])

    def test_unlock_cycle_requires_restore_proof_for_clean_cleanup(self):
        saved = ('LOCATION_ENGINE_LOCK_SAVED {"lock_type":4,'
                 '"clients_config":"0xffffffffffffffff"}')
        confirmed = 'LOCATION_ENGINE_UNLOCK_CONFIRMED {"lock_type":1}'
        marker = "LOCATION_SESSION_STOPPED nmea=0\n"
        error = ("LOCATION_SESSION_ERROR operation=start-result "
                 "error=QMI protocol error (46): 'GeneralError'\n")
        cases = [
            (saved + "\n" + confirmed + "\n" + marker, error, False),
            (saved + "\n" + confirmed + "\n" + marker, error, True),
        ]
        for stdout, stderr, with_restore in cases:
            if with_restore:
                stdout += ('LOCATION_ENGINE_LOCK_RESTORED {"lock_type":4}\n')
            with self.subTest(with_restore=with_restore), patch.object(
                GNSS, "run", return_value={"returncode": 1, "stdout": stdout,
                                           "stderr": stderr}
            ):
                result = GNSS.collect_native_session("/native", 25, unlock=True)
            self.assertTrue(result["unlock_requested"])
            self.assertTrue(result["lock_restore_required"])
            self.assertEqual(result["lock_restore_proven"], with_restore)
            self.assertEqual(result["cleanup_complete"], with_restore)

    def test_unlock_cycle_without_restore_attempt_is_not_clean(self):
        stdout = ('LOCATION_ENGINE_LOCK_SAVED {"lock_type":4}\n'
                  'LOCATION_ENGINE_UNLOCK_CONFIRMED {"lock_type":1}\n'
                  'LOCATION_SESSION_STARTED seconds=25\n'
                  'LOCATION_SESSION_STOPPED nmea=0\n')
        with patch.object(GNSS, "run", return_value={
            "returncode": 0, "stdout": stdout, "stderr": "",
        }):
            result = GNSS.collect_native_session("/native", 25, unlock=True)
        self.assertTrue(result["started"])
        self.assertTrue(result["lock_restore_required"])
        self.assertFalse(result["lock_restore_proven"])
        self.assertFalse(result["cleanup_complete"])

    def test_unrestored_lock_marker_is_never_clean(self):
        stdout = ('LOCATION_ENGINE_LOCK_SAVED {"lock_type":4}\n'
                  'LOCATION_ENGINE_UNLOCK_CONFIRMED {"lock_type":1}\n'
                  'LOCATION_SESSION_STOPPED nmea=0\n')
        stderr = ("LOCATION_SESSION_ERROR operation=start-result "
                  "error=QMI protocol error (46): 'GeneralError'\n"
                  "LOCATION_ENGINE_LOCK_UNRESTORED saved=4\n")
        with patch.object(GNSS, "run", return_value={
            "returncode": 1, "stdout": stdout, "stderr": stderr,
        }):
            result = GNSS.collect_native_session("/native", 25, unlock=True)
        self.assertTrue(result["lock_unrestored"])
        self.assertFalse(result["cleanup_complete"])
        self.assertFalse(result["engine_control_qualified"])

    def test_already_unlocked_device_needs_no_restore(self):
        stdout = ('LOCATION_ENGINE_LOCK_SAVED {"lock_type":1}\n'
                  'LOCATION_ENGINE_UNLOCK already=none\n'
                  'LOCATION_SESSION_STARTED seconds=25\n'
                  'LOCATION_SESSION_STOPPED nmea=0\n')
        with patch.object(GNSS, "run", return_value={
            "returncode": 0, "stdout": stdout, "stderr": "",
        }):
            result = GNSS.collect_native_session("/native", 25, unlock=True)
        self.assertTrue(result["lock_already_unlocked"])
        self.assertFalse(result["lock_restore_required"])
        self.assertTrue(result["cleanup_complete"])

    def test_temporary_unlock_passes_flag_and_extends_timeout(self):
        response = {"returncode": 1, "stdout": "LOCATION_SESSION_STOPPED nmea=0\n",
                    "stderr": "LOCATION_SESSION_ERROR operation=start-result "
                              "error=QMI protocol error (46): 'GeneralError'\n"}
        with patch.object(GNSS, "run", return_value=response) as run:
            GNSS.collect_native_session("/native/probe", 80, unlock=True)
        self.assertEqual(run.call_args.args[0], [
            "/native/probe", "--unlock-engine-lock", "--seconds", "80"])
        self.assertEqual(run.call_args.args[1], 220)
        with patch.object(GNSS, "run", return_value=response) as plain:
            GNSS.collect_native_session("/native/probe", 80)
        self.assertEqual(plain.call_args.args[0], ["/native/probe", "--seconds", "80"])
        self.assertEqual(plain.call_args.args[1], 175)


if __name__ == "__main__":
    unittest.main()
