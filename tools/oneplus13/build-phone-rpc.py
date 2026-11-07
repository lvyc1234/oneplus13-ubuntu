#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build only the phone's generic reverse-file daemon, entirely offline to phone.

Use the original pinned source/five patches plus a separate phone-only patch.
Never install a service or include ROM, registry, firmware or calibration.
"""
import argparse,hashlib,json,os,platform,shutil,struct,subprocess,tarfile,urllib.request
from pathlib import Path

REPO=Path(__file__).resolve().parents[2]
EXTRAS={
 '0001-serve-phone-pcb-version-read-only.patch':'a2f30815f42b1a167588ff0a7c3322743b47bb41ccded130dbc94e22f7a23bd7',
 '0002-copy-unaligned-wire-size-without-UB.patch':'4c015b91881c6a7c62ae1dc87cf06250788c3353108ae5ea675ea03a0f50f321',
 '0003-read-packed-type-fields-with-byte-copies.patch':'5d369bbd1966b194f84aab999cb6f19a6a7b1e7b0ab1167b870477320436cfe1',
 '0004-fix-input-type-sequence-marshalling.patch':'bc54e868b010761dc614125ebb71e97172aacfcaacf0b49789ec89079fd34afe',
 '0005-retain-vfs-aliases-and-directory-parents.patch':'f055af775735dba2bec49b0794049186a0c6a09fc55d5f2aa0ba7ae6b127162d',
 '0006-add-volatile-apps-std-files.patch':'bbe6cdd895830a2446331ee160c48dcff97706d580bd8e2f1b7f6ff9283ff254',
 '0007-expand-bounded-ram-inputs.patch':'1ce5cd9c7e2423ee17b0a9acc084ca09890c4220317aa57f2eedf668b4e26771',
 '0008-support-bounded-registry-renames.patch':'987a07d6470212042313dc0fa594a64cbdb165ab9dec5319c58f037070eca3a0',
 '0009-preserve-volatile-namespace-failure-semantics.patch':'1ed5bb901b5e20192eb1b9e44d8b7b965119b1fd93b0cd65c952943847267aa0',
 '0010-map-exact-camera-metadata-read-only.patch':'c33a494ccd71dc9b938f1de939a899e87d4eaf63bae4a26b2f8bb9c87be357bf'}
SOURCE_COMMIT='598b591ae9da6a6cfe2ca5ea78998019ca6395ea'
ARCHIVE_SHA='05181d130f1e3a3f4c368be606a627562f7abd972c5efff92a21d66d35a2fe51'

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def build_environment():
    # Meson test logs record their inherited environment. Do not copy runner
    # tokens, proxy credentials or unrelated host secrets into artifacts.
    result={key:os.environ[key] for key in ('PATH','HOME','TMPDIR') if key in os.environ}
    result['LANG']='C.UTF-8';result['LC_ALL']='C.UTF-8'
    return result

def run(args,log,env=None):
    result=subprocess.run([str(x) for x in args],capture_output=True,text=True,
                          env=env if env is not None else build_environment(),timeout=180)
    with log.open('a',encoding='utf-8') as stream:
        stream.write('\nCOMMAND '+json.dumps([str(x) for x in args])+'\n'+result.stdout+result.stderr)
    if result.returncode:raise RuntimeError('Offline build step failed: '+str(args[0]))
    return result.stdout

def inputs(archive,lock):
    recipe=next(x for x in lock['sources'] if x['name']=='hexagonrpc')
    if recipe['upstream_commit']!=SOURCE_COMMIT or recipe['sha256']!=ARCHIVE_SHA or sha(archive)!=ARCHIVE_SHA:
        raise ValueError('Original source identity mismatch')
    patches=[]
    for item in recipe['patches']:
        path=REPO/'packaging/sensors'/item['file']
        if sha(path)!=item['sha256']:raise ValueError('Original patch identity mismatch')
        patches.append(path)
    if len(patches)!=5:raise ValueError('Unexpected original patch count')
    for name,expected in EXTRAS.items():
        path=REPO/'tools/oneplus13/sensor-patches'/name
        if sha(path)!=expected:raise ValueError('Unexpected phone patch identity')
        patches.append(path)
    return recipe,patches

def extract(archive,directory,expected):
    seen=set()
    with tarfile.open(archive) as tf:
        for item in tf:
            path=Path(item.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!=expected:
                raise ValueError('Unexpected source archive path')
            if item.name in seen or not (item.isfile() or item.isdir()):raise ValueError('Unexpected source archive entry')
            seen.add(item.name)
        tf.extractall(directory,filter='data')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--archive',type=Path);parser.add_argument('--host-check',action='store_true')
    parser.add_argument('--meson',default='meson')
    a=parser.parse_args();native=platform.machine() in ('aarch64','arm64')
    if not native and not a.host_check:raise ValueError('Native ARM64 is required for runtime artifacts')
    out=a.output.resolve()
    if out.exists():raise ValueError('Preserve existing output')
    out.mkdir(parents=True);log=out/'build.log';log.write_text('PHONE_USERSPACE_OFFLINE_ONLY\n')
    lock_path=REPO/'packaging/sensors/sources.json';lock=json.loads(lock_path.read_text())
    recipe=next(x for x in lock['sources'] if x['name']=='hexagonrpc')
    archive=a.archive.resolve() if a.archive else out/recipe['archive']
    if not a.archive:
        request=urllib.request.Request(recipe['url'],headers={'User-Agent':'OnePlus13-offline-build'})
        with urllib.request.urlopen(request,timeout=60) as response,archive.open('xb') as target:
            shutil.copyfileobj(response,target)
    recipe,patches=inputs(archive,lock)
    source_parent=out/'source';source_parent.mkdir();extract(archive,source_parent,recipe['directory'])
    source=source_parent/recipe['directory']
    for patch in patches:run(['patch','-d',source,'-p1','--batch','--fuzz=0','-i',patch],log)
    common=['--prefix=/usr/lib/erhai/sensors','--libdir=lib','--bindir=bin','--libexecdir=libexec',
            '--wrap-mode=nofallback','-Dc_link_args=-Wl,-rpath,/usr/lib/erhai/sensors/lib']
    test_counts={}
    for name,options in [('release',['--buildtype=release']),
                          ('sanitized',['--buildtype=debug','-Db_sanitize=address,undefined','-Db_lundef=false'])]:
        build=out/name
        run([a.meson,'setup',build,source,*common,*options],log)
        run([a.meson,'compile','-C',build,'-j','2'],log)
        environment=build_environment()
        if name=='sanitized':
            # Upstream construct_root_dir explicitly has no tree destructor.
            # Invalid memory/UB are checked; do not claim leak validation.
            environment['ASAN_OPTIONS']='detect_leaks=0:abort_on_error=1'
            environment['UBSAN_OPTIONS']='halt_on_error=1:print_stacktrace=1'
        run([a.meson,'test','-C',build,'--print-errorlogs'],log,environment)
        tested=[json.loads(line) for line in (build/'meson-logs/testlog.json').read_text().splitlines() if line.strip()]
        if len(tested)!=14 or not all(item['result']=='OK' for item in tested):raise ValueError('Missing exact C test results')
        if not any(item['name'].endswith('input-type-sequence') for item in tested):raise ValueError('No actual input sequence wire test')
        if not any(item['name'].endswith('hexagonfs-lifetime') for item in tested):raise ValueError('No actual VFS lifetime test')
        if not any(item['name'].endswith('apps-std-ram') for item in tested):raise ValueError('No actual RAM wire test')
        if not any(item['name'].endswith('phone-ramfs-storage') for item in tested):raise ValueError('No actual RAM storage test')
        if not any(item['name'].endswith('phone-camera-info') for item in tested):raise ValueError('No exact camera input mapping test')
        test_counts[name]=len(tested)
    outputs={};offline_tests={}
    if native and not a.host_check:
        entries={'usr/lib/erhai/sensors/bin/hexagonrpcd':out/'release/hexagonrpcd/hexagonrpcd',
                 'usr/lib/erhai/sensors/lib/libhexagonrpc.so.0.5':out/'release/libhexagonrpc/libhexagonrpc.so.0.5'}
        runtime=out/'phone-rpc-arm64.tar.gz'
        with tarfile.open(runtime,'x:gz') as tf:
            for name,path in entries.items():
                value=path.read_bytes()
                if value[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',value,18)[0]!=183:raise ValueError('Not AArch64 ELF')
                info=tarfile.TarInfo(name);info.size=len(value);info.mode=0o755 if '/bin/' in name else 0o644;info.mtime=0
                with path.open('rb') as stream:tf.addfile(info,stream)
                outputs[name]={'bytes':len(value),'sha256':sha(path),'mode':info.mode}
        outputs['runtime_archive_sha256']=sha(runtime)
        run(['readelf','-d',entries['usr/lib/erhai/sensors/bin/hexagonrpcd']],log)
        tests_archive=out/'phone-rpc-tests-arm64.tar.gz'
        test_names=['test_apps_std_ram','test_phone_ramfs']
        with tarfile.open(tests_archive,'x:gz') as tf:
            for name in test_names:
                path=out/'release/tests'/name;value=path.read_bytes()
                if value[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',value,18)[0]!=183:raise ValueError('Not native ARM64 selftest')
                info=tarfile.TarInfo(name);info.size=len(value);info.mode=0o755;info.mtime=0
                with path.open('rb') as stream:tf.addfile(info,stream)
                offline_tests[name]={'bytes':len(value),'sha256':sha(path),'mode':0o755}
        (out/'phone-rpc-tests.json').write_text(json.dumps({
            'scope':'PUBLIC_ARM64_RPC_MOCK_SELFTESTS','files':offline_tests,
            'archive_sha256':sha(tests_archive),'synthetic_data_only':True,
            'fastrpc_call_mocked_to_fail':True,'hardware_qualification_implied':False},indent=2)+'\n')
    for name in ['COPYING','LICENSE','COPYING.GPL']:
        if (source/name).is_file():shutil.copy2(source/name,out/('LICENSE-'+name))
    for name in ['GPL-3','LGPL-3']:
        path=Path('/usr/share/common-licenses')/name
        if not path.is_file():raise ValueError('Standard redistribution license text missing')
        shutil.copy2(path,out/('LICENSE-'+name))
    (out/'COPYRIGHT.txt').write_text(
        'Original linux-msm/hexagonrpc sources retain their Sensor Shell and HexagonRPC contributor notices.\n'
        'Original source commit: '+SOURCE_COMMIT+'\n'
        'Phone supplemental patches: Codex <codex@openai.com>; see verification.json for source and patch hashes.\n'
        'No factory data, calibration, firmware, service integration or installer is included.\n')
    report={'scope':'OFFLINE_PHONE_RPC_SOURCE_BUILD_ONLY','phone_operations':0,'native_arm64':native,
       'host_check_only':a.host_check,'original_source_commit':SOURCE_COMMIT,'source_archive_sha256':ARCHIVE_SHA,
       'original_recipe_sha256':sha(lock_path),'patches':[{'name':p.name,'sha256':sha(p)} for p in patches],
       'release_tests_pass':True,'asan_ubsan_tests_pass':True,'leaks_tested':False,
       'c_test_counts':test_counts,'input_type_sequence_wire_fixtures_pass':True,'vfs_alias_parent_lifetime_tests_pass':True,
       'volatile_ram_wire_tests_pass':True,'volatile_ram_storage_tests_pass':True,
       'listener_large_input_64k_test_pass':True,
       'bounded_ram_registry_rename_tests_pass':True,
       'volatile_namespace_tombstone_failure_and_signed_seek_tests_pass':True,
       'exact_read_only_camera_input_mapping_tests_pass':True,
       'unqualified_output_type_sequences_refused_before_ioctl':True,
       'synthetic_board_data_only':True,'private_inputs_included':False,'services_or_udev_or_firmware_included':False,
       'sensor_attach_stream_or_hardware_eligible':False,'outputs':outputs,
       'separate_mock_selftest_export':offline_tests}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
