#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a clean persistent installation bundle from this source build."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

from common import boot_sections, sha256

REPO = Path(__file__).resolve().parents[2]


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=REPO / "out/oneplus13-complete-image")
    parser.add_argument("--output", type=Path, default=REPO / "out/oneplus13-persistent")
    args = parser.parse_args()
    image, out = args.image.resolve(), args.output.resolve()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    manifest = json.loads((image / "automatic-usb-manifest.json").read_text())
    if manifest["source_commit"] != commit:
        raise ValueError("Persistent bundle requires this commit's complete image")
    base = image / manifest["boot_image_file"]
    if sha256(base) != manifest["boot_image_sha256"]:
        raise ValueError("Base boot image changed")
    if sha256(image / "rootfs.squashfs") != manifest["rootfs_sha256"]:
        raise ValueError("Root filesystem differs from the base boot binding")
    out.mkdir(parents=True, exist_ok=False)
    qsource = REPO / "third_party/qbootctl"
    qpin = json.loads((qsource / "source.json").read_text())
    for name, digest in qpin["files"].items():
        if sha256(qsource / name) != digest:
            raise ValueError("qbootctl source changed: " + name)
    with tempfile.TemporaryDirectory(prefix="op13-persistent-build-") as temp:
        temp = Path(temp)
        locks = json.loads((REPO / "kernel/source.json").read_text())["tool_inputs"]
        for name in ("mkbootimg.py", "avbtool.py"):
            pin = locks[name]
            archive = REPO / pin["bundled_archive"]
            if sha256(archive) != pin["archive_sha256"]:
                raise ValueError("AOSP build input changed")
            with tarfile.open(archive) as source:
                if name == "mkbootimg.py":
                    source.extractall(temp, filter="data")
                else:
                    (temp / name).write_bytes(source.extractfile(pin["archive_path"]).read())
            if sha256(temp / name) != pin["sha256"]:
                raise ValueError("AOSP tool hash mismatch")
        # A disposable key supplies the accepted signed 96 MiB geometry.
        # Installation requires an unlocked bootloader.
        key = temp / "avb.pem"
        run("openssl", "genrsa", "-out", key, "4096")
        run(sys.executable, REPO / "tools/oneplus13/persistent.py",
            "--base-boot", base, "--output", out / "boot.img",
            "--mkbootimg", temp / "mkbootimg.py", "--avbtool", temp / "avbtool.py", "--key", key)
        run("gcc", "-std=gnu11", "-static", "-O2", "-Wall", "-Wextra", "-o", temp / "qbootctl",
            *[qsource / name for name in ("qbootctl.c", "bootctrl_impl.c", "gpt-utils.c", "ufs-bsg.c", "crc32.c")])
        elf = (temp / "qbootctl").read_bytes()
        if elf[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", elf, 18)[0] != 183:
            raise ValueError("qbootctl must be a little-endian ARM64 ELF executable")
        importlib.import_module("persistent-overlay").build(
            out / "boot.img", temp / "qbootctl", out / "persistent-overlay.tar")
    shutil.copyfile(image / "rootfs.squashfs", out / "rootfs.squashfs")
    shutil.copyfile(base, out / "boot-ram.img")
    helper_dir = out / "tools"
    helper_dir.mkdir()
    for name in ("install-persistent.py", "op13-prepare-dualboot.py", "package-switch.py",
                 "serve-rootfs.py", "common.py"):
        shutil.copyfile(REPO / "tools/oneplus13" / name, helper_dir / name)
    shutil.copytree(REPO / "switch/oneplus13", out / "switch/oneplus13")
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(REPO / name, out / name)
    shutil.copytree(REPO / "docs", out / "docs")
    for name in ("kernel-source.tar.zst", "kernel.config", "kernel-COPYING"):
        shutil.copyfile(REPO / "out/oneplus13" / name, out / name)
    # Distribute the exact integration scripts and source with this binary bundle.
    run("git", "archive", "--format=tar.gz", "--output=" + str(out / "integration-source.tar.gz"), commit)
    def contents(name):
        return subprocess.check_output(["unsquashfs", "-cat", str(out / "rootfs.squashfs"), name])
    if any(row and 1000 <= int(row.split(":")[2]) < 60000
           for row in contents("etc/passwd").decode().splitlines()):
        raise ValueError("A normal account is present in the installation image")
    if contents("etc/machine-id").strip():
        raise ValueError("A machine identity is present in the installation image")
    if b"InitialSetupEnable=true" not in contents("etc/gdm3/custom.conf"):
        raise ValueError("GNOME first setup is disabled")
    if b"CLUTTER_PAINT=disable-clipped-redraws" not in contents("etc/environment"):
        raise ValueError("The accepted GPU redraw setting is missing")
    payload = boot_sections((out / "boot.img").read_bytes())
    files = {path.relative_to(out).as_posix(): sha256(path)
             for path in sorted(out.rglob("*")) if path.is_file()}
    result = {"scope": "ONEPLUS13_CLEAN_PERSISTENT_AB_INSTALL", "source_commit": commit,
              "kernel_release": manifest["kernel_release"], "normal_users": 0,
              "first_setup": "GNOME Initial Setup", "hardware_revalidation_required": True,
              "kernel_sha256": hashlib.sha256(payload["kernel"]).hexdigest(), "files": files}
    (out / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    files["manifest.json"] = sha256(out / "manifest.json")
    (out / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(files.items())))
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, indent=2))


if __name__ == "__main__":
    main()
