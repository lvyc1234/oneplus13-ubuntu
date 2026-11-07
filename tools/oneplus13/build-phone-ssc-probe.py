#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build isolated metadata and bounded sample probes, never a sensor service."""
import argparse,hashlib,json,os,platform,re,shutil,struct,subprocess,tarfile
from pathlib import Path
REPO=Path(__file__).resolve().parents[2]
ARCHIVE_SHA='716d6bd6b34d2d753060c6b54c9a87e34fae75b724c763bf9ef487efa3621587'
SOURCE_COMMIT='0cf77b93b55752da34dca2dcecc06fca8665184b'
BASE_PATCHES=(
    '0001-libssc-select-the-delivery-and-batching-of-streams-at-run-time.patch',
    '0002-libssc-choose-the-stream-delivery-by-sensor-type.patch',
)
LIGHT_PATCH='0004-bounded-phone-light-lookup.patch'
LIGHT_SHA='a8e8aa403227d4a76ea0e0299bc43884d04b2ff553c5b1e40db499b4247e07a7'
DIAGNOSTIC_PATCH='0005-record-bounded-discovery-stages.patch'
DIAGNOSTIC_SHA='09328d2f337027a3c8e2d186fdfc162e2125010d43b51c3967edf78b723c4f1d'
SAMPLE_PATCH='0006-bounded-three-vector-sampling.patch'
SAMPLE_SHA='6e72336db4239e97869ff22b723959df6c16386f07185939f3bbb8d7ef884d3c'
EXTRA='0001-add-metadata-only-discovery-guard.patch'
EXTRA_SHA='d2c913814a2011fe5e5009c065d45423b6e43c595f538f0249b6fb066dea7cee'
PROPERTY_PATCH='0002-fix-sensor-property-value-types.patch'
PROPERTY_SHA='7ed350b5a98f8e6c6e04e707746beafe8337ad0daf3d596a55f92c35b72e7f1c'
MATRIX_PATCH='0003-preserve-nonzero-mount-matrices.patch'
MATRIX_SHA='58e297539e90562165e12fe5c6924ae7f65ce062eb71e82759881fbc4b2f02d3'
RUNTIME_LIBRARIES={'libprotobuf-c.so.1','libqmi-glib.so.5','libqrtr-glib.so.0','libmbim-glib.so.4'}

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def environment():
    env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR') if k in os.environ}
    env.update(LANG='C.UTF-8',LC_ALL='C.UTF-8');return env
def run(args,log,env=None):
    r=subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=240,env=env or environment())
    with log.open('a',encoding='utf-8') as stream:stream.write('\n'+json.dumps([str(x) for x in args])+'\n'+r.stdout+r.stderr)
    if r.returncode:raise RuntimeError('SSC offline build/check failed: '+str(args[0]))
    return r.stdout
