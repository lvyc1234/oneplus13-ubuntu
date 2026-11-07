# SPDX-License-Identifier: GPL-2.0-only
import importlib,sys,unittest
import hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/oneplus13'))
video=importlib.import_module('video-probe-dtb')
from stock import Fdt,strings


class VideoProbeTree(unittest.TestCase):
    def test_phone_firmware_is_present_and_phone_specific(self):
        path=ROOT/'devices/oneplus13/qualified-data/firmware/qcom/sm8750/oneplus/dodge/vpu35_4v.mbn'
        self.assertEqual(path.stat().st_size,video.FIRMWARE_BYTES)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),video.FIRMWARE_SHA256)
        manifest=__import__('json').loads((ROOT/'devices/oneplus13/qualified-data/manifest.json').read_text())
        self.assertEqual(manifest['files']['firmware/qcom/sm8750/oneplus/dodge/vpu35_4v.mbn'],
                         {'sha256':video.FIRMWARE_SHA256,'bytes':video.FIRMWARE_BYTES})

    def test_real_phone_candidate_changes_only_video_status_and_firmware(self):
        blob=(ROOT/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes()
        camera,_=video.camera.derive(blob);before=Fdt(camera)
        result,report=video.derive(blob);after=Fdt(result)
        self.assertEqual(before.reservations,after.reservations)
        self.assertEqual(set(before.nodes),set(after.nodes))
        for path,props in before.nodes.items():
            if path!=video.VIDEO:self.assertEqual(props,after.nodes[path],path)
        props=after.nodes[video.VIDEO].copy()
        self.assertEqual(props.pop('status'),b'okay\0')
        self.assertEqual(strings(props.pop('firmware-name')),[video.FIRMWARE])
        props['status']=b'disabled\0'
        self.assertEqual(props,before.nodes[video.VIDEO])
        self.assertFalse(report['firmware_authentication_qualified'])
        self.assertFalse(report['hardware_decode_qualified'])

    def test_active_core_or_changed_memory_is_rejected_before_enable(self):
        blob=(ROOT/'devices/oneplus13/qualified-data/calibrated.dtb').read_bytes()
        for path,key,value in [(video.VIDEO,'status',b'okay\0'),
                               (video.VIDEO,'memory-region',video.camera.u32(0xffff)),
                               (video.MEMORY,'reg',video.camera.u32(0,0x9c300000,0,0x900000))]:
            with self.subTest(path=path,key=key):
                tree=Fdt(blob);tree.nodes[path][key]=value
                with self.assertRaises(ValueError):video.validate_video_graph(tree)


if __name__=='__main__':unittest.main()
