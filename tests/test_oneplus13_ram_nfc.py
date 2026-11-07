#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).resolve().parents[1] / "tools/oneplus13/ram-nfc-check.py"
SPEC = importlib.util.spec_from_file_location("ram_nfc_check", PATH)
NFC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NFC)


class NfcPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original_policy = NFC.POLICY
        self.original_marker = NFC.RAM_MARKER
        NFC.POLICY = root / "policy.conf"
        NFC.RAM_MARKER = root / "ram-session"

    def tearDown(self):
        NFC.POLICY = self.original_policy
        NFC.RAM_MARKER = self.original_marker
        self.temp.cleanup()

    def exact_policy(self):
        lines = ["# policy"]
        for module in reversed(NFC.ALLOWED):
            lines.extend((f"blacklist {module}", f"install {module} /bin/false"))
        NFC.POLICY.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_exact_nfc_denies_are_removed_and_others_retained(self):
        NFC.RAM_MARKER.write_text("", encoding="utf-8")
        self.exact_policy()
        with NFC.POLICY.open("a", encoding="utf-8") as stream:
            stream.write(
                "blacklist unrelated\ninstall unrelated /bin/false\n"
            )
        self.assertEqual(NFC.allow_exact_modules(), list(NFC.ALLOWED))
        value = NFC.POLICY.read_text(encoding="utf-8")
        for module in NFC.ALLOWED:
            self.assertNotIn(f"blacklist {module}\n", value)
            self.assertNotIn(f"install {module} /bin/false\n", value)
        self.assertIn("blacklist unrelated\n", value)
        self.assertIn("install unrelated /bin/false\n", value)

    def test_missing_ram_marker_refuses(self):
        self.exact_policy()
        with self.assertRaisesRegex(RuntimeError, "RAM session"):
            NFC.allow_exact_modules()

    def test_missing_exact_entry_refuses(self):
        NFC.RAM_MARKER.write_text("", encoding="utf-8")
        NFC.POLICY.write_text(
            "blacklist nfc\ninstall nfc /bin/false\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "exact NFC deny set"):
            NFC.allow_exact_modules()

    def test_default_enabled_policy_needs_no_mutation(self):
        NFC.RAM_MARKER.write_text("", encoding="utf-8")
        NFC.POLICY.write_text(
            "# reviewed image leaves the NCI closure enabled\n",
            encoding="utf-8",
        )
        before = NFC.POLICY.read_bytes()
        self.assertEqual(NFC.allow_exact_modules(), [])
        self.assertEqual(NFC.POLICY.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
