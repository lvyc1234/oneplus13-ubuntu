#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/oneplus13"))
PATH = ROOT / "tools/oneplus13/build-modem-probe-boot.py"
SPEC = importlib.util.spec_from_file_location("modem_boot", PATH)
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)


class ModemFirmwareArchiveTests(unittest.TestCase):
    def test_deterministic_tar_paths_hashes_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            firmware = root / "firmware"
            firmware.mkdir()
            (firmware / "modem.mdt").write_bytes(b"metadata")
            (firmware / "modem.b00").write_bytes(b"segment")
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            BOOT.deterministic_tar(firmware, first)
            BOOT.deterministic_tar(firmware, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                rows = archive.getmembers()
                self.assertEqual(
                    [row.name for row in rows],
                    [
                        "qcom/sm8750/oneplus/dodge/modem.b00",
                        "qcom/sm8750/oneplus/dodge/modem.mdt",
                    ],
                )
                self.assertTrue(all(row.uid == 0 and row.gid == 0 for row in rows))
                self.assertTrue(all(row.mtime == 0 for row in rows))
                self.assertEqual(archive.extractfile(rows[1]).read(), b"metadata")


if __name__ == "__main__":
    unittest.main()
