#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the phone SAR codec against pinned public vendor wire definitions."""
import argparse, hashlib, json, os, platform, re, subprocess, urllib.request
from pathlib import Path
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function(text, name):
    start = text.index('static QDF_STATUS ' + name + '(')
    body = text.index('{', start)
    level = 1
    end = body + 1
    while level:
        if text[end] == '{': level += 1
        elif text[end] == '}': level -= 1
        end += 1
    return text[start:end]


def structure(text, name):
    end = text.index('} ' + name + ';') + len('} ' + name + ';')
    start = text.rfind('typedef struct {', 0, end)
    if start < 0: raise ValueError('Missing pinned structure: ' + name)
    return text[start:end]


def enum(text, marker):
    # Used on the pinned non-nested enum, excluding commented-out examples.
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    matches = [x.group(0) for x in re.finditer(r'(?:typedef\s+)?enum(?:\s+\w+)?\s*\{[^{}]*\}\s*\w*\s*;', text, re.S) if marker in x.group(0)]
    if len(matches) != 1: raise ValueError('Ambiguous pinned enum: ' + marker)
    return matches[0]


def oracle(refs, license_text):
    api = refs['wmi_unified.h'].read_text()
    tags = refs['wmi_tlv_defs.h'].read_text()
    host = refs['wmi_unified_sta_param.h'].read_text()
    sender = refs['wmi_unified_sta_tlv.c'].read_text()
    parts = [license_text, '#include <stdint.h>\n#include <stdlib.h>\n#include <string.h>\n',
             'typedef uint32_t A_UINT32;\n', enum(tags, 'WMITLV_TAG_LAST_RESERVED'),
             enum(api, 'WMI_SAR_FEATURE_ON_USER_DEFINED')]
    for name in ['wmi_sar_limits_cmd_fixed_param', 'wmi_sar_limit_cmd_row', 'wmi_sar_get_limits_cmd_fixed_param']:
        parts.append(structure(api, name))
    for name in ['sar_limit_cmd_row', 'sar_limit_cmd_params']:
        start = host.index('struct ' + name + ' {')
        end = host.index('};', start) + 2
        parts.append(host[start:end])
    parts.append(r'''
typedef int QDF_STATUS;
typedef void *wmi_unified_t;
typedef unsigned char *wmi_buf_t;
#define QDF_STATUS_SUCCESS 0
#define QDF_STATUS_E_NOMEM 1
#define QDF_IS_STATUS_ERROR(s) ((s) != 0)
#define WMI_TLV_HDR_SIZE 4
#define WMI_SAR_LIMITS_CMDID 0x29006
#define NO_SESSION 0
#define wmi_debug(...) ((void)0)
#define wmi_err(...) ((void)0)
#define wmi_mtrace(...) ((void)0)
#define WMITLV_GET_STRUCT_TLVLEN(t) (sizeof(t) - 4)
#define WMITLV_SET_HDR(p,t,l) (*(uint32_t *)(p) = (uint32_t)(t) << 16 | (uint32_t)(l))
static unsigned char oracle_data[4096];
static size_t oracle_size;
static wmi_buf_t wmi_buf_alloc(void *handle, size_t len)
{ (void)handle; return calloc(1, len); }
static void *wmi_buf_data(wmi_buf_t buf) { return buf; }
static void wmi_buf_free(wmi_buf_t buf) { free(buf); }
static int wmi_unified_cmd_send(void *handle, wmi_buf_t buf, size_t len, unsigned id)
{
    (void)handle;
    if (len > sizeof(oracle_data) || id != WMI_SAR_LIMITS_CMDID) abort();
    memcpy(oracle_data, buf, len); oracle_size = len; free(buf); return 0;
}
''')
    # Preserve the vendor's signed loop counter verbatim. Test inputs are
    # bounded to eight rows; keep strict diagnostics for all new codec code.
    parts.append('#pragma GCC diagnostic push\n#pragma GCC diagnostic ignored "-Wsign-compare"')
    parts.append(function(sender, 'send_sar_limit_cmd_tlv'))
    parts.append('#pragma GCC diagnostic pop')
    return '\n\n'.join(parts) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--references', type=Path, help='Optional local cache of the exact public files')
    a = p.parse_args()
    out = a.output.resolve()
    if out.exists(): raise ValueError('Preserve prior build evidence')
    out.mkdir(parents=True)
    refs_dir = out / 'references'; refs_dir.mkdir()
    lock = json.loads((HERE / 'sources.json').read_text())
    refs = {}
    for name, info in lock['files'].items():
        if Path(name).name != name: raise ValueError('Unsafe reference name')
        cached = a.references / name if a.references else None
        if cached is not None and cached.exists():
            data = cached.read_bytes()
        else:
            req = urllib.request.Request(info['url'], headers={'User-Agent':'Codex-public-WiFi-source-build'})
            with urllib.request.urlopen(req, timeout=40) as response: data = response.read(4 * 1024 * 1024 + 1)
        if len(data) != info['bytes'] or hashlib.sha256(data).hexdigest() != info['sha256']:
            raise ValueError('Pinned source mismatch: ' + name)
        refs[name] = refs_dir / name; refs[name].write_bytes(data)
    # Keep the complete original copyright/license comment in generated code.
    text = refs['wmi_unified_sta_tlv.c'].read_text()
    comments = re.findall(r'/\*.*?\*/', text[:7000], re.S)
    license_text = next(x for x in comments if 'Copyright' in x and ('Permission' in x or 'license' in x.lower()))
    (out / 'vendor-oracle.h').write_text(oracle(refs, license_text))
    log = out / 'build.log'
    counts = {}
    for config, flags in [('release', ['-O2']), ('sanitized', ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer'])]:
        exe = out / ('test-' + config)
        command = ['gcc', '-std=c11', '-Wall', '-Wextra', '-Werror', *flags, '-I'+str(HERE), '-I'+str(out),
                   str(HERE/'sar-wire.c'), str(HERE/'test-sar-wire.c'), '-o', str(exe)]
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1')
        for argv in [command, [str(exe)]]:
            r = subprocess.run(argv, capture_output=True, env=env, timeout=60)
            with log.open('ab') as stream: stream.write(json.dumps(argv).encode()+b'\n'+r.stdout+r.stderr)
            if r.returncode: raise RuntimeError('Offline SAR check failed; see build.log')
        if b'groups=15 vendor_sender_exact_bytes=yes hardware=no' not in r.stdout:
            raise ValueError('Missing actual C test completion')
        counts[config] = 15
    proof = {'scope':'PHONE_WIFI_SAR_CODEC_VENDOR_ORACLE_OFFLINE_ONLY', 'phone_operations':0,
             'machine':platform.machine(), 'test_groups':counts, 'vendor_sender_exact_bytes':True,
             'asan_ubsan_lsan_enabled':True, 'wire_source_commit':lock['commit'],
             'source_files':{n:sha(HERE/n) for n in ['sar-wire.c','sar-wire.h','test-sar-wire.c','check.py','sources.json']},
             'generated_vendor_oracle_sha256':sha(out/'vendor-oracle.h'),
             'driver_integrated':False, 'firmware_or_private_data_included':False,
             'phone_policy_or_rf_acceptance':False, 'hardware_qualified':False}
    (out/'verification.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps(proof,indent=2))


if __name__ == '__main__': main()
