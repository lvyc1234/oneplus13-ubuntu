#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the pinned kernel's real DSC PPS packer against its real structure.

Host-only, public inputs only. This checks C field names/layout and all packet
bytes independently of Python's packer. It does not validate display hardware.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from dsc import decode_pps, c_initializer
from panel import vendor_pps


def extract_function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace+1
    while depth and end < len(source):
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    if depth:
        raise ValueError('Unterminated C function')
    return source[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--sanitize', action='store_true')
    p.add_argument('--keep-source', type=Path, help='Optional host inspection C source')
    p.add_argument('--dsi-source', type=Path, help='Patched DSI host (defaults to build working tree)')
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    pin = json.loads((repo / 'devices/oneplus13/source.json').read_text())['kernel']['commit']
    actual = subprocess.check_output(['git', '-C', str(a.kernel_source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != pin:
        raise ValueError('DSC reference source commit differs from the pinned kernel')
    fixture = json.loads((repo / 'devices/oneplus13/panel-aa569-120hz.json').read_text())
    pps = vendor_pps(fixture['on_packets'])
    cfg = decode_pps(pps)
    def kernel_file(name):
        return subprocess.check_output(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-C', str(a.kernel_source),
                                        'show', f'{pin}:{name}']).decode('utf-8')
    header = kernel_file('include/drm/display/drm_dsc.h')
    source = kernel_file('drivers/gpu/drm/display/drm_dsc_helper.c')
    function = extract_function(source, 'void drm_dsc_pps_payload_pack(')
    dsi_name = 'drivers/gpu/drm/msm/dsi/dsi_host.c'
    baseline_dsi = kernel_file(dsi_name)
    patched_dsi = (a.dsi_source or a.kernel_source / dsi_name).read_text(encoding='utf-8')
    helper_header = kernel_file('include/drm/display/drm_dsc_helper.h')
    enum_start = helper_header.index('enum drm_dsc_params_type {')
    enum_end = helper_header.index('};', enum_start)+2
    enum = helper_header[enum_start:enum_end]
    tables = source[source.index('struct rc_parameters {'):
                    source.index('/**\n * drm_dsc_setup_rc_params()')]
    threshold_start = source.index('static const u16 drm_dsc_rc_buf_thresh[]')
    threshold = source[threshold_start:source.index('};', threshold_start)+2]
    helpers = '\n'.join(extract_function(source, signature) for signature in [
        'void drm_dsc_set_const_params(', 'void drm_dsc_set_rc_buf_thresh(',
        'int drm_dsc_setup_rc_params(', 'int drm_dsc_compute_rc_parameters(',
        'u8 drm_dsc_initial_scale_value(', 'u32 drm_dsc_get_bpp_int('])
    dsi_signature = 'static int dsi_populate_dsc_params(struct msm_dsi_host *msm_host, struct drm_dsc_config *dsc)\n'
    def dsi_functions(text):
        return (extract_function(text, 'static u8 dsi_dsc_1_2_first_line_bpg_offset(') +
                extract_function(text, dsi_signature))
    patched_functions = dsi_functions(patched_dsi)
    baseline_functions = dsi_functions(baseline_dsi).replace(
        'dsi_dsc_1_2_first_line_bpg_offset', 'baseline_first_line_bpg_offset').replace(
        'dsi_populate_dsc_params', 'baseline_populate_dsc_params')
    # Replace only the transitive DP header. Keep the complete original DSC
    # structs/constants; a tiny DP packet definition satisfies an unused type.
    original_header_sha = hashlib.sha256(header.encode()).hexdigest()
    header = header.replace('#include <drm/display/drm_dp.h>', '')
    declarations = '''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
typedef uint16_t __be16;
#define __packed __attribute__((packed))
#define BUILD_BUG_ON(c) _Static_assert(!(c), "PPS structure layout changed")
#define DP_SDP_PPS_HEADER_PAYLOAD_BYTES_MINUS_1 127
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define min(a,b) ((a)<(b)?(a):(b))
static int host_warn_once(int condition) { return condition; }
#define WARN_ON_ONCE(c) host_warn_once(!!(c))
#define DRM_DEBUG_KMS(...) do {} while(0)
#define DRM_DEV_ERROR(dev,...) do {(void)(dev);} while(0)
#ifndef EOPNOTSUPP
#define EOPNOTSUPP 95
#endif
struct platform_device { int dev; };
struct msm_dsi_host { struct platform_device *pdev; };
struct dp_sdp_header { u8 bytes[4]; };
static inline uint16_t cpu_to_be16(uint16_t x) {
  const uint16_t little = 1;
  return *(const unsigned char *)&little ? (uint16_t)((x << 8) | (x >> 8)) : x;
}
'''
    main_c = '''
int main(void) {
  struct drm_dsc_picture_parameter_set pps;
  struct drm_dsc_config wire = aa569_dsc;
  /* DPU/MSM retain physical bpp; the PPS packer expects encoded group bpp. */
  wire.bits_per_pixel *= 2;
  drm_dsc_pps_payload_pack(&pps, &wire);
  const unsigned char *bytes = (const unsigned char *)&pps;
  for (unsigned int i=0;i<sizeof(pps);i++) printf("%02x",bytes[i]);
  putchar('\n');
  drm_dsc_pps_payload_pack(&pps, &aa569_dsc);
  for (unsigned int i=0;i<sizeof(pps);i++) printf("%02x",bytes[i]);
  putchar('\n');
  /* Rebuild all RC fields from a minimal panel configuration. */
  struct drm_dsc_config minimal = {
    .dsc_version_major=1,.dsc_version_minor=2,.bits_per_component=10,
    .bits_per_pixel=128,.slice_width=720,.slice_height=22,.slice_count=2,
    .pic_width=1440,.pic_height=3168,.native_422=true,.block_pred_enable=true
  };
  struct platform_device pdev={0}; struct msm_dsi_host host={.pdev=&pdev};
  struct drm_dsc_config generated=minimal, baseline=minimal;
  if(dsi_populate_dsc_params(&host,&generated) || generated.bits_per_pixel!=128 ||
     generated.convert_rgb || !generated.native_422) return 2;
  generated.bits_per_pixel *= 2;
  drm_dsc_pps_payload_pack(&pps,&generated);
  for (unsigned int i=0;i<sizeof(pps);i++) printf("%02x",bytes[i]);
  putchar('\n');
  if(baseline_populate_dsc_params(&host,&baseline)) return 3;
  baseline.bits_per_pixel *= 2;
  drm_dsc_pps_payload_pack(&pps,&baseline);
  for (unsigned int i=0;i<sizeof(pps);i++) printf("%02x",bytes[i]);
  putchar('\n');
  /* The RGB route must remain byte-identical to the pinned baseline. */
  minimal.native_422=false; generated=minimal; baseline=minimal;
  struct drm_dsc_picture_parameter_set rgb_baseline;
  if(dsi_populate_dsc_params(&host,&generated) ||
     baseline_populate_dsc_params(&host,&baseline)) return 4;
  drm_dsc_pps_payload_pack(&pps,&generated);
  drm_dsc_pps_payload_pack(&rgb_baseline,&baseline);
  if(memcmp(&pps,&rgb_baseline,sizeof(pps))) return 5;
  minimal.native_422=true;
  for(int i=0;i<4;i++) {
    struct drm_dsc_config bad=minimal;
    if(i==0) bad.dsc_version_minor=1;
    if(i==1) bad.native_420=true;
    if(i==2) bad.slice_width=719;
    if(i==3) bad.slice_height=1;
    if(dsi_populate_dsc_params(&host,&bad)!=-EINVAL) return 6;
  }
  return 0;
}
'''.replace("putchar('\n')", "putchar('\\n')")
    with tempfile.TemporaryDirectory(prefix='oneplus13-pps-') as tmp:
        c_file, exe = Path(tmp) / 'pps.c', Path(tmp) / 'pps-test.exe'
        combined = (declarations+header+enum+tables+threshold+helpers+function+
                    patched_functions+baseline_functions+c_initializer(cfg)+main_c)
        c_file.write_text(combined, encoding='utf-8')
        if a.keep_source:
            a.keep_source.write_text(combined, encoding='utf-8')
        command = ['gcc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare']
        if sys.platform == 'win32':
            # Old MinGW defaults to MS layout, which adds tail padding even
            # to this packed Linux structure. Use the target's GCC ABI; retain
            # the real kernel's 128-byte BUILD_BUG_ON instead of bypassing it.
            command += ['-mno-ms-bitfields']
        if a.sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command+[str(c_file), '-o', str(exe)], check=True)
        correct, wrong, generated, baseline = subprocess.check_output([str(exe)], text=True).splitlines()
    expected = pps.ljust(128, b'\0')
    if bytes.fromhex(correct) != expected or bytes.fromhex(wrong) == expected:
        raise ValueError('Real kernel PPS packer mismatch or ineffective bpp negative control')
    if bytes.fromhex(generated) != expected or bytes.fromhex(baseline) == expected:
        raise ValueError('DSI native422 RC calculation mismatch or ineffective baseline control')
    report = {'scope': 'HOST_C_PPS_AND_DSI_RC_ONLY', 'hardware_tested': False, 'kernel_commit': actual,
              'reference_header_sha256': original_header_sha,
              'compiled_header_sha256': hashlib.sha256(header.encode()).hexdigest(),
              'reference_function_sha256': hashlib.sha256(function.encode()).hexdigest(),
              'patched_dsi_functions_sha256': hashlib.sha256(patched_functions.encode()).hexdigest(),
              'vendor_pps_sha256': hashlib.sha256(pps).hexdigest(),
              'matched_payload_bytes': 94, 'matched_zero_padding_bytes': 34,
              'physical_bpp_q4': 128, 'encoded_bpp_q4': 256,
              'wrong_bpp_negative_control_rejected': True, 'sanitizers': a.sanitize,
              'native422_from_minimal_panel_matches_all_pps_bytes': True,
              'baseline_native422_negative_control_rejected': True,
              'rgb_baseline_control_match': True, 'invalid_native422_profiles_rejected': 4}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('Real kernel C: complete PPS and minimal native422 RC calculation match; '
          'bpp/baseline negative controls rejected; RGB preserved and four invalid profiles rejected.')


if __name__ == '__main__':
    main()
