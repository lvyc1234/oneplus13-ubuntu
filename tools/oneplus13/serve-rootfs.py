#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Serve one verified SquashFS as bounded USB chunks; no directory or file browsing."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from common import sha256

CHUNK = 64*1024*1024


def handler(image, stats=None):
    size = image.stat().st_size

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.serve(True)

        def do_HEAD(self):
            self.serve(False)

        def serve(self, body):
            match = re.fullmatch(r'/oneplus13-rootfs.parts/part([0-9]{4})', self.path)
            if not match or int(match[1])*CHUNK >= size:
                self.send_error(404)
                return
            offset = int(match[1])*CHUNK
            remaining = min(CHUNK, size-offset)
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(remaining))
            self.end_headers()
            if not body:
                return
            try:
                with image.open('rb') as source:
                    source.seek(offset)
                    while remaining:
                        data = source.read(min(65536, remaining))
                        if not data:
                            self.close_connection = True
                            return
                        self.wfile.write(data)
                        if stats is not None:
                            stats['bytes'] += len(data)
                        remaining -= len(data)
                if stats is not None:
                    stats['parts'].append(int(match[1]))
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
    return Handler


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', required=True, type=Path)
    p.add_argument('--sha256', required=True)
    p.add_argument('--check-only', action='store_true')
    p.add_argument('--bind', default='172.16.13.2', help='Listen address on the build host')
    a = p.parse_args()
    image = a.image.resolve()
    if not 1048576 <= image.stat().st_size <= 3*1024**3 or sha256(image) != a.sha256:
        raise SystemExit('Rootfs size/hash does not match the selected desktop cmdline')
    print(json.dumps({'sha256': a.sha256, 'bytes': image.stat().st_size,
                      'parts': (image.stat().st_size+CHUNK-1)//CHUNK,
                      'bind': a.bind + ':8000', 'phone_connections': 0}), flush=True)
    if not a.check_only:
        with ThreadingHTTPServer((a.bind, 8000), handler(image)) as server:
            server.serve_forever()
