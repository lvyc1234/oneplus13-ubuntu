"""Host-only rejection tests for private RAM runtime archive inputs."""
import hashlib,importlib.util,io,struct,tarfile,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('sensor_runtime',Path(__file__).resolve().parents[1]/'tools/oneplus13/assemble-sensor-ram-runtime.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Archive(unittest.TestCase):
 def fixture(self,root,kind='data',extra=None,wrong_arch=False,mode=0o644):
  path=root/(kind+'.tar.gz')
  value=bytearray(64) if kind=='userspace' else bytearray(b'unit data')
  if kind=='userspace':value[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',value,18,62 if wrong_arch else 183)
  name=m.PREFIX+'bin/hexagonrpcd' if kind=='userspace' else 'var/lib/erhai/sensors/persist/registry/fixture'
  info={'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest(),'mode':mode}
  with tarfile.open(path,'w:gz') as tf:
   item=tarfile.TarInfo(name);item.size=len(value);item.mode=mode;tf.addfile(item,io.BytesIO(value))
   if kind=='data':
    for link,target in m.DATA_LINKS.items():
     item=tarfile.TarInfo(link);item.type=tarfile.SYMTYPE;item.linkname=target;tf.addfile(item)
   if extra:
    item=tarfile.TarInfo(extra);item.size=1;tf.addfile(item,io.BytesIO(b'x'))
  manifest={'files':[dict(info,path=name)]} if kind=='userspace' else {'files':{name:info},'links':m.DATA_LINKS}
  return path,manifest
 def test_verified_data_and_only_original_mappings(self):
  with tempfile.TemporaryDirectory() as folder:
   path,manifest=self.fixture(Path(folder));entries=m.read_archive(path,manifest,False)
   self.assertEqual(len(entries),4)
 def test_hooks_traversal_and_unmanifested_files_rejected(self):
  for path in ['etc/udev/rules.d/81-pad.rules','../outside','usr/share/erhai-sensors/hexagonrpcd/extra']:
   with self.subTest(path=path),tempfile.TemporaryDirectory() as folder:
    archive,manifest=self.fixture(Path(folder),extra=path)
    with self.assertRaises(ValueError):m.read_archive(archive,manifest,False)
 def test_changed_data_permissions_and_file_bytes_rejected(self):
  for fault in ['mode','hash','mapping']:
   with self.subTest(fault=fault),tempfile.TemporaryDirectory() as folder:
    archive,manifest=self.fixture(Path(folder),mode=0o755 if fault=='mode' else 0o644)
    if fault=='hash':next(iter(manifest['files'].values()))['sha256']='0'*64
    if fault=='mapping':manifest['links']=dict(m.DATA_LINKS,unreviewed='/outside')
    with self.assertRaises(ValueError):m.read_archive(archive,manifest,False)
 def test_non_arm64_binary_and_pad_hooks_rejected(self):
  for fault in ['architecture','hook']:
   with self.subTest(fault=fault),tempfile.TemporaryDirectory() as folder:
    archive,manifest=self.fixture(Path(folder),'userspace',wrong_arch=fault=='architecture',extra='usr/lib/systemd/system/pad.service' if fault=='hook' else None)
    with self.assertRaises(ValueError):m.read_archive(archive,manifest,True)
 def test_output_does_not_include_hooks_or_invent_sensor_matrix(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);users,um=self.fixture(root,'userspace');data,dm=self.fixture(root)
   um.update(archive_sha256=m.digest(users),scope='OFFLINE_ARM64_USERSPACE_ONLY_NOT_PHONE_ACCEPTANCE',reference_rom_excluded=True,package_hooks_excluded=True)
   dm.update(archive_sha256=m.digest(data),original_importer_unchanged=True,no_executable_or_service_included=True)
   out=root/'runtime.tar.gz';report=m.assemble(users,data,um,dm,out)
   self.assertFalse(report['sensor_attach_or_stream_eligible']);self.assertFalse(report['service_or_udev_hooks_included'])
   with tarfile.open(out) as tf:self.assertEqual(len(tf.getmembers()),5)
   with self.assertRaises(FileExistsError):m.assemble(users,data,um,dm,out)
if __name__=='__main__':unittest.main()
