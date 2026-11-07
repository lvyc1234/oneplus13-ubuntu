import copy,hashlib,importlib.util,json,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'tools/oneplus13'
DATA=Path(__file__).resolve().parents[1]/'devices/oneplus13/qualified-data/sensors/reviewed-runtime-data.json'
def module(name):
    s=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
data=module('reviewed-sensor-inputs');fs=module('ram-sensor-fileserver');proxy=module('ram-ssc-proxy')
image=module('image-sensors-check')
builder=module('build-phone-sensor-runtime')

class RuntimeInputs(unittest.TestCase):
    def test_unpatched_or_untested_rpc_cannot_enter_public_image(self):
        for defect in ('pad-only','missing-sanitized-tests'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);(out/'phone-rpc').mkdir()
                proof={'native_arm64':True,'host_check_only':False,
                    'c_test_counts':{'release':14,'sanitized':14},
                    'release_tests_pass':True,'asan_ubsan_tests_pass':True,'patches':[{}]*15}
                if defect=='pad-only':proof['patches']=[{}]*5
                else:proof['c_test_counts'].pop('sanitized')
                (out/'phone-rpc/verification.json').write_text(json.dumps(proof))
                with patch.object(builder,'run'),self.assertRaisesRegex(ValueError,'Incomplete phone RPC'):
                    builder.build_phone_rpc(out/'source.tar.gz',out,'meson',out/'log')
                self.assertFalse((out/'hexagonrpc-stage').exists())

    def test_actual_reviewed_data_is_complete(self):
        self.assertEqual(len(data.data_entries(DATA)),922)

    def test_identifier_and_path_injection_are_rejected_after_hash_update(self):
        baseline=json.loads(DATA.read_text())
        for problem in ('identity','escape','executable','import'):
            doc=copy.deepcopy(baseline);name=next(iter(doc['files']));entry=doc['files'][name]
            if problem=='identity':
                raw=json.dumps({'serial_number':{'type':'str','data':'redacted-test'}})
                entry.update(text=raw,bytes=len(raw.encode()),sha256=hashlib.sha256(raw.encode()).hexdigest())
            if problem=='escape':doc['files']['var/lib/erhai/sensors/../escape']=doc['files'].pop(name)
            if problem=='executable':entry['mode']=0o755
            if problem=='import':doc['files']['var/lib/erhai/sensors/import.json']=doc['files'].pop(name)
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'data.json';p.write_text(json.dumps(doc))
                with self.assertRaises(ValueError,msg=problem):data.data_entries(p)

    def test_real_bus_receipt_cannot_skip_release_or_update(self):
        good='PHONE_SENSOR_BUS_RESULT complete=yes gyro_updates=8 accel_updates=1 compass_updates=8 claims=111 releases=111 physical_axes_accepted=no\n'
        self.assertTrue(proxy.interpret(good)['same_connection_three_claims_and_releases'])
        for bad in (good.replace('updates=8','updates=0'),good.replace('releases=111','releases=110'),
                    good.replace('complete=yes','complete=no'),good+good):
            with self.assertRaises(ValueError):proxy.interpret(bad)

    def test_longer_reverse_window_is_explicit(self):
        self.assertIn('--property=RuntimeMaxSec=40s',fs.service_command('/trace'))
        self.assertIn('--property=RuntimeMaxSec=70s',fs.service_command('/trace',proxy_mode=True))

    def test_image_wrapper_binds_paths_kernel_and_real_trace_argument(self):
        digest='a'*64
        doc={'format':'ONEPLUS13_IMAGE_SENSOR_INPUTS_V1','kernel_release':'7.2-test',
             'inputs':{k:{'path':v,'sha256':digest} for k,v in image.PATHS.items()},
             'module_sha256':digest,'policy_sha256':digest}
        args=image.arguments(doc,'samples',lambda p:digest,'7.2-test')
        self.assertIn('--trace-sha256',args);self.assertNotIn('--trace-tool-sha256',args)
        self.assertNotIn('--proxy-helper',args)
        self.assertIn('--proxy-helper',image.arguments(doc,'proxy',lambda p:digest,'7.2-test'))
        with self.assertRaisesRegex(ValueError,'kernel'):image.arguments(doc,'proxy',lambda p:digest,'other-kernel')
        doc['inputs']['archive']['path']='/tmp/arbitrary-private-input.tar.gz'
        with self.assertRaisesRegex(ValueError,'changed'):image.arguments(doc,'proxy',lambda p:digest,'7.2-test')

if __name__=='__main__':unittest.main()
