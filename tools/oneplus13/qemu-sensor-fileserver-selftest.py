#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""QEMU-only actual systemd sandbox check; uses a synthetic non-RPC process."""
import importlib.util,json,os,subprocess,time
from pathlib import Path

def main():
    identities=Path('/proc/device-tree/compatible').read_bytes().split(b'\0')
    if b'linux,dummy-virt' not in identities:raise ValueError('Emulator only; no phone execution')
    helper=Path('/run/oneplus13/ram-sensor-fileserver.py')
    spec=importlib.util.spec_from_file_location('fileserver_contract',helper)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert not Path('/dev/fastrpc-adsp-secure').exists()
    assert not Path('/sys/module/fastrpc').exists()
    previous=os.umask(0o077)
    try:
        module.install(Path('/'),{
          'usr/lib/erhai/sensors/bin/qemu-install-probe':(b'PUBLIC_SYNTHETIC_BINARY_PATH',0o644,None),
          'var/lib/erhai/sensors/persist/registry/qemu-install-probe':(b'PUBLIC_SYNTHETIC_REGISTRY',0o640,None)})
    finally:os.umask(previous)
    state=Path(module.STATE);state.mkdir(mode=0o700);os.chown(state,65534,65534)
    # The unchanged command/property builder is exercised with a synthetic
    # executable standing in for strace. This validates sandbox launch and
    # permissions, not ptrace, any ioctl or the real reverse-RPC daemon.
    probe=Path('/run/oneplus13/qemu-fileserver-synthetic-tracer')
    probe.write_text('''#!/usr/bin/python3
import os,time
from pathlib import Path
assert os.getuid()==65534 and os.getgid()==65534
assert b"CapEff:\\t0000000000000000" in Path('/proc/self/status').read_bytes()
assert Path('/usr/lib/erhai/sensors/bin/qemu-install-probe').read_bytes()==b'PUBLIC_SYNTHETIC_BINARY_PATH'
assert Path('/var/lib/erhai/sensors/persist/registry/qemu-install-probe').read_bytes()==b'PUBLIC_SYNTHETIC_REGISTRY'
try:
    Path('/usr/share/oneplus13-sandbox-readonly-check').write_text('unexpected')
except OSError:
    pass
else:
    raise SystemExit('Root was writable inside sandbox')
Path('/run/oneplus13/sensor-fileserver/sandbox-ok').write_text('SYNTHETIC_SANDBOX_OK')
time.sleep(3)
''');probe.chmod(0o755)
    subprocess.run(module.service_command(probe),check=True,timeout=10,capture_output=True)
    until=time.monotonic()+12
    while time.monotonic()<until and not (state/'sandbox-ok').exists():time.sleep(.2)
    assert (state/'sandbox-ok').read_text()=='SYNTHETIC_SANDBOX_OK'
    subprocess.run(['/usr/bin/systemctl','stop',module.UNIT],check=True,timeout=16,capture_output=True)
    facts=dict(line.split('=',1) for line in subprocess.check_output(
        ['/usr/bin/systemctl','show',module.UNIT,'-p','ActiveState','-p','Result','-p','MainPID'],text=True,timeout=5).splitlines())
    assert facts=={'ActiveState':'inactive','Result':'success','MainPID':'0'},facts
    assert not Path('/sys/module/fastrpc').exists() and not Path('/dev/fastrpc-adsp-secure').exists()
    print('ONEPLUS13_SENSOR_FILESERVER_SANDBOX_QEMU_OK',flush=True)
    print(json.dumps({'synthetic_sandbox_only':True,'real_strace_or_rpc_executed':False,
                      'ordinary_uid_no_capabilities':True,'protected_root_write_refused':True,
                      'umask077_new_directories_and_private_mode_read_by_uid65534':True,
                      'bounded_service_cleanly_stopped':True,'phone_operations':0}),flush=True)

if __name__=='__main__':main()
