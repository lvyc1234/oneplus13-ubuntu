#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""RAM-only readonly ioctl fallback, including removable devices without media."""
from array import array
from pathlib import Path
import fcntl,json,os,re,stat,sys

BLKROSET=0x125d
BLKROGET=0x125e


def protect(path,sys_root=Path('/sys/class/block')):
    path=Path(path)
    if path.parent!=Path('/dev') or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*',path.name):
        raise ValueError('Exact block-node basename required')
    entry=sys_root/path.name
    number=(entry/'dev').read_text().strip()
    if not re.fullmatch('[0-9]+:[0-9]+',number):raise ValueError('Invalid kernel device number')
    major,minor=map(int,number.split(':'));expected=os.makedev(major,minor)
    inode=entry.stat().st_ino
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd)
        if not stat.S_ISBLK(before.st_mode) or before.st_rdev!=expected:
            raise RuntimeError('Opened node differs from sysfs block identity')
        value=array('i',[0]);assert value.itemsize==4
        fcntl.ioctl(fd,BLKROGET,value,True)
        initial=value[0]
        if initial not in (0,1):raise RuntimeError('Invalid readonly ioctl result')
        if not initial:fcntl.ioctl(fd,BLKROSET,array('i',[1]),True)
        fcntl.ioctl(fd,BLKROGET,value,True)
        if value[0]!=1:raise RuntimeError('Readonly ioctl did not confirm protection')
        after=os.fstat(fd);node=os.stat(path,follow_symlinks=False)
        if (not stat.S_ISBLK(node.st_mode) or after.st_rdev!=expected or node.st_rdev!=expected
                or node.st_ino!=before.st_ino or entry.stat().st_ino!=inode
                or (entry/'dev').read_text().strip()!=number):
            raise RuntimeError('Block identity changed during readonly protection')
        result=dict(scope='READONLY_NONBLOCK_IOCTL',device=path.name,major_minor=number,
            nonblocking_open=True,descriptor_read_only=True,readonly_before=initial,
            readonly_after=1,set_readonly_requested=not bool(initial),identity_verified=True)
        for name in ['size','removable']:
            try:result[name]=(entry/name).read_text().strip()
            except OSError:result[name]=None
        return result
    finally:os.close(fd)


if __name__=='__main__':
    try:
        if os.geteuid() or len(sys.argv)!=2 or not Path('/run/oneplus13/ram-session').is_file():
            raise RuntimeError('Protected root RAM session required')
        value=protect(sys.argv[1])
        if value['set_readonly_requested']:
            message='READONLY_NONBLOCK_RESULT '+json.dumps(value,sort_keys=True)+'\n'
            log=Path('/run/oneplus13/readonly-block-events.log')
            fd=os.open(log,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            try:os.write(fd,message.encode())
            finally:os.close(fd)
            print(message,end='',flush=True)
    except Exception as error:
        print('READONLY_NONBLOCK_ERROR '+json.dumps(dict(error=type(error).__name__,errno=getattr(error,'errno',None))),file=sys.stderr,flush=True)
        raise SystemExit(1)
