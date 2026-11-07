#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host/Actions only: recover matching generic OTA files without a phone."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_url(url, suffixes):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or
            not any((parsed.hostname or '').endswith(s) for s in suffixes)):
        raise ValueError('Unexpected official source host')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class OfficialRanges:
    """Loopback gateway: fixed OTA only, bounded reads, no signed URL in logs."""
    def __init__(self, gate, size):
        safe_url(gate, ['.allawntech.com'])
        self.gate, self.size = gate, size
        self.url, self.expires = None, 0
        self.lock = threading.Lock()
        self.downloaded = 0

    def resolve(self, force=False):
        with self.lock:
            if not force and self.url and time.monotonic() < self.expires:
                return self.url
            request = urllib.request.Request(self.gate, headers={
                'User-Agent': 'okhttp/3.12.12', 'userId': 'oplus-ota|16002018'})
            try:
                response = urllib.request.build_opener(NoRedirect()).open(request, timeout=30)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                if response.status not in (301, 302, 307, 308):
                    raise ValueError('Official download gate did not redirect')
                self.url = response.headers['Location']
            safe_url(self.url, ['.allawnfs.com', '.allawntech.com'])
            self.expires = time.monotonic()+120
            return self.url

    def open_range(self, low, high):
        for attempt in range(2):
            url = self.resolve(force=bool(attempt))
            request = urllib.request.Request(url, headers={
                'Range': f'bytes={low}-{high}', 'Accept-Encoding': 'identity',
                'User-Agent': 'okhttp/3.12.12'})
            try:
                response = urllib.request.urlopen(request, timeout=30)
            except urllib.error.HTTPError as error:
                if error.code == 403 and attempt == 0:
                    error.close()
                    continue  # Expired signature: one fresh gate resolution.
                raise
            if (response.status != 206 or response.headers.get('Content-Range') !=
                    f'bytes {low}-{high}/{self.size}'):
                response.close()
                raise ValueError('CDN did not honor the exact bounded range')
            return response
        raise ValueError('Could not refresh official signature')


def proxy_handler(source):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_HEAD(self):
            if self.path != '/ota.zip':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Length', str(source.size))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()

        def do_GET(self):
            match = re.fullmatch(r'bytes=(\d+)-(\d+)', self.headers.get('Range', ''))
            if self.path != '/ota.zip' or not match:
                self.send_error(416)
                return
            low, high = map(int, match.groups())
            if not (0 <= low <= high < source.size and high-low+1 <= 128*1024*1024):
                self.send_error(416)
                return
            started = False
            try:
                with source.open_range(low, high) as upstream:
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {low}-{high}/{source.size}')
                    self.send_header('Content-Length', str(high-low+1))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.end_headers()
                    started = True
                    remaining = high-low+1
                    while remaining:
                        data = upstream.read(min(1024*1024, remaining))
                        if not data:
                            raise ValueError('Short official range')
                        self.wfile.write(data)
                        remaining -= len(data)
                        with source.lock:
                            source.downloaded += len(data)
            except Exception as error:
                if not started:
                    try:
                        self.send_error(502, type(error).__name__)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # A disconnected consumer must not expose a traceback.
                self.close_connection = True
    return Handler