def main():
    from verified_download import fetch
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--archive',type=Path);p.add_argument('--meson',default='meson');p.add_argument('--host-check',action='store_true')
    a=p.parse_args();native=platform.machine() in ('aarch64','arm64')
    if not native and not a.host_check:raise ValueError('Native ARM64 required for artifacts')
    out=a.output.resolve()
    if out.exists():raise ValueError('Preserve previous build output')
    out.mkdir(parents=True);log=out/'build.log';log.write_text('PHONE_SSC_METADATA_PROBE_OFFLINE_ONLY\n')
    integration_commit=run(['git','-C',REPO,'rev-parse','HEAD'],log).strip()
    if not re.fullmatch(r'[0-9a-f]{40}',integration_commit):raise ValueError('Missing integration identity')
    lock=json.loads((REPO/'packaging/sensors/sources.json').read_text())
    entry=next(x for x in lock['sources'] if x['name']=='libssc')
    if (entry['sha256']!=ARCHIVE_SHA or entry['upstream_commit']!=SOURCE_COMMIT or
        tuple(Path(item['file']).name for item in entry['patches'])!=BASE_PATCHES):
        raise ValueError('Original recipe changed')
    archive=a.archive.resolve() if a.archive else out/entry['archive']
    if not a.archive:
        fetch(entry['mirror_url'].replace('http:','https:',1),ARCHIVE_SHA,archive)
    if sha(archive)!=ARCHIVE_SHA:raise ValueError('Original source hash changed')
    parent=out/'source';parent.mkdir()
    with tarfile.open(archive) as tf:
        names=set()
        for member in tf:
            path=Path(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='libssc' or member.name in names or not(member.isfile() or member.isdir()):raise ValueError('Unsafe source archive')
            names.add(member.name)
        tf.extractall(parent,filter='data')
    source=parent/'libssc';patches=[]
    for item in entry['patches']:
        path=REPO/'packaging/sensors'/item['file']
        if sha(path)!=item['sha256']:raise ValueError('Original patch changed')
        patches.append(path)
    light=REPO/'tools/oneplus13/ssc-patches'/LIGHT_PATCH
    if sha(light)!=LIGHT_SHA:raise ValueError('Phone light overlay changed')
    patches.append(light)
    extra=REPO/'tools/oneplus13/ssc-patches'/EXTRA
    if sha(extra)!=EXTRA_SHA:raise ValueError('Phone overlay changed')
    patches.append(extra)
    properties=REPO/'tools/oneplus13/ssc-patches'/PROPERTY_PATCH
    if sha(properties)!=PROPERTY_SHA:raise ValueError('Phone property correction changed')
    patches.append(properties)
    matrix=REPO/'tools/oneplus13/ssc-patches'/MATRIX_PATCH
    if sha(matrix)!=MATRIX_SHA:raise ValueError('Phone matrix correction changed')
    patches.append(matrix)
    diagnostic=REPO/'tools/oneplus13/ssc-patches'/DIAGNOSTIC_PATCH
    if sha(diagnostic)!=DIAGNOSTIC_SHA:raise ValueError('Phone diagnostics overlay changed')
    patches.append(diagnostic)
    sample=REPO/'tools/oneplus13/ssc-patches'/SAMPLE_PATCH
    if sha(sample)!=SAMPLE_SHA:raise ValueError('Phone sample diagnostic changed')
    patches.append(sample)
    for patch in patches:run(['patch','-d',source,'-p1','--batch','--fuzz=0','-i',patch],log)
    counts={}
    for kind,options in [('release',['--buildtype=release']),('sanitized',['--buildtype=debug','-Db_sanitize=address,undefined','-Db_lundef=false'])]:
        build=out/kind
        run([a.meson,'setup',build,source,'--wrap-mode=nofallback',*options],log)
        run([a.meson,'compile','-C',build,'-j','2'],log)
        env=environment();env['ASAN_OPTIONS']='detect_leaks=0:abort_on_error=1';env['UBSAN_OPTIONS']='halt_on_error=1:print_stacktrace=1'
        run([a.meson,'test','-C',build,'--setup','phoneoffline','--suite','phone-probe','--suite','delivery','--print-errorlogs'],log,env)
        tests=[json.loads(line) for line in (build/'meson-logs/testlog-phoneoffline.json').read_text().splitlines() if line.strip()]
        if len(tests)!=5 or not all(x['result']=='OK' for x in tests):raise ValueError('Missing actual guard/property/encoding/sample results')
        names={x['name'].split(':')[-1] for x in tests}
        if names!={'phone-probe-policy','phone-probe-send-guard','phone-sensor-properties','tests-delivery','phone-sample-tests'}:raise ValueError('Wrong tested scope')
        counts[kind]=len(tests)
        answer=run([build/'src/phone-ssc-metadata-probe','--verify-lock'],log,env)
        if 'SSC_PHONE_METADATA_LOCK_VERIFIED_NO_QRTR' not in answer:raise ValueError('Probe policy not linked')
    probe=out/'release/src/phone-ssc-metadata-probe'
    symbols=run(['readelf','-Ws',probe],log)
    imported=[x.split()[-1].split('@')[0] for x in symbols.splitlines() if len(x.split())>=8 and x.split()[6]=='UND']
    if any('ssc_sensor_' in name and ('_open' in name or '_close' in name) for name in imported):raise ValueError('Probe imports measurement APIs')
    if not {'ssc_sensor_new','ssc_phone_lock_metadata_only','ssc_phone_metadata_only_locked'}<=set(imported):raise ValueError('Probe lacks guarded API imports')
    files={};runtime_libraries={}
    if native and not a.host_check:
        entries={'probe/phone-ssc-metadata-probe':probe,'probe/libssc.so.2':out/'release/src/libssc.so.2',
                 'tests/phone-probe-policy':out/'release/tests/phone-probe-policy',
                 'tests/phone-probe-send-guard':out/'release/tests/phone-probe-send-guard'}
        linked=run(['ldd',probe],log)
        paths={match[1]:Path(match[2]).resolve() for match in re.finditer(r'^\s*(\S+) => (/\S+) ',linked,re.M)}
        if not RUNTIME_LIBRARIES<=set(paths):raise ValueError('Missing reviewed SSC dependency closure')
        for name in sorted(RUNTIME_LIBRARIES):
            path=paths[name];entries['probe/'+name]=path
            owners=run(['dpkg-query','-S',path],log).strip().splitlines()
            if len(owners)!=1:raise ValueError('Ambiguous runtime package owner')
            package=owners[0].rsplit(': ',1)[0]
            record=run(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n',package],log).strip().split('\t')
            if len(record)!=4:raise ValueError('Missing corresponding source package identity')
            copyright_path=Path('/usr/share/doc')/package.split(':')[0]/'copyright'
            target=out/('LICENSE-runtime-'+name);shutil.copy2(copyright_path,target)
            runtime_libraries[name]={'binary_package':record[0],'binary_version':record[1],
               'source_package':record[2],'source_version':record[3],
               'source_url':'https://launchpad.net/ubuntu/+source/'+record[2]+'/'+record[3],
               'copyright_file':target.name,'copyright_sha256':sha(target),'library_sha256':sha(path)}
        archive_out=out/'phone-ssc-probe-arm64.tar.gz'
        with tarfile.open(archive_out,'x:gz') as tf:
            for name,path in entries.items():
                data=path.read_bytes()
                if data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183:raise ValueError('Not AArch64 ELF')
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o755;info.mtime=0
                with path.open('rb') as stream:tf.addfile(info,stream)
                files[name]={'bytes':len(data),'sha256':sha(path),'mode':info.mode}
        (out/'phone-ssc-probe-manifest.json').write_text(json.dumps({'scope':'PUBLIC_PHONE_SSC_METADATA_ONLY_PROBE',
           'files':files,'archive_sha256':sha(archive_out),'samples_or_streams_allowed':False,
           'integration_commit':integration_commit,
           'one_way_send_guard':True,'private_inputs_included':False,'hardware_qualified':False,
           'runtime_libraries':runtime_libraries,'runtime_library_namespace':'probe directory only'},indent=2)+'\n')
        # A separate archive and scope keep the metadata executable incapable
        # of upgrading itself to a stream. The sampler links private objects.
        sample_entries={'probe/phone-ssc-sample-probe':out/'release/src/phone-ssc-sample-probe',
                        'tests/phone-sample-tests':out/'release/src/phone-sample-tests'}
        sample_entries.update({name:path for name,path in entries.items() if Path(name).name in RUNTIME_LIBRARIES})
        sample_files={};sample_archive=out/'phone-ssc-samples-arm64.tar.gz'
        with tarfile.open(sample_archive,'x:gz') as tf:
            for name,path in sample_entries.items():
                data=path.read_bytes()
                if data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183:raise ValueError('Sample is not AArch64 ELF')
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o755;info.mtime=0
                with path.open('rb') as stream:tf.addfile(info,stream)
                sample_files[name]={'bytes':len(data),'sha256':sha(path),'mode':info.mode}
        (out/'phone-ssc-samples-manifest.json').write_text(json.dumps({
            'scope':'PUBLIC_PHONE_SSC_BOUNDED_THREE_VECTOR_PROBE','files':sample_files,
            'archive_sha256':sha(sample_archive),'integration_commit':integration_commit,
            'rate_hz':5,'collection_ms':3000,'maximum_uids':3,'one_way_send_guard':True,
            'metadata_executable_can_upgrade':False,'private_inputs_included':False,
            'hardware_qualified':False,'runtime_libraries':runtime_libraries},indent=2)+'\n')
    for name in ['COPYING','LICENSE']:
        if (source/name).is_file():shutil.copy2(source/name,out/('LICENSE-'+name))
    shutil.copy2('/usr/share/common-licenses/GPL-3',out/'LICENSE-GPL-3')
    (out/'COPYRIGHT.txt').write_text('Original libssc 0.4.4 retains Dylan Van Assche and original contributor notices.\nPhone-only metadata guard/probe: Codex <codex@openai.com>, GPL-3.0-or-later.\nSource commit '+SOURCE_COMMIT+'; original recipe patches and phone overlay recorded in verification.json.\nNo firmware, calibration, registry, service integration or installer included.\n')
    result={'scope':'PHONE_SSC_ISOLATED_DIAGNOSTICS_OFFLINE_BUILD','phone_operations':0,'native_arm64':native,
        'host_check_only':a.host_check,'source_commit':SOURCE_COMMIT,'source_archive_sha256':ARCHIVE_SHA,
        'integration_commit':integration_commit,
        'patches':[{'name':x.name,'sha256':sha(x)} for x in patches],
        'test_counts':counts,'one_way_metadata_policy_and_real_send_guard_tests_pass':True,
        'original_delivery_encoding_regression_pass':True,'typed_sensor_properties_regression_pass':True,'actual_attribute_matrix_regression_pass':True,'original_qrtr_service_tests_run':False,
        'no_measurement_api_imported_by_probe':True,'lock_only_cli_pass':True,'leak_testing_claimed':False,
        'private_inputs_included':False,'services_or_installers_included':False,'hardware_eligible':False,
        'bounded_three_vector_sample_policy_and_decoder_tests_pass':True,
        'artifact_files':files,'runtime_libraries':runtime_libraries}
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()



