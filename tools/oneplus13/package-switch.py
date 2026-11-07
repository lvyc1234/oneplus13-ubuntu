#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Package the OnePlus 13 KernelSU dual-boot/shared-storage module."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import uuid
import zipfile

REPO = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--boot-b-sha256", required=True)
    parser.add_argument("--shared-uuid", type=uuid.UUID, required=True)
    parser.add_argument("--source", type=Path, default=REPO / "switch/oneplus13")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{64}", args.boot_b_sha256):
        parser.error("--boot-b-sha256 must be a lowercase SHA-256 digest")
    source = args.source.resolve()
    module = source / "ksu-module"
    files = [path for path in sorted(module.rglob("*")) if path.is_file()]
    files += [source / "op13-switch-android", source / "op13-mount-shared"]
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = path.name if path.parent == source else path.relative_to(module)
            info = zipfile.ZipInfo(str(name))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if path.suffix in (".sh", "") else 0o100644) << 16
            archive.writestr(info, path.read_bytes().replace(b"\r\n", b"\n"))
        archive.writestr("boot-b.sha256", f"{args.boot_b_sha256}  /dev/block/by-name/boot_b\n")
        archive.writestr("shared-uuid", f"{args.shared_uuid}\n")
    (output.with_suffix(".json")).write_text(
        json.dumps(
            {
                "module_id": "op13_dualboot",
                "boot_b_sha256": args.boot_b_sha256,
                "shared_uuid": str(args.shared_uuid),
                "files": {str(path.relative_to(source)): sha(path) for path in files},
                "output_bytes": output.stat().st_size,
                "output_sha256": sha(output),
            },
            indent=2,
        )
        + "\n"
    )
    print(output)


if __name__ == "__main__":
    main()
