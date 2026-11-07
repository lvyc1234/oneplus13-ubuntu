#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import gzip
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest


PATH = (
    Path(__file__).resolve().parents[1]
    / "tools/oneplus13/ram-modem-gnss-check.py"
)
SPEC = importlib.util.spec_from_file_location("ram_modem", PATH)
MODEM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEM)


class ModemFirmwareInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original_marker = MODEM.RAM_MARKER
        self.original_firmware = MODEM.FIRMWARE_ROOT
        self.original_installed = MODEM.INSTALLED_ROOT
        MODEM.RAM_MARKER = self.root / "ram-session"
        MODEM.FIRMWARE_ROOT = self.root / "firmware"
        MODEM.INSTALLED_ROOT = (
            MODEM.FIRMWARE_ROOT / "qcom/sm8750/oneplus/dodge"
        )
        MODEM.FIRMWARE_ROOT.mkdir()
        MODEM.RAM_MARKER.write_text("", encoding="utf-8")
        self.archive = self.root / "modem.tar.gz"
        self.review = self.root / "review.json"
        self.files = {
            "modem.mdt": b"metadata",
            "modem.b00": b"segment",
            "modem_dtb.mdt": b"dtb metadata",
            "modem_dtb.b00": b"dtb segment",
            "modemr.jsn": b"{}",
        }
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, value in self.files.items():
                info = tarfile.TarInfo("qcom/sm8750/oneplus/dodge/" + name)
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
        self.review.write_text(
            MODEM.json.dumps(
                {
                    "firmware_sha256": {
                        "modem.mdt": hashlib.sha256(b"metadata").hexdigest(),
                        "modem.b00": hashlib.sha256(b"segment").hexdigest(),
                        "modem_dtb.mdt": hashlib.sha256(b"dtb metadata").hexdigest(),
                        "modem_dtb.b00": hashlib.sha256(b"dtb segment").hexdigest(),
                    },
                    "resource_json_sha256": hashlib.sha256(b"{}").hexdigest(),
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        MODEM.RAM_MARKER = self.original_marker
        MODEM.FIRMWARE_ROOT = self.original_firmware
        MODEM.INSTALLED_ROOT = self.original_installed
        self.temp.cleanup()

    def test_safe_archive_installs_exact_reviewed_files(self):
        digest = MODEM.sha256(self.archive)
        result = MODEM.install_firmware(self.archive, self.review, digest)
        self.assertEqual(result["file_count"], 5)
        self.assertEqual(
            (MODEM.FIRMWARE_ROOT / "qcom/sm8750/oneplus/dodge/modem.mdt").read_bytes(),
            b"metadata",
        )

    def test_archive_hash_and_ram_marker_are_required(self):
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            MODEM.install_firmware(self.archive, self.review, "0" * 64)
        MODEM.RAM_MARKER.unlink()
        with self.assertRaisesRegex(RuntimeError, "RAM session"):
            MODEM.install_firmware(
                self.archive, self.review, MODEM.sha256(self.archive)
            )

    def test_review_mismatch_is_rejected(self):
        review = MODEM.json.loads(self.review.read_text())
        review["firmware_sha256"]["modem.b00"] = "0" * 64
        self.review.write_text(MODEM.json.dumps(review), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs"):
            MODEM.install_firmware(
                self.archive, self.review, MODEM.sha256(self.archive)
            )

    def test_preinstalled_reviewed_firmware_is_verified_without_archive(self):
        MODEM.install_firmware(
            self.archive, self.review, MODEM.sha256(self.archive)
        )
        result = MODEM.verify_installed_firmware(self.review)
        self.assertEqual(result["source"], "installed_rootfs")
        self.assertEqual(result["file_count"], 5)

    def test_preinstalled_firmware_mismatch_is_rejected(self):
        MODEM.install_firmware(
            self.archive, self.review, MODEM.sha256(self.archive)
        )
        (MODEM.INSTALLED_ROOT / "modem.b00").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "differs"):
            MODEM.verify_installed_firmware(self.review)

    def test_runtime_facts_require_ipcrtr_endpoint(self):
        entry = {
            "path": self.root / "remoteproc0",
            "target": "/soc@0/4080000.remoteproc",
            "name": "modem",
            "state": "running",
            "firmware": "modem.mdt",
        }
        endpoints = self.root / "rpmsg"
        endpoints.mkdir()
        (endpoints / "modem.IPCRTR.-1.-1").mkdir()
        original = MODEM.Path
        class ScopedPath:
            def __new__(cls, value):
                if value == "/sys/bus/rpmsg/devices":
                    return endpoints
                if value == "/dev":
                    return self.root / "dev"
                if value == "/sys/class/qrtr":
                    return self.root / "qrtr"
                return original(value)
        try:
            MODEM.Path = ScopedPath
            result = MODEM.runtime_facts(entry)
        finally:
            MODEM.Path = original
        self.assertTrue(result["rpmsg_has_ipcrtr"])

    def test_wait_control_plane_reads_fresh_remoteproc_state(self):
        original_find = MODEM.find_mpss
        original_runtime = MODEM.runtime_facts
        original_sleep = MODEM.time.sleep
        rows = [
            {
                "path": self.root / "remoteproc0",
                "name": "modem",
                "state": "running",
                "firmware": "modem.mdt",
            },
            {
                "path": self.root / "remoteproc0",
                "name": "modem",
                "state": "offline",
                "firmware": "modem.mdt",
            },
        ]
        calls = []
        try:
            MODEM.find_mpss = lambda: rows.pop(0)
            MODEM.runtime_facts = lambda entry: {
                "state": entry["state"],
                "rpmsg_has_ipcrtr": False,
            }
            MODEM.time.sleep = lambda _: calls.append("sleep")
            result = MODEM.wait_control_plane(
                {
                    "path": self.root / "remoteproc0",
                    "name": "modem",
                    "state": "running",
                    "firmware": "modem.mdt",
                },
                5,
            )
        finally:
            MODEM.find_mpss = original_find
            MODEM.runtime_facts = original_runtime
            MODEM.time.sleep = original_sleep
        self.assertEqual(result["state"], "offline")
        self.assertEqual(calls, ["sleep"])

    def test_running_control_plane_is_reused_without_a_remoteproc_cycle(self):
        original_wait = MODEM.wait_control_plane
        entry = {
            "path": self.root / "remoteproc0",
            "name": "modem",
            "state": "running",
            "firmware": "modem.mdt",
        }
        calls = []
        try:
            MODEM.wait_control_plane = lambda received, timeout: (
                calls.append((received, timeout))
                or {"rpmsg_has_ipcrtr": True, "state": "running"}
            )
            result = MODEM.reusable_running_control_plane(entry)
        finally:
            MODEM.wait_control_plane = original_wait
        self.assertTrue(result["rpmsg_has_ipcrtr"])
        self.assertEqual(calls, [(entry, 10)])

    def test_modem_service_is_bounded_and_retries_ipcrtr(self):
        service = (
            Path(__file__).resolve().parents[1]
            / "devices/oneplus13/modem/oneplus13-modem.service"
        ).read_text(encoding="utf-8")
        self.assertIn("TimeoutStartSec=120s", service)
        self.assertIn(
            "ConditionPathExists=!/run/oneplus13/ram-session", service
        )
        self.assertIn("for attempt in 1 2", service)
        self.assertIn("have_ipcrtr", service)
        self.assertIn('echo stop > "$rp/state"', service)


if __name__ == "__main__":
    unittest.main()
