# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the host-only OTA proxy contract, with no external network."""
import importlib.util
from io import BytesIO
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.request

path = Path(__file__).resolve().parents[1]/'tools/oneplus13/extract-stock-firmware.py'
spec = importlib.util.spec_from_file_location('stock_firmware', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Source:
    size = 100
    downloaded = 0
    lock = threading.Lock()
    failed = False

    def open_range(self, low, high):
        if self.failed:
            raise ValueError('https://signed-url.example/private-signature')
        return BytesIO(bytes(range(100))[low:high+1])


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.source = Source()
        self.finished = threading.Event()
        finished = self.finished
        handler = module.proxy_handler(self.source)
        class ObservedHandler(handler):
            def do_GET(self):
                try:
                    super().do_GET()
                finally:
                    finished.set()
        self.server = module.ThreadingHTTPServer(('127.0.0.1', 0), ObservedHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)+'/ota.zip'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, headers=None, method='GET'):
        return urllib.request.urlopen(urllib.request.Request(self.url, headers=headers or {}, method=method), timeout=2)

    def test_exact_range_and_size(self):
        with self.request(method='HEAD') as response:
            self.assertEqual(response.headers['Content-Length'], '100')
            self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
        with self.request({'Range': 'bytes=5-9'}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers['Content-Range'], 'bytes 5-9/100')
            self.assertEqual(response.read(), bytes([5, 6, 7, 8, 9]))
        # The socket consumer can finish before the handler updates accounting.
        self.assertTrue(self.finished.wait(2), 'Proxy handler did not finish')
        self.assertEqual(self.source.downloaded, 5)

    def test_full_and_out_of_bounds_reads_rejected(self):
        for headers in [{}, {'Range': 'bytes=0-100'}, {'Range': 'bytes=9-5'}, {'Range': 'bytes=-5'}]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(headers)
            self.assertEqual(caught.exception.code, 416)
        self.assertEqual(self.source.downloaded, 0)

    def test_upstream_error_does_not_disclose_signed_url(self):
        self.source.failed = True
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request({'Range': 'bytes=0-3'})
        self.assertEqual(caught.exception.code, 502)
        self.assertNotIn(b'private-signature', caught.exception.read())

    def test_source_host_restrictions(self):
        for url in ['http://component-ota-cn.allawntech.com/x',
                    'https://allawntech.com.attacker.example/x',
                    'https://user:password@component-ota-cn.allawntech.com/x']:
            with self.assertRaises(ValueError):
                module.safe_url(url, ['.allawntech.com'])
        module.safe_url('https://component-ota-cn.allawntech.com/downloadCheck', ['.allawntech.com'])


if __name__ == '__main__':
    unittest.main()
