# SPDX-License-Identifier: GPL-2.0-only
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


class LocalizationAndSsh(unittest.TestCase):
    def setUp(self):
        self.image=json.loads((ROOT/'devices/oneplus13/ubuntu.json').read_text())
        self.rootfs=(ROOT/'tools/oneplus13/rootfs.sh').read_text()
        self.bringup=(ROOT/'devices/oneplus13/desktop/qualified-bringup').read_text()

    def test_chinese_desktop_and_input_method_are_pinned(self):
        for package in ['language-pack-zh-hans','language-pack-gnome-zh-hans',
                        'fonts-noto-cjk','ibus','ibus-libpinyin','im-config']:
            self.assertIn(package,self.image['packages'])
        self.assertIn('locale-gen en_US.UTF-8 zh_CN.UTF-8',self.rootfs)
        self.assertIn('LANG=zh_CN.UTF-8',self.rootfs)
        self.assertIn('Language=zh_CN.UTF-8',self.bringup)
        self.assertIn("('ibus', 'libpinyin')",self.bringup)
        self.assertLess(self.bringup.index('systemctl restart gdm'),
                        self.bringup.index('gsettings set org.gnome.desktop.input-sources'))
        self.assertLess(self.bringup.index("pgrep -u a -x gnome-shell"),
                        self.bringup.index('gsettings set org.gnome.desktop.input-sources'))
        self.assertIn('mru-sources',self.bringup)
        self.assertIn('sleep 8',self.bringup)
        self.assertIn('test "$actual_sources" = "$expected_sources"',self.bringup)
        for setting in ['dock-fixed true','autohide false','intellihide false',
                        "dock-position 'LEFT'",'extend-height true']:
            self.assertIn(setting,self.bringup)
        self.assertLess(self.bringup.index('pgrep -u a -x gnome-shell'),
                        self.bringup.index('gnome-extensions enable ubuntu-dock@ubuntu.com'))

    def test_ssh_server_is_enabled_without_root_or_shared_host_keys(self):
        self.assertIn('openssh-server',self.image['packages'])
        self.assertIn('PasswordAuthentication yes',self.rootfs)
        self.assertIn('PermitRootLogin no',self.rootfs)
        self.assertNotIn('AllowUsers a',self.rootfs)
        self.assertIn('Refusing to ship a precreated desktop user',self.rootfs)
        self.assertIn('GNOME Initial Setup to create the first user',self.rootfs)
        self.assertIn('oneplus13-ssh-host-keys.service',self.rootfs)
        self.assertIn('ssh.service',self.rootfs)
        self.assertIn('rm -f "$root"/etc/ssh/ssh_host_*',self.rootfs)

    def test_qualcomm_remote_services_are_installed_and_enabled(self):
        for package in ('rmtfs', 'tqftpserv'):
            self.assertIn(package, self.image['packages'])
        for unit in ('rmtfs.service', 'tqftpserv.service'):
            self.assertIn(unit, self.rootfs)


if __name__=='__main__':
    unittest.main()