def run(command, work, label, timeout=3600):
    """Retain raw child logs only in temporary work, never in the artifact."""
    log = work/(label+'.log')
    print(label+' started', flush=True)
    with log.open('wb') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    if result.returncode:
        text = re.sub(r'https?://[^\s"<>]+', '[URL omitted]', log.read_text(errors='replace'))
        raise RuntimeError(label+' failed: '+text[-1200:])
    return log.read_text(errors='replace')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pin', type=Path, default=Path('docs/oneplus13/stock-firmware.json'))
    p.add_argument('--dumper', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    pin = json.loads(a.pin.read_text())
    work, out = a.work.resolve(), a.output.resolve()
    for directory in (work, out):
        if directory.exists() and any(directory.iterdir()):
            raise ValueError('Select an empty extraction/output directory')
        directory.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(pin['catalog_url'], headers={'User-Agent': 'Codex-OTA-research'})
    with urllib.request.urlopen(request, timeout=30) as response:
        catalog = json.loads(response.read(2*1024*1024))
    releases = [r for r in catalog['releases'] if r['model'] == pin['model'] and
                r['version'] == pin['version'] and r['region'] == pin['region'] and
                int(r['size']) == pin['archive_size'] and r['md5'] == pin['archive_md5']]
    if len(releases) != 1:
        raise ValueError('Matching pinned official release not unique')
    source = OfficialRanges(releases[0]['source_url'], pin['archive_size'])
    # Fail before the external tool's retry loop, naming the earliest layer.
    # Eight bytes prove both normal and >4GiB offsets, without a full download.
    try:
        source.resolve()
    except Exception as error:
        raise SystemExit('Official gate preflight failed: '+type(error).__name__) from None
    print('Official gate preflight passed', flush=True)
    try:
        for low, high in [(0, 3), (source.size-4, source.size-1)]:
            with source.open_range(low, high) as response:
                data = response.read(5)
                if len(data) != 4 or (low == 0 and data != b'PK\x03\x04'):
                    raise ValueError('Unexpected ZIP range')
    except Exception as error:
        raise SystemExit('Official CDN TLS/range preflight failed: '+type(error).__name__) from None
    print('Official CDN TLS and exact ranges preflight passed', flush=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), proxy_handler(source))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:'+str(server.server_port)+'/ota.zip'
    dumper = str(a.dumper.resolve())
    try:
        listing = json.loads(run([dumper, 'list', url, '--json', '-p', 'vendor,odm,modem'], work, 'partition-list', 180))
        if set(listing) != {'vendor', 'odm', 'modem'}:
            raise ValueError('Missing selected partitions')
        required = sum(v['size_in_bytes'] for v in listing.values())
        if shutil.disk_usage(work).free < 3*required+1024**3:
            raise ValueError('Insufficient space for isolated partition/filesystem extraction')
        images = work/'images'
        run([dumper, 'extract', url, '-p', 'vendor,odm,modem', '-o', str(images),
             '--verify', '--http-workers', '8', '--part-size', '2M', '--cpu-count', '4'],
            work, 'partition-extract', 4200)
    finally:
        server.shutdown()
        server.server_close()
    partitions = {}
    for name, info in listing.items():
        image = images/(name+'.img')
        if (not re.fullmatch('[0-9a-f]{64}', info['hash']) or image.stat().st_size != info['size_in_bytes']
                or sha256(image) != info['hash']):
            raise ValueError('Independent partition hash/size mismatch: '+name)
        partitions[name] = {'bytes': image.stat().st_size, 'sha256': info['hash']}
    trees = {}
    for name in ['vendor', 'odm']:
        dest = work/(name+'-files')
        run(['fsck.erofs', '--no-preserve', '--extract='+str(dest), str(images/(name+'.img'))],
            work, name+'-erofs', 600)
        trees[name] = dest
    files = {}
    for item in pin['files']:
        target = out/item['output']
        if not target.resolve().is_relative_to(out):
            raise ValueError('Unsafe output path')
        target.parent.mkdir(parents=True, exist_ok=True)
        if item['partition'] == 'modem':
            run(['mcopy', '-i', str(images/'modem.img'), '-n', item['member'], str(target)],
                work, 'modem-'+target.name, 60)
        else:
            base = trees[item['partition']]
            original = (base/item['member']).resolve()
            if not original.is_relative_to(base) or not original.is_file():
                raise ValueError('Missing/unsafe OTA member')
            shutil.copy2(original, target)
        size, digest = target.stat().st_size, sha256(target)
        if size <= 0 or size > 16*1024*1024:
            raise ValueError('Unexpected firmware size')
        if item['owner_hash_observed'] and (size != item['size'] or digest != item['sha256']):
            raise ValueError('Owner firmware equality failed: '+item['output'])
        files[item['output']] = {'bytes': size, 'sha256': digest,
                                 'owner_hash_observed_and_matched': item['owner_hash_observed']}
    manifest = {'scope': 'MATCHING_GENERIC_OTA_FIRMWARE_NOT_HARDWARE_TESTED', 'version': pin['version'],
                'integration_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'hardware_tested': False, 'phone_connections': 0, 'phone_binary_transfers': 0,
                'full_archive_digest_verified': False, 'selected_partition_sha256_verified': True,
                'range_bytes_received': source.downloaded, 'partitions': partitions, 'files': files,
                'dumper_source': pin['dumper'], 'owner_hash_matches': sum(f['owner_hash_observed'] for f in pin['files']),
                'active_wlan_board_id_sar_verified': False, 'firmware_authentication_verified': False}
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (out/'SOURCES.md').write_text('# Generic OTA firmware\n\nOfficial '+pin['version']+
        ' vendor/odm/modem partitions; selected full partition SHA256 checked against payload metadata.\n'+
        'Eight observed phone file hashes must match. No device binary transfer or per-unit calibration.\n'+
        'Vendor firmware retains its original proprietary terms; repository GPL does not relicense it.\n'+
        'Extraction tool source: '+pin['dumper']['repository']+' commit '+pin['dumper']['commit']+
        ' (GPL-3.0, separately built and not included as a binary).\n'+
        'No authentication, board-ID/SAR or hardware operation is claimed.\n')
    (out/'SHA256SUMS').write_text(''.join(f'{sha256(f)}  {f.relative_to(out).as_posix()}\n'
        for f in sorted(out.rglob('*')) if f.is_file() and f.name != 'SHA256SUMS'))
    print(json.dumps({'status': manifest['scope'], 'owner_hash_matches': manifest['owner_hash_matches'],
                      'files': len(files), 'selected_partitions': list(partitions),
                      'received_bytes': source.downloaded, 'phone_connections': 0}), flush=True)


if __name__ == '__main__':
    main()
