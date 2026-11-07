#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Real Docker bridge/NAT/published-port test in a disposable RAM net namespace."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time

ROOT = Path('/run/oneplus13/container-check')

def run(args, timeout=15, **kwargs):
    p = subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)
    if p.returncode:
        raise ValueError(Path(args[0]).name + ' failed rc=' + str(p.returncode) +
                         ': ' + p.stderr.decode(errors='replace')[-2500:])
    if len(p.stdout) > 1024 * 1024: raise ValueError('Container output exceeded bound')
    return p.stdout.decode().strip()

def protected_ram():
    if os.geteuid() != 0 or not Path('/run/oneplus13/ram-session').is_file():
        raise ValueError('Protected root RAM session required')
    if not any(row.split()[1:3] == ['/', 'overlay'] for row in Path('/proc/mounts').read_text().splitlines()):
        raise ValueError('RAM overlay required')
    for d in Path('/sys/class/block').iterdir():
        if not d.name.startswith(('ram', 'loop', 'zram')) and (d/'ro').read_text().strip() != '1':
            raise ValueError('Physical storage is writable')
    for unit in ('oneplus13-ram-guard.service', 'oneplus13-ram-deadline.timer'):
        run(['systemctl', 'is-active', '--quiet', unit], 4)

def tiny_image(path):
    # Use the delivered ARM64 static BusyBox; no registry, Internet or foreign
    # rootfs. The image contains an HTTP server with a fixed public test body.
    busy = Path('/usr/bin/busybox').read_bytes()
    if busy[:6] != b'\x7fELF\x02\x01' or int.from_bytes(busy[18:20], 'little') != 183:
        raise ValueError('Native ARM64 BusyBox required')
    with tarfile.open(path, 'w') as archive:
        for name in ('bin', 'www', 'tmp'):
            item = tarfile.TarInfo(name); item.type = tarfile.DIRTYPE; item.mode = 0o755
            archive.addfile(item)
        for name, content, mode in [('bin/busybox', busy, 0o755), ('www/index.html', b'OP13_CONTAINER_OK\n', 0o644)]:
            item = tarfile.TarInfo(name); item.mode = mode; item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
        item = tarfile.TarInfo('bin/sh'); item.type = tarfile.SYMTYPE; item.linkname = 'busybox'; item.mode = 0o777
        archive.addfile(item)
    return hashlib.sha256(busy).hexdigest()

