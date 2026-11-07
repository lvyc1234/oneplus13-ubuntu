#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Package A/B installation files for a fresh persistent root."""
import argparse
import hashlib
import io
from pathlib import Path
import tarfile

REPO = Path(__file__).resolve().parents[2]


def add(archive, name, data, mode=0o644):
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def link(archive, name, target):
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    archive.addfile(info)


def build(linux_boot, qbootctl, output):
    if linux_boot.stat().st_size != 96 * 1024 ** 2:
        raise ValueError("A complete 96 MiB Linux B boot image is required")
    digest = hashlib.sha256(linux_boot.read_bytes()).hexdigest()
    qhash = hashlib.sha256(qbootctl.read_bytes()).hexdigest()
    source = REPO / "switch/oneplus13"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w") as archive:
        add(archive, "etc/fstab",
            b"PARTLABEL=linux_rootfs / ext4 defaults,noatime 0 1\n"
            b"PARTLABEL=shared_media /mnt/shared ext4 defaults,noatime,nofail 0 2\n")
        for name, dest, mode in (
            ("op13-switch", "usr/local/sbin/op13-switch", 0o755),
            ("op13-mark-boot-success", "usr/local/libexec/op13-mark-boot-success", 0o755),
            ("op13-mark-boot-success.service", "usr/lib/systemd/system/op13-mark-boot-success.service", 0o644),
            ("setup-linux-shared", "usr/local/libexec/op13-setup-linux-shared", 0o755),
            ("op13-reboot-android.desktop", "usr/share/applications/op13-reboot-android.desktop", 0o644),
        ):
            add(archive, dest, (source / name).read_bytes().replace(b"\r\n", b"\n"), mode)
        link(archive, "usr/local/bin/op13-switch", "../sbin/op13-switch")
        add(archive, "usr/local/libexec/op13-qbootctl", qbootctl.read_bytes(), 0o755)
        add(archive, "etc/op13-boot-b.sha256",
            (f"{qhash}  /usr/local/libexec/op13-qbootctl\n"
             f"{digest}  /dev/disk/by-partlabel/boot_b\n").encode())
        add(archive, "usr/share/polkit-1/actions/org.op13.switch.policy", b'''<?xml version="1.0" encoding="UTF-8"?>
<policyconfig><vendor>OnePlus 13 Ubuntu</vendor>
<action id="org.op13.switch"><description>Restart into Android</description>
<message>Authenticate to restart into Android</message>
<defaults><allow_any>auth_admin</allow_any><allow_inactive>auth_admin</allow_inactive><allow_active>auth_admin_keep</allow_active></defaults>
<annotate key="org.freedesktop.policykit.exec.path">/usr/local/sbin/op13-switch</annotate>
</action></policyconfig>
''')
        link(archive, "etc/systemd/system/graphical.target.wants/op13-mark-boot-success.service",
             "/usr/lib/systemd/system/op13-mark-boot-success.service")
        for name, target in (("Documents", "Documents"), ("Downloads", "Download"),
                             ("Pictures", "Pictures"), ("Music", "Music"),
                             ("Videos", "Movies"), ("Shared", ".")):
            link(archive, "etc/skel/" + name, "/mnt/shared/0/" + target)
        add(archive, "usr/lib/systemd/system/op13-shared-user.service", b'''[Unit]
Description=Set up shared media for the first GNOME account
ConditionPathExists=!/var/lib/op13/shared-user-ready
RequiresMountsFor=/mnt/shared
[Service]
Type=oneshot
ExecCondition=/usr/bin/getent passwd 1000
ExecStart=/bin/sh -c '/usr/local/libexec/op13-setup-linux-shared "$(findmnt -n -o UUID --mountpoint /mnt/shared)"'
ExecStartPost=/usr/bin/mkdir -p /var/lib/op13
ExecStartPost=/usr/bin/touch /var/lib/op13/shared-user-ready
''')
        add(archive, "usr/lib/systemd/system/op13-shared-user.timer", b'''[Unit]
Description=Wait for the first GNOME account to configure shared media
[Timer]
OnBootSec=30
OnUnitInactiveSec=30
[Install]
WantedBy=timers.target
''')
        link(archive, "etc/systemd/system/timers.target.wants/op13-shared-user.timer",
             "/usr/lib/systemd/system/op13-shared-user.timer")
        for path in (REPO / "third_party/qbootctl").iterdir():
            if path.is_file():
                add(archive, "usr/share/doc/op13-qbootctl/source/" + path.name, path.read_bytes())
        for name in ("LICENSE", "NOTICE"):
            add(archive, "usr/share/doc/oneplus13-ubuntu/" + name, (REPO / name).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linux-boot", type=Path, required=True)
    parser.add_argument("--qbootctl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.linux_boot, args.qbootctl, args.output)


if __name__ == "__main__":
    main()
