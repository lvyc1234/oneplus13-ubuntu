import unittest
from pathlib import Path

class PrivateQEMUObserverTests(unittest.TestCase):
    def test_private_and_public_handoff_checks_are_distinct(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/'devices/oneplus13/desktop/qemu-install').read_text()
        self.assertIn('grep -qx linux,dummy-virt',text)
        self.assertIn('ONEPLUS13_PRIVATE_NATIVE_HANDOFF_OK',text)
        self.assertIn("assert not p('/run/oneplus13/native/activation-attempted').exists()",text)
        self.assertIn('else\n    /run/oneplus13/native-handoff-selftest\nfi',text)
        self.assertIn("stat().st_mode & 0o111 == 0",text)

if __name__=='__main__':unittest.main()
