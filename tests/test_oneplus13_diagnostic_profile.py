import ast,hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
TOOLS=Path(__file__).resolve().parents[1]/'tools/oneplus13'
sys.path.insert(0,str(TOOLS))
spec=importlib.util.spec_from_file_location('profile_image_verifier',TOOLS/'verify-complete-image.py')
verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
spec=importlib.util.spec_from_file_location('profile_image_packager',TOOLS/'package-source-local.py')
packager=importlib.util.module_from_spec(spec);spec.loader.exec_module(packager)

class ProfileContainerTests(unittest.TestCase):
    def manifest(self,profile):
        files={'usr/lib/oneplus13-container/kernel.config':'hash',
               'usr/libexec/oneplus13-container-kernel.py':'hash',
               'usr/libexec/oneplus13-ram-container-check.py':'hash'}
        if profile=='full':files['etc/docker/daemon.json']='hash'
        return dict(desktop_profile=profile,container_files=files,
                    container_userspace_installed=profile=='full',container_daemon_auto_started=False)

    def test_diagnostic_keeps_kernel_checks_without_docker_userspace(self):
        self.assertFalse(verifier.validate_container_profile(self.manifest('diagnostic')))

    def test_full_still_requires_docker_configuration(self):
        m=self.manifest('full');self.assertTrue(verifier.validate_container_profile(m))
        del m['container_files']['etc/docker/daemon.json']
        with self.assertRaises(ValueError):verifier.validate_container_profile(m)

    def test_diagnostic_rejects_unexpected_docker_or_missing_kernel_check(self):
        for name,action in [('etc/docker/daemon.json','add'),('usr/lib/oneplus13-container/kernel.config','remove')]:
            m=self.manifest('diagnostic')
            if action=='add':m['container_files'][name]='hash'
            else:del m['container_files'][name]
            with self.assertRaises(ValueError):verifier.validate_container_profile(m)

    def test_userspace_and_autostart_claims_must_match_payload(self):
        for profile in ['full','diagnostic']:
            for key in ['container_userspace_installed','container_daemon_auto_started']:
                m=self.manifest(profile);m[key]=not m[key]
                with self.assertRaises(ValueError):verifier.validate_container_profile(m)

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):verifier.validate_container_profile(self.manifest('unknown'))

class StagingLifetimeTests(unittest.TestCase):
    def fixture(self,root,qualified=False):
        target=root/'usr/libexec/test-runtime';target.parent.mkdir(parents=True);target.write_bytes(b'runtime')
        manifest=root/'usr/lib/oneplus13-desktop/diagnostic-runtime.json';manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps(dict(files={'usr/libexec/test-runtime':hashlib.sha256(b'runtime').hexdigest()},
            hardware_tested=qualified,native_dpms_qualified=False,system_suspend_enabled=False)))
        return target

    def test_detached_bindings_survive_staging_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root);captured=packager.capture_diagnostic_bindings(root)
        self.assertFalse(root.exists())
        self.assertEqual(captured['diagnostic_runtime_files']['usr/libexec/test-runtime'],hashlib.sha256(b'runtime').hexdigest())
        self.assertFalse(captured['diagnostic_runtime_hardware_qualified'])

    def test_changed_runtime_is_rejected_before_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=self.fixture(root);target.write_bytes(b'changed')
            with self.assertRaises(ValueError):packager.capture_diagnostic_bindings(root)

    def test_unearned_hardware_claim_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root,qualified=True)
            with self.assertRaises(ValueError):packager.capture_diagnostic_bindings(root)

    def test_main_never_uses_stage_after_cleanup(self):
        tree=ast.parse((TOOLS/'package-source-local.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        cleanup=next(n.lineno for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                     and isinstance(n.func.value,ast.Name) and n.func.value.id=='staging' and n.func.attr=='cleanup')
        self.assertFalse([n.lineno for n in ast.walk(main) if isinstance(n,ast.Name) and n.id=='stage' and n.lineno>cleanup])

if __name__=='__main__':unittest.main()
