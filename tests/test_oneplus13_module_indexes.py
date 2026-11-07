# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools/oneplus13'
sys.path.insert(0,str(TOOLS))
SPEC=importlib.util.spec_from_file_location('phone_verify_indexes',TOOLS/'verify.py')
VERIFY=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(VERIFY)


class ModuleIndexes(unittest.TestCase):
    def archive(self,dependencies=None,omit=None):
        entries={'kernel/a.ko':b'a','kernel/b.ko':b'b',
                 'modules.dep':dependencies or b'kernel/a.ko: kernel/b.ko\nkernel/b.ko:\n',
                 'modules.dep.bin':b'binary-index','modules.alias':b'alias synthetic a\n',
                 'modules.alias.bin':b'binary-alias'}
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w') as archive:
            for name,data in entries.items():
                if name==omit:continue
                info=tarfile.TarInfo('lib/modules/test/'+name);info.size=len(data)
                archive.addfile(info,io.BytesIO(data))
        stream.seek(0)
        return tarfile.open(fileobj=stream)

    def test_complete_dependency_graph(self):
        with self.archive() as archive:VERIFY.check_module_indexes(archive,'test')

    def test_actual_server_failure_missing_depmod_output_is_rejected(self):
        for name in ['modules.dep','modules.dep.bin','modules.alias','modules.alias.bin']:
            with self.subTest(index=name),self.archive(omit=name) as archive,self.assertRaisesRegex(ValueError,'index'):
                VERIFY.check_module_indexes(archive,'test')

    def test_missing_dependency_duplicate_or_incomplete_graph_is_rejected(self):
        for value in [b'kernel/a.ko: kernel/missing.ko\nkernel/b.ko:\n',
                      b'kernel/a.ko:\n',b'kernel/a.ko:\nkernel/a.ko:\n',
                      b'kernel/a.ko kernel/b.ko\n']:
            with self.subTest(value=value),self.archive(value) as archive,self.assertRaises(ValueError):
                VERIFY.check_module_indexes(archive,'test')


if __name__=='__main__':unittest.main()
