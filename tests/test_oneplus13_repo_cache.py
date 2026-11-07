# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "repo_cache", ROOT / "tools/oneplus13/repo-cache.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class RepositoryCache(unittest.TestCase):
    def test_raw_download_has_repository_fallback(self):
        raw = (
            "https://raw.githubusercontent.com/owner/repo/cache-tag/"
            "cache/oneplus13/r280/chunks/a.part"
        )
        self.assertEqual(
            MOD.download_candidates(raw),
            [
                raw,
                "https://github.com/owner/repo/raw/cache-tag/"
                "cache/oneplus13/r280/chunks/a.part",
            ],
        )

    def test_unsafe_names_are_rejected(self):
        for value in ["../x", "/x", "a/../../x"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                MOD.safe_name(value)
        self.assertEqual(MOD.safe_name("cache/file"), Path("cache/file"))

    def test_extract_accepts_root_directory_but_rejects_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cache.tar"
            payload = root / "payload"
            payload.write_bytes(b"verified")
            with tarfile.open(archive, "w") as output:
                output.add(payload, arcname="./cache/payload")
            destination = root / "output"
            destination.mkdir()
            MOD.safe_extract(archive, destination)
            self.assertEqual((destination / "cache/payload").read_bytes(), b"verified")

            with tarfile.open(archive, "w") as output:
                member = tarfile.TarInfo("cache/link")
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                output.addfile(member)
            with self.assertRaises(ValueError):
                MOD.safe_extract(archive, destination)


if __name__ == "__main__":
    unittest.main()
