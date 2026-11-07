# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
import io
from pathlib import Path
import tarfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools/oneplus13'
spec = importlib.util.spec_from_file_location('container_kernel', TOOLS / 'container-kernel.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class ContainerDelivery(unittest.TestCase):
    def config(self):
        return '\n'.join('CONFIG_' + name + '=y' for name in mod.BUILTIN + mod.BOOLS) + '\n' + \
            '\n'.join('CONFIG_' + name + '=m' for name in list(mod.MODULES) + mod.FEATURES)

    def archive(self, omit=None):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for name in set(mod.MODULES.values()) - {omit}:
                entry = tarfile.TarInfo('lib/modules/test/kernel/' + name + '.ko')
                entry.size = 1; archive.addfile(entry, io.BytesIO(b'X'))
        stream.seek(0)
        return tarfile.open(fileobj=stream)

    def test_legacy_compat_symbol_does_not_substitute_for_evaluator(self):
        text = self.config().replace('CONFIG_IP_NF_IPTABLES_LEGACY=m', 'CONFIG_IP_NF_IPTABLES=m')
        with self.assertRaisesRegex(ValueError, 'IP_NF_IPTABLES_LEGACY'): mod.check_config(text)

    def test_nft_backend_cannot_be_silently_missing(self):
        with self.assertRaisesRegex(ValueError, 'NF_TABLES'):
            mod.check_config(self.config().replace('CONFIG_NF_TABLES=m', '# CONFIG_NF_TABLES is not set'))

    def test_config_alone_cannot_hide_omitted_delivery(self):
        with self.archive('iptable_nat') as archive, self.assertRaisesRegex(ValueError, 'iptable_nat'):
            mod.check_payload(self.config(), archive, 'test')
        with self.archive() as archive:
            self.assertTrue(mod.check_payload(self.config(), archive, 'test')['iptables_nft'])

    def test_builtin_does_not_require_a_ko(self):
        text = self.config().replace('CONFIG_IP_NF_NAT=m', 'CONFIG_IP_NF_NAT=y')
        with self.archive('iptable_nat') as archive: mod.check_payload(text, archive, 'test')

    def test_bridge_family_menu_does_not_invent_a_module(self):
        self.assertEqual(mod.FEATURES, ['NF_TABLES_BRIDGE'])
        self.assertNotIn('nf_tables_bridge', mod.MODULES.values())
        with self.archive() as archive: mod.check_payload(self.config(), archive, 'test')
        with self.assertRaisesRegex(ValueError, 'NF_TABLES_BRIDGE'):
            mod.check_config(self.config().replace('CONFIG_NF_TABLES_BRIDGE=m', '# CONFIG_NF_TABLES_BRIDGE is not set'))

if __name__ == '__main__': unittest.main()