def inside(outer_net):
    if str(os.stat('/proc/self/ns/net').st_ino) == outer_net:
        raise ValueError('Network namespace was not isolated')
    run(['mount', '--make-rprivate', '/'])
    Path('/run/netns').mkdir(exist_ok=True)
    run(['mount', '-t', 'tmpfs', '-o', 'mode=0755,size=1m', 'tmpfs', '/run/netns'])
    run(['ip', 'link', 'set', 'lo', 'up'])
    # Real off-bridge peer, wholly inside the disposable outer namespace. Its
    # lack of a container-subnet route makes successful HTTP prove SNAT works.
    peer = 'op13-peer'
    daemon = server = None
    client = ['docker', '--host', 'unix://' + str(ROOT/'docker.sock')]
    def docker(*args, timeout=15): return run(client + list(args), timeout)
    try:
        run(['ip', 'netns', 'add', peer])
        run(['ip', 'link', 'add', 'uplink', 'type', 'veth', 'peer', 'name', 'peer0'])
        run(['ip', 'link', 'set', 'peer0', 'netns', peer])
        run(['ip', 'address', 'add', '10.242.13.1/24', 'dev', 'uplink'])
        run(['ip', 'link', 'set', 'uplink', 'up'])
        for args in [['link','set','lo','up'], ['address','add','10.242.13.2/24','dev','peer0'], ['link','set','peer0','up']]:
            run(['ip', '-n', peer] + args)
        run(['ip', 'route', 'add', 'default', 'via', '10.242.13.2'])
        www = ROOT/'www'; www.mkdir(); (www/'index.html').write_text('OP13_PEER_OK\n')
        server = subprocess.Popen(['ip', 'netns', 'exec', peer, '/usr/bin/busybox', 'httpd', '-f', '-p', '8080', '-h', str(www)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Verify the legacy evaluator and both required legacy tables as well
        # as Docker's distribution-default iptables-nft backend.
        for table in ('filter', 'nat'):
            run(['iptables-legacy', '-w', '2', '-t', table, '-N', 'OP13_TEST'])
        run(['iptables-legacy', '-w', '2', '-A', 'OP13_TEST', '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'])
        run(['iptables-legacy', '-w', '2', '-A', 'OP13_TEST', '-m', 'addrtype', '--dst-type', 'LOCAL', '-j', 'ACCEPT'])
        run(['iptables-legacy', '-w', '2', '-t', 'nat', '-A', 'OP13_TEST', '-j', 'MASQUERADE'])
        log = (ROOT/'dockerd.log').open('wb')
        # Exercise the delivered RAM storage defaults in the private daemon.
        config=Path('/etc/docker/daemon.json').read_bytes()
        if json.loads(config)!={'storage-driver':'vfs','log-driver':'local','log-opts':{'max-size':'5m','max-file':'2'}}:
            raise ValueError('Unexpected RAM Docker defaults')
        (ROOT/'daemon.json').write_bytes(config)
        daemon = subprocess.Popen(['dockerd', '--config-file='+str(ROOT/'daemon.json'), '--host=unix://' + str(ROOT/'docker.sock'),
             '--data-root=' + str(ROOT/'data'), '--exec-root=' + str(ROOT/'exec'),
             '--pidfile=' + str(ROOT/'docker.pid'),
             '--exec-opt=native.cgroupdriver=cgroupfs', '--iptables=true', '--ip6tables=true', '--userland-proxy=false',
             '--bip=10.243.13.1/24', '--default-address-pool=base=10.244.0.0/16,size=24'],
             stdout=log, stderr=subprocess.STDOUT, env={**os.environ, 'DOCKER_TMPDIR': str(ROOT/'tmp')})
        until = time.monotonic() + 22
        while True:
            if daemon.poll() is not None: raise ValueError('Private Docker daemon exited')
            try:
                if docker('info', '--format', '{{.Driver}}', timeout=3) != 'vfs':
                    raise ValueError('Unexpected Docker storage driver')
                break
            except (ValueError, subprocess.TimeoutExpired):
                if time.monotonic() >= until: raise ValueError('Private Docker readiness timed out')
                time.sleep(.25)
        image = ROOT/'busybox.tar'; busy_hash = tiny_image(image)
        docker('import', str(image), 'op13-test:local')
        docker('network', 'create', '--driver', 'bridge', '--subnet', '10.244.13.0/24', 'op13-test')
        docker('run', '-d', '--name', 'web', '--network', 'op13-test', '-p', '18080:8080',
               'op13-test:local', '/bin/busybox', 'httpd', '-f', '-p', '8080', '-h', '/www')
        address = docker('inspect', '--format', '{{(index .NetworkSettings.Networks "op13-test").IPAddress}}', 'web')
        bridge_http = docker('run', '--rm', '--network', 'op13-test', 'op13-test:local', '/bin/busybox', 'wget', '-T', '5', '-qO-', 'http://' + address + ':8080/')
        dns_http = docker('run', '--rm', '--network', 'op13-test', 'op13-test:local', '/bin/busybox', 'wget', '-T', '5', '-qO-', 'http://web:8080/')
        nat_http = docker('run', '--rm', '--network', 'op13-test', 'op13-test:local', '/bin/busybox', 'wget', '-T', '5', '-qO-', 'http://10.242.13.2:8080/')
        # From a separate peer namespace, not localhost/userland-proxy fallback.
        published = run(['ip', 'netns', 'exec', peer, '/usr/bin/busybox', 'wget', '-T', '5', '-qO-', 'http://10.242.13.1:18080/'])
        if [bridge_http, dns_http, nat_http, published] != ['OP13_CONTAINER_OK', 'OP13_CONTAINER_OK', 'OP13_PEER_OK', 'OP13_CONTAINER_OK']:
            raise ValueError('Unexpected bridge/NAT/DNS/published-port HTTP result')
        docker('rm', '-f', 'web'); docker('network', 'rm', 'op13-test')
        result = {'scope': 'REAL_DOCKER_ISOLATED_RAM_BRIDGE', 'pass': True,
            'bridge_container_http': True, 'embedded_dns_http': True,
            'masquerade_off_bridge_http': True, 'published_port_peer_http': True,
            'legacy_filter_nat_rules': True, 'iptables_backend': run(['iptables', '--version']),
            'storage_driver': 'vfs', 'overlay2_tested': False, 'busybox_sha256': busy_hash,
            'physical_network_rules_modified': False, 'external_registry_used': False}
    finally:
        if daemon is not None:
            # Remove test containers before terminating the private containerd.
            if daemon.poll() is None:
                try: docker('rm', '-f', 'web', timeout=5)
                except (ValueError, subprocess.TimeoutExpired): pass
                daemon.terminate()
                try: daemon.wait(timeout=10)
                except subprocess.TimeoutExpired: daemon.kill(); daemon.wait(timeout=3)
            log.close()
        if server is not None:
            server.terminate()
            try: server.wait(timeout=3)
            except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=2)
        # Destruction of this PID+network+mount namespace removes every peer,
        # bridge, firewall rule and private daemon mount even after failure.
        run(['ip', 'netns', 'delete', peer], 5)
    print('RAM_CONTAINER_RESULT ' + json.dumps(result, sort_keys=True), flush=True)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inside', help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.inside:
        inside(a.inside); return
    protected_ram()
    ROOT.mkdir(mode=0o700)  # single use per RAM boot
    (ROOT/'tmp').mkdir()
    outer = str(os.stat('/proc/self/ns/net').st_ino)
    command = ['unshare', '--mount', '--net', '--pid', '--fork', '--kill-child=SIGKILL', '--mount-proc',
               sys.executable, str(Path(__file__).resolve()), '--inside', outer]
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=105)
        (ROOT/'stdout').write_text(p.stdout); (ROOT/'stderr').write_text(p.stderr)
        if p.returncode:
            raise ValueError('Container namespace rc=' + str(p.returncode) + ': ' + p.stderr[-2500:])
        print(p.stdout, end='', flush=True)
    finally:
        protected_ram()

if __name__ == '__main__': main()
