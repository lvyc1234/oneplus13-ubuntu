# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


class MesaRuntimePolicy(unittest.TestCase):
    def test_desktop_package_uses_release_assertion_policy(self):
        build=(ROOT/'tools/build-mesa.py').read_text()
        package=(ROOT/'tools/package-mesa.py').read_text()
        self.assertIn(
            "'-Db_ndebug=' + ('true' if args.profile in ('desktop', 'gpu-2404') else 'false')",
            build)
        self.assertIn("'-Db_ndebug=true' not in identity['flags']",package)


if __name__=='__main__':
    unittest.main()
