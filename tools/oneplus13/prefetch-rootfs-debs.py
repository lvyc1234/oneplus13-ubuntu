#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Fetch an APT SHA256 URI plan concurrently, retaining a verified public cache.

APT must first validate the pinned snapshot's signed indexes. This only handles
transport; apt-get --no-download still performs the installation and checks.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
from verified_download import ubuntu_pool_urls

OWNER = b'oneplus13-rootfs-debs-v1\n'


def plan(text, base):
    origin = urlsplit(base)
    if (origin.scheme != 'https' or not origin.hostname or origin.username or
            origin.password or origin.query or origin.fragment or not base.endswith('/')):
        raise ValueError('Expected a pinned HTTPS archive prefix')
    entries = {}
    for line in text.splitlines():
        if not line.startswith("'"):
            continue
        fields = shlex.split(line)
        if len(fields) != 4:
            raise ValueError('Malformed APT URI row')
        uri, name, size, checksum = fields
        parsed = urlsplit(uri)
        decoded_path = unquote(parsed.path, errors='strict')
        if (not uri.startswith(base + 'pool/') or parsed.scheme != 'https' or
                parsed.netloc != origin.netloc or parsed.query or parsed.fragment or
                not re.fullmatch(r'/[A-Za-z0-9/._+~:-]+', decoded_path) or
                any(part in ('.', '..') for part in decoded_path.split('/')) or
                re.search(r'%(?:2f|5c)', parsed.path, re.I)):
            raise ValueError('Package outside pinned archive pool')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+%_~:-]*\.deb', name):
            raise ValueError('Unsafe package filename')
        if not size.isdecimal() or not 0 < int(size) <= 512 * 1024 * 1024:
            raise ValueError('Invalid package size')
        if not re.fullmatch(r'SHA256:[a-fA-F0-9]{64}', checksum):
            raise ValueError('APT must supply SHA256')
        row = (uri, name, int(size), checksum[7:].lower())
        if name in entries and entries[name] != row:
            raise ValueError('Conflicting package filename')
        entries[name] = row
    if not entries or len(entries) > 4096:
        raise ValueError('Empty or oversized package plan')
    return list(entries.values())


def safe_directory(path):
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Symlink in package directory ancestry')
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError('Package directory is not a directory')
    return path


def cache_directory(path):
    path = safe_directory(path)
    marker = path / '.oneplus13-deb-cache'
    if not marker.exists() and not marker.is_symlink():
        if any(path.iterdir()):
            raise ValueError('Refusing an unowned nonempty cache')
        with marker.open('xb') as stream:
            stream.write(OWNER)
    if marker.is_symlink() or not marker.is_file() or marker.read_bytes() != OWNER:
        raise ValueError('Unexpected package cache owner')
    return path


def verified(path, size, sha):
    if path.is_symlink():
        raise ValueError('Symlink package refused')
    if not path.exists():
        return False
    if not path.is_file():
        raise ValueError('Package path is not a regular file')
    if path.stat().st_size != size:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == sha


def curl_download(uri, target, stop, deadline):
    command = ['curl', '--fail', '--silent', '--show-error', '--location',
               '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '5',
               '--connect-timeout', '10', '--max-time', '180',
               '--max-filesize', str(512 * 1024 * 1024),
               '--speed-limit', '2048', '--speed-time', '20',
               '--output', str(target), uri]
    # Transport stderr may contain proxy details; keep it out of public logs.
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        while proc.poll() is None:
            if stop.wait(0.25) or time.monotonic() >= deadline:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError('Package transport cancelled or deadline reached')
        if proc.returncode:
            raise RuntimeError('Package transport failed: '+uri)


def fetch(row, cache, archives, stop, deadline, download=curl_download):
    uri, name, size, sha = row
    candidates = ubuntu_pool_urls(uri)
    stored = cache / name
    hit = verified(stored, size, sha)
    if not hit:
        # Alternate the original snapshot with the two hash-identical official
        # pools. Public archive endpoints can fail independently and briefly.
        for attempt in range(9):
            if stop.is_set() or time.monotonic() >= deadline:
                raise RuntimeError('Package prefetch deadline reached')
            fd, temporary = tempfile.mkstemp(prefix='.deb-', suffix='.partial', dir=cache)
            os.close(fd)
            temporary = Path(temporary)
            try:
                download(candidates[min(attempt, len(candidates) - 1)], temporary, stop, deadline)
                if not verified(temporary, size, sha):
                    raise RuntimeError('Downloaded package size or SHA256 mismatch')
                # Never replace a link, including when the cache was modified meanwhile.
                if stored.is_symlink():
                    raise ValueError('Symlink cache destination refused')
                # The staging builder runs as root; Actions' cache step runs
                # as the ordinary runner and must be able to read public debs.
                temporary.chmod(0o644)
                os.replace(temporary, stored)
                break
            except RuntimeError:
                if attempt == 8:
                    raise
            finally:
                temporary.unlink(missing_ok=True)
    staged = archives / name
    if staged.is_symlink() or staged.exists():
        raise ValueError('Expected an empty APT staging destination')
    with stored.open('rb') as source, staged.open('xb') as target:
        shutil.copyfileobj(source, target)
    staged.chmod(0o644)
    if not verified(staged, size, sha):
        raise RuntimeError('Staged package verification failed')
    return hit, size


def prefetch(rows, cache, archives, workers=32, seconds=3600):
    cache = cache_directory(cache)
    archives = safe_directory(archives)
    stop = threading.Event()
    start = time.monotonic()
    deadline = start + seconds
    result = {'packages': len(rows), 'completed': 0, 'cache_hits': 0, 'bytes': 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(fetch, row, cache, archives, stop, deadline) for row in rows}
        try:
            while pending:
                done, pending = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
                for future in done:
                    hit, size = future.result()
                    result['completed'] += 1
                    result['cache_hits'] += int(hit)
                    result['bytes'] += size
                elapsed = time.monotonic() - start
                if not pending or elapsed - result.get('last_log_seconds', -10) >= 10:
                    result['last_log_seconds'] = round(elapsed, 1)
                    print('ROOTFS_DEB_PROGRESS ' + json.dumps(result, sort_keys=True), flush=True)
                if time.monotonic() >= deadline:
                    raise RuntimeError('Package prefetch deadline reached')
        finally:
            stop.set()
            for future in pending:
                future.cancel()
    result.pop('last_log_seconds', None)
    result['seconds'] = round(time.monotonic() - start, 3)
    result['signed_metadata_source'] = 'APT URI plan; installation rechecked by APT'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--apt-uri', required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = prefetch(plan(args.plan.read_text(), args.apt_uri), args.cache, args.archives)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
