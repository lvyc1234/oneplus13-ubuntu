#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute real DSI group/packet/resource functions against host-only fault I/O."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--sanitize', action='store_true')
    p.add_argument('--keep-source', type=Path)
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    pin = json.loads((repo/'devices/oneplus13/source.json').read_text())['kernel']['commit']
    def original(path):
        return subprocess.check_output(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
            '-C', str(a.kernel_source), 'show', pin+':'+path]).decode()
    assert subprocess.check_output(['git', '-C', str(a.kernel_source), 'rev-parse', 'HEAD'], text=True).strip() == pin
    spec = importlib.util.spec_from_file_location('display_check', Path(__file__).with_name('check-display.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extract = module.extract_function
    paths = ['include/drm/drm_mipi_dsi.h', 'drivers/gpu/drm/drm_mipi_dsi.c',
             'drivers/gpu/drm/msm/dsi/dsi_host.c', 'drivers/gpu/drm/msm/dsi/dsi_manager.c']
    header, core, host, manager = [(a.kernel_source/path).read_text() for path in paths]
    functions = '\n'.join(extract(core, sig) for sig in [
        'bool mipi_dsi_packet_format_is_short(', 'bool mipi_dsi_packet_format_is_long(',
        'int mipi_dsi_create_packet(', 'int mipi_dsi_transfer_batch('])
    functions += '\n'+extract(manager, 'bool msm_dsi_manager_batch_allowed(')
    functions += '\n'+extract(manager, 'bool msm_dsi_manager_cmd_xfer_trigger(')
    signatures = ['int dsi_link_clk_set_rate_6g(', 'int dsi_link_clk_set_rate_6g_v2_9(',
                  'int dsi_link_clk_enable_6g(', 'void dsi_link_clk_disable_6g(',
                  'static void dsi_sw_reset(', 'static int dsi_cmd_dma_add(',
                  'static int dsi_cmd_dma_tx(', 'static ssize_t dsi_host_transfer(',
                  'static int dsi_batch_size(', 'static int dsi_batch_pack(',
                  'static int dsi_batch_prepare(', 'static int dsi_host_transfer_batch(',
                  'static int dsi_host_attach(',
                  'int msm_dsi_host_power_on(',
                  'void msm_dsi_host_xfer_restore(']
    # No changes to ordinary single-message packing/dispatch/trigger are allowed.
    for sig in ['static int dsi_cmd_dma_add(', 'static ssize_t dsi_host_transfer(']:
        assert extract(host, sig) == extract(original(paths[2]), sig)
    assert extract(manager, 'bool msm_dsi_manager_cmd_xfer_trigger(') == extract(original(paths[3]), 'bool msm_dsi_manager_cmd_xfer_trigger(')
    functions += '\n'+'\n'.join(extract(host, sig) for sig in signatures)
    types = '\n'.join(extract(header, sig)+';' for sig in [
        'struct mipi_dsi_msg {', 'struct mipi_dsi_packet {', 'struct mipi_dsi_host_ops {'])
    fixture = json.loads((repo/'devices/oneplus13/panel-aa569-120hz.json').read_text())
    data, groups, pending, messages = [], [], bytearray(), []
    for packet in fixture['on_packets']:
        b = bytes.fromhex(packet['payload'])
        i = len(data)
        data.extend(b)
        messages.append(f'{{.type={packet["type"]},.flags=2,.tx_len={len(b)},.tx_buf=payload+{i}}}')
        last = packet['vendor_flags'] == 0
        h = bytearray([len(b)&255, len(b)>>8, packet['type'], 64]) if packet['type']==57 else bytearray([b[0], b[1] if len(b)==2 else 0, packet['type'], 0])
        h[3] |= 128 if last else 0
        if packet['type']==57:
            h += b
        h += b'\xff'*(-len(h)%4)
        pending += h
        if last:
            groups.append((len(messages)-sum(g[0] for g in groups), bytes(pending)))
            pending.clear()
    assert not pending and len(groups)==13
    def arr(b):
        return ','.join(str(x) for x in b)
    vectors = 'static const u8 payload[] = {'+arr(data)+'};\n'
    vectors += 'static struct mipi_dsi_msg messages[] = {'+','.join(messages)+'};\n'
    for i, (_, b) in enumerate(groups):
        vectors += f'static const u8 expected_{i}[] = {{'+arr(b)+'};\n'
    vectors += 'static const struct {size_t count,len;const u8 *bytes;} groups[] = {'
    vectors += ','.join(f'{{{n},{len(b)},expected_{i}}}' for i,(n,b) in enumerate(groups))+'};\n'
    template = (repo/'tests/dsi_batch_test.c').read_text()
    source = template.replace('/* MIPI_TYPES */', original('include/video/mipi_display.h')+'\n'+types)
    source = source.replace('/* REAL_FUNCTIONS */', functions).replace('/* VECTORS */', vectors)
    with tempfile.TemporaryDirectory(prefix='oneplus13-dsi-batch-') as tmp:
        c, exe = Path(tmp)/'batch.c', Path(tmp)/'batch.exe'
        c.write_text(source, encoding='utf-8')
        if a.keep_source:
            a.keep_source.write_text(source, encoding='utf-8')
        # Kernel builds disable this warning; unchanged legacy packing uses int len.
        command = ['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare']
        if a.sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command+[str(c), '-o', str(exe)], check=True)
        output = subprocess.check_output([str(exe)], text=True).strip()
    assert output == 'OK real DSI batches, resources, fault cleanup and legacy controls'
    result = {'scope':'HOST_C_DSI_BATCH_ONLY', 'hardware_tested':False, 'kernel_commit':pin,
        'sanitizers':a.sanitize, 'on_vendor_groups_match':13, 'on_packets':150,
        'max_group_bytes':max(len(b) for _,b in groups),
        'group_dma_sha256':[hashlib.sha256(b).hexdigest() for _,b in groups],
        'invalid_groups_fail_before_mapping_or_pm':True,
        'pm_parent_rate_and_enable_failures_balanced':True,
        'one_lock_mapping_resource_lifetime_and_kickoff_per_group':True,
        'dma_timeout_reset_before_restore':True, 'no_queued_state_after_return':True,
        'synchronous_component_attach_and_failure_cleanup_checked':True,
        'host_power_pm_clock_and_pinctrl_failures_balanced':True,
        'ordinary_packing_dispatch_and_manager_trigger_unchanged':True,
        'actual_c_source_sha256':{path:hashlib.sha256(text.encode()).hexdigest()
                                 for path,text in zip(paths,[header,core,host,manager])}}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
