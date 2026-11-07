#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Keep unreviewed peripherals manual when matching modules are preinstalled.

This is a RAM bring-up policy, not a hardware driver allowlist or a sandbox.
Built-in drivers need separate device-tree review. Display retains its existing
explicit activation policy. No driver is loaded by this module.
"""
from pathlib import PurePosixPath
import re

PREFIXES=('kernel/sound/', 'kernel/drivers/net/wireless/',
          'kernel/drivers/power/sequencing/', 'kernel/drivers/pci/pwrctrl/',
          'kernel/drivers/media/', 'kernel/drivers/nfc/', 'kernel/net/nfc/')
EXACT={'kernel/drivers/misc/fastrpc.ko',
       'kernel/drivers/i2c/busses/i2c-qcom-cci.ko',
       'kernel/drivers/clk/qcom/camcc-sm8750.ko',
       'kernel/drivers/clk/qcom/cambistmclkcc-sm8750.ko',
       'kernel/drivers/phy/qualcomm/phy-qcom-mipi-csi2.ko'}

def blocked_modules(paths, release):
    if not re.fullmatch(r'[A-Za-z0-9._+-]{1,128}',release):
        raise ValueError('Invalid module release')
    prefix='lib/modules/'+release+'/'
    selected=set()
    for path in paths:
        name=path.removeprefix('./')
        p=PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts:
            raise ValueError('Module archive path escapes root')
        if not name.endswith('.ko'):
            continue
        if not name.startswith(prefix):
            raise ValueError('Module from another kernel release')
        relative=name[len(prefix):]
        if relative.startswith(PREFIXES) or relative in EXACT:
            stem=PurePosixPath(relative).stem.replace('-','_')
            if not re.fullmatch(r'[A-Za-z0-9_]+',stem):
                raise ValueError('Invalid kernel module name')
            selected.add(stem)
    return sorted(selected)

def configuration(modules):
    if not modules or len(set(modules))!=len(modules):
        raise ValueError('A nonempty unique module set is required')
    lines=['# RAM staging: unreviewed peripheral probing requires separate review.',
           '# Built-in drivers are governed separately by the reviewed DT.']
    for module in sorted(modules):
        if not re.fullmatch(r'[A-Za-z0-9_]+',module):
            raise ValueError('Invalid module name')
        lines.extend(['blacklist '+module,'install '+module+' /bin/false'])
    return '\n'.join(lines)+'\n'

def dependency_closure(selected,depfile):
    graph={}
    for line in depfile.splitlines():
        if not line:continue
        if line.count(':')!=1:raise ValueError('Malformed module dependency line')
        path,deps=line.split(':')
        paths=[path,*deps.split()]
        for name in paths:
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or not name.startswith('kernel/') or not name.endswith('.ko'):
                raise ValueError('Unsafe module dependency path')
        name=PurePosixPath(path).stem.replace('-','_')
        if name in graph:raise ValueError('Duplicate normalized module name')
        graph[name]={PurePosixPath(x).stem.replace('-','_') for x in deps.split()}
    pending=list(selected);result=set()
    while pending:
        name=pending.pop()
        if name in result:continue
        if name not in graph:raise ValueError('Incomplete module dependency graph')
        result.add(name);pending.extend(graph[name]-result)
    return sorted(result)
