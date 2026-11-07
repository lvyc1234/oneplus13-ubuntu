#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""One-shot RAM-only loading of two hash-bound filesystem modules."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/run/oneplus13/combined')
STATE = ROOT/'r225-filesystem-modules.private.json'
NAMES = ('exfat', 'ntfs3')
RULE = ('ACTION=="add", SUBSYSTEM=="block", KERNEL!="loop*|ram*|zram*", '
        'TEST=="/run/oneplus13/ram-session", ENV{UDISKS_IGNORE}="1", '
        'RUN+="/usr/sbin/blockdev --setro %N"\n')


def load():
    if os.geteuid() or not Path('/run/oneplus13/ram-session').is_file():
        raise RuntimeError('Protected RAM session required')
    if Path('/usr/lib/udev/rules.d/10-oneplus13-ram-storage.rules').read_text() != RULE:
        raise RuntimeError('Exact readonly and no-automount block policy required')
    config = json.loads((ROOT/'config.private.json').read_text())
    if config['identity']['kernel_release'] != os.uname().release:
        raise RuntimeError('Kernel release differs')
    state = dict(owned=[], preexisting=[], load_pass=False)
    with STATE.open('x') as stream:
        os.chmod(STATE, 0o600)
        json.dump(state, stream)
    for name in NAMES:
        if (Path('/sys/module')/name).exists():
            state['preexisting'].append(name)
            STATE.write_text(json.dumps(state))
            continue
        relative = 'lib/modules/'+os.uname().release+'/kernel/fs/'+name+'/'+name+'.ko'
        path = Path('/')/relative
        expected = config['module_sha256'].get(relative)
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if not expected or actual != expected:
            raise RuntimeError('Filesystem module bytes differ from exact image')
        # Ownership precedes insertion, including an ambiguous insertion timeout.
        state['owned'].append(name)
        STATE.write_text(json.dumps(state))
        result = subprocess.run(['insmod', str(path)], capture_output=True, timeout=8, check=False)
        if result.returncode or not (Path('/sys/module')/name).is_dir():
            raise RuntimeError('Filesystem module insertion failed')
    state['load_pass'] = True
    STATE.write_text(json.dumps(state))
    return dict(stage='filesystems-load', load_pass=True, owned=state['owned'],
                preexisting=state['preexisting'], module_policy_changed=False)


def restore():
    state = json.loads(STATE.read_text())
    owned = state['owned']
    if (not isinstance(owned, list) or len(owned) != len(set(owned))
            or not set(owned) <= set(NAMES)):
        raise RuntimeError('Filesystem ownership state invalid')
    clean = True
    for name in reversed(owned):
        if (Path('/sys/module')/name).exists():
            try:
                result = subprocess.run(['rmmod', name], capture_output=True, timeout=8, check=False)
                clean &= result.returncode == 0 and not (Path('/sys/module')/name).exists()
            except (OSError, subprocess.TimeoutExpired):
                clean = False
    return dict(stage='filesystems-restore', cleanup_complete=bool(clean),
                owned=owned, module_policy_changed=False)


if __name__ == '__main__':
    stage = sys.argv[1] if len(sys.argv) == 2 else ''
    try:
        result = load() if stage == 'load' else restore() if stage == 'restore' else {}
        if not result:
            raise ValueError('Expected load or restore')
    except Exception as error:
        result = dict(stage=stage, error_class=type(error).__name__, cleanup_complete=False)
    print('RAM_USB_FILESYSTEM_MODULES '+json.dumps(result, sort_keys=True), flush=True)
    raise SystemExit(0 if result.get('load_pass') or result.get('cleanup_complete') else 1)
