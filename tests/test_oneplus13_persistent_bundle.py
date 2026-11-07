# SPDX-License-Identifier: GPL-2.0-only
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/oneplus13" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PersistentBundleTests(unittest.TestCase):
    def test_installation_unlocks_data_disk_after_guard_stop_and_udev_drain(self):
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp)
            commands = []
            def runner(*args):
                commands.append(args)
                if args[:2] == ("udevadm", "control"):
                    for name in ("09-oneplus13-ram-readonly.rules", "10-oneplus13-ram-storage.rules"):
                        self.assertEqual((rules / name).readlink(), Path("/dev/null"))
                return "0"
            installer = load("install-persistent")
            installer.enter_installation_mode(rules, runner)
            self.assertEqual(commands[0][0:2], ("systemctl", "stop"))
            self.assertIn("oneplus13-ram-guard.service", commands[0])
            self.assertLess(commands.index(("udevadm", "settle", "--timeout=10")),
                            commands.index(("blockdev", "--setrw", "/dev/sda")))
            installer.enter_installation_mode(rules, runner)

    def test_installation_rejects_a_conflicting_rule_before_stopping_services(self):
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp)
            (rules / "09-oneplus13-ram-readonly.rules").write_text("unexpected rule")
            commands = []
            with self.assertRaises(ValueError):
                load("install-persistent").enter_installation_mode(rules, lambda *args: commands.append(args))
            self.assertEqual(commands, [])

    def test_overlay_selects_ab_switch_and_preserves_separate_qbootctl_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            boot = base / "boot.img"
            with boot.open("wb") as output:
                output.truncate(96 * 1024 ** 2)
            qboot = base / "qbootctl"
            qboot.write_bytes(b"example-arm64-program")
            output = base / "overlay.tar"
            load("persistent-overlay").build(boot, qboot, output)
            with tarfile.open(output) as archive:
                names = archive.getnames()
                self.assertNotIn("var/lib/op13/switch/android/boot.img", names)
                checksums = archive.extractfile("etc/op13-boot-b.sha256").read().decode()
                self.assertIn(hashlib.sha256(qboot.read_bytes()).hexdigest(), checksums)
                self.assertIn("/dev/disk/by-partlabel/boot_b", checksums)
                desktop = archive.extractfile("usr/share/applications/op13-reboot-android.desktop").read()
                self.assertIn(b"/usr/local/sbin/op13-switch to-android", desktop)
                self.assertIn("usr/share/doc/op13-qbootctl/source/LICENSE", names)
                self.assertEqual(archive.getmember("etc/skel/Downloads").linkname, "/mnt/shared/0/Download")

    def test_relocated_module_packager_uses_supplied_source_and_installation_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            tool = base / "tools/package-switch.py"
            tool.parent.mkdir()
            tool.write_bytes((ROOT / "tools/oneplus13/package-switch.py").read_bytes())
            output = base / "module.zip"
            subprocess.run([sys.executable, str(tool), "--source", str(ROOT / "switch/oneplus13"),
                "--output", str(output), "--boot-b-sha256", "ab" * 32,
                "--shared-uuid", "12345678-1234-1234-1234-123456789abc"], check=True, capture_output=True)
            import zipfile
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read("boot-b.sha256"), ("ab" * 32 + "  /dev/block/by-name/boot_b\n").encode())
                self.assertIn("LICENSE", archive.namelist())
                self.assertNotIn("post-fs-data.sh", archive.namelist())

    def test_install_bundle_rejects_omitted_payloads_changed_bytes_and_escape_paths(self):
        module = load("install-persistent")
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            required = ["boot.img", "rootfs.squashfs", "persistent-overlay.tar",
                        "tools/op13-prepare-dualboot.py", "tools/package-switch.py"]
            files = {}
            for name in required:
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"payload")
                files[name] = hashlib.sha256(b"payload").hexdigest()
            value = {"scope": "ONEPLUS13_CLEAN_PERSISTENT_AB_INSTALL", "normal_users": 0, "files": files}
            manifest = base / "manifest.json"
            manifest.write_text(json.dumps(value))
            module.check_bundle(base)
            (base / "boot.img").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                module.check_bundle(base)
            (base / "boot.img").write_bytes(b"payload")
            files["../outside"] = "ab" * 32
            manifest.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                module.check_bundle(base)
            del files["../outside"]
            del files["boot.img"]
            manifest.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                module.check_bundle(base)


if __name__ == "__main__":
    unittest.main()
