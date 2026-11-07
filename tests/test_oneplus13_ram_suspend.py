#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import tempfile
import unittest


PATH = (
    Path(__file__).resolve().parents[1]
    / "tools/oneplus13/ram-suspend-check.py"
)
SPEC = importlib.util.spec_from_file_location("ram_suspend", PATH)
SUSPEND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUSPEND)


class SuspendPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.saved = {}
        for name in ("RAM_MARKER", "STATE", "RTC", "POWER_STATE", "MEM_SLEEP"):
            self.saved[name] = getattr(SUSPEND, name)
        SUSPEND.RAM_MARKER = root / "ram-session"
        SUSPEND.STATE = root / "suspend"
        SUSPEND.RTC = root / "rtc0"
        SUSPEND.POWER_STATE = root / "power-state"
        SUSPEND.MEM_SLEEP = root / "mem-sleep"
        SUSPEND.RAM_MARKER.write_text("", encoding="utf-8")
        SUSPEND.POWER_STATE.write_text("freeze mem disk\n", encoding="utf-8")
        SUSPEND.MEM_SLEEP.write_text("s2idle [deep]\n", encoding="utf-8")
        SUSPEND.RTC.mkdir()
        (SUSPEND.RTC / "name").write_text("rtc-test\n")
        (SUSPEND.RTC / "since_epoch").write_text("1000\n")
        (SUSPEND.RTC / "wakealarm").write_text("0\n")
        device = SUSPEND.RTC / "device/power"
        device.mkdir(parents=True)
        (device / "wakeup").write_text("disabled\n")
        self.original_protected = SUSPEND.protected
        SUSPEND.protected = lambda: None

    def tearDown(self):
        SUSPEND.protected = self.original_protected
        for name, value in self.saved.items():
            setattr(SUSPEND, name, value)
        self.temp.cleanup()

    def test_prepare_sets_bounded_rtc_wakealarm(self):
        SUSPEND.prepare(15)
        result = SUSPEND.json.loads(
            (SUSPEND.STATE / "result.json").read_text()
        )
        self.assertTrue(result["pass"])
        self.assertEqual(result["wakealarm_epoch"], 1015)
        self.assertEqual((SUSPEND.RTC / "wakealarm").read_text().strip(), "1015")
        self.assertEqual((SUSPEND.RTC / "device/power/wakeup").read_text().strip(), "enabled")

    def test_prepare_selects_requested_s2idle_mode(self):
        (SUSPEND.MEM_SLEEP).write_text("s2idle [deep]\n", encoding="utf-8")
        original_write = SUSPEND.write
        def kernel_write(path, value):
            if path == SUSPEND.MEM_SLEEP and value == "s2idle":
                path.write_text("[s2idle] deep\n", encoding="utf-8")
                return
            original_write(path, value)
        SUSPEND.write = kernel_write
        try:
            SUSPEND.prepare(15, "s2idle")
        finally:
            SUSPEND.write = original_write
        result = SUSPEND.json.loads(
            (SUSPEND.STATE / "result.json").read_text()
        )
        self.assertEqual(result["sleep_mode"], "s2idle")
        self.assertEqual(
            SUSPEND.MEM_SLEEP.read_text(encoding="utf-8"),
            "[s2idle] deep\n",
        )

    def test_bad_duration_is_rejected_before_alarm_change(self):
        for value in (4, 21, 32, 45, 46):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SUSPEND.prepare(value)
            self.assertEqual((SUSPEND.RTC / "wakealarm").read_text().strip(), "0")


if __name__ == "__main__":
    unittest.main()
