# SPDX-License-Identifier: GPL-2.0-only
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'tools/oneplus13/ssc-patches/0004-bounded-phone-light-lookup.patch'
GUARD = ROOT / 'tools/oneplus13/ssc-patches/0001-add-metadata-only-discovery-guard.patch'


class SensorLightFallback(unittest.TestCase):
    def setUp(self):
        self.sources = json.loads((ROOT / 'packaging/sensors/sources.json').read_text())
        self.text = PATCH.read_text()

    def test_patch_is_pinned_and_only_retries_ambient_light_once(self):
        source = next(item for item in self.sources['sources'] if item['name'] == 'libssc')
        builder=(ROOT/'tools/oneplus13/build-phone-ssc-probe.py').read_text()
        self.assertIn(hashlib.sha256(PATCH.read_bytes()).hexdigest(), builder)
        self.assertEqual([Path(item['file']).name for item in source['patches']], [
            '0001-libssc-select-the-delivery-and-batching-of-streams-at-run-time.patch',
            '0002-libssc-choose-the-stream-delivery-by-sensor-type.patch',
        ])
        self.assertIn('gboolean light_discover_all;', self.text)
        self.assertIn('g_strcmp0 (priv->data_type, "ambient_light")', self.text)
        self.assertIn('!priv->light_discover_all', self.text)
        self.assertIn('msg.only_default_values = !priv->light_discover_all;', self.text)
        self.assertIn('priv->light_uid_index + 1 < priv->light_uid_count', self.text)
        self.assertIn('attribute (ctx->sensor, ctx->task);', self.text)
        self.assertNotIn('SSC_MSG_REPORT_MEASUREMENT', self.text)
        guard = GUARD.read_text()
        self.assertIn('!msg->only_default_values && g_str_equal(msg->data_type,"ambient_light")', guard)
        self.assertNotIn('names[]={"registry","accel","ambient_light","gyro","mag","all"}', guard)


if __name__ == '__main__':
    unittest.main()
