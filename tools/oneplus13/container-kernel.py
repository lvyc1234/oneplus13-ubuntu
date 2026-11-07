#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check resolved container Kconfig and the delivered module payload, not lsmod."""
import argparse
import json
from pathlib import Path
import tarfile

# Actual Kconfig/module names in the pinned SM8750 kernel. The legacy evaluator
# gates ip_tables even when the compatibility IP_NF_IPTABLES symbol is enabled.
BUILTIN = '''NAMESPACES NET_NS PID_NS IPC_NS UTS_NS USER_NS CGROUPS MEMCG
CPUSETS CGROUP_PIDS CGROUP_DEVICE BPF_SYSCALL CGROUP_BPF SECCOMP SECCOMP_FILTER
POSIX_MQUEUE OVERLAY_FS NETFILTER NETFILTER_XTABLES_LEGACY'''.split()
MODULES = dict(row.split(':') for row in '''
VETH:veth BRIDGE:bridge BRIDGE_NETFILTER:br_netfilter
NF_CONNTRACK:nf_conntrack NF_NAT:nf_nat NETFILTER_XTABLES:x_tables
NETFILTER_XT_MATCH_ADDRTYPE:xt_addrtype
NETFILTER_XT_MATCH_CONNTRACK:xt_conntrack
NETFILTER_XT_MATCH_COMMENT:xt_comment
NETFILTER_XT_MATCH_MULTIPORT:xt_multiport
NETFILTER_XT_MATCH_PHYSDEV:xt_physdev NETFILTER_XT_MARK:xt_mark
NETFILTER_XT_NAT:xt_nat NETFILTER_XT_TARGET_MASQUERADE:xt_MASQUERADE
NETFILTER_XT_TARGET_CHECKSUM:xt_CHECKSUM NETFILTER_XT_TARGET_TCPMSS:xt_TCPMSS
IP_NF_IPTABLES_LEGACY:ip_tables IP_NF_FILTER:iptable_filter
IP_NF_NAT:iptable_nat IP_NF_MANGLE:iptable_mangle IP_NF_RAW:iptable_raw
IP_NF_TARGET_REJECT:ipt_REJECT
IP6_NF_IPTABLES_LEGACY:ip6_tables IP6_NF_FILTER:ip6table_filter
IP6_NF_NAT:ip6table_nat IP6_NF_MANGLE:ip6table_mangle IP6_NF_RAW:ip6table_raw
IP6_NF_TARGET_REJECT:ip6t_REJECT
NF_TABLES:nf_tables
NFT_CT:nft_ct NFT_NAT:nft_chain_nat NFT_MASQ:nft_masq NFT_REDIR:nft_redir
NFT_COMPAT:nft_compat NFT_LOG:nft_log
NFT_LIMIT:nft_limit NFT_REJECT:nft_reject NFT_REJECT_INET:nft_reject_inet
NFT_FIB_IPV4:nft_fib_ipv4 NFT_FIB_IPV6:nft_fib_ipv6 NFT_FIB_INET:nft_fib_inet
'''.split())
BOOLS = 'NF_TABLES_INET NF_TABLES_IPV4 NF_TABLES_IPV6 NF_CONNTRACK_MARK NF_CONNTRACK_ZONES'.split()
# Menu selector only in this pinned kernel. Bridge-family handling lives in
# nf_tables; net/bridge/netfilter/Makefile has no nf_tables_bridge object.
FEATURES = ['NF_TABLES_BRIDGE']

def settings(text):
    return dict(line[7:].split('=', 1) for line in text.splitlines()
                if line.startswith('CONFIG_') and '=' in line)

def check_config(text):
    values = settings(text)
    missing = [name for name in BUILTIN + BOOLS if values.get(name) != 'y']
    missing += [name for name in list(MODULES) + FEATURES if values.get(name) not in ('y', 'm')]
    if missing:
        raise ValueError('Container kernel dependencies disabled: ' + ', '.join(missing))
    return values

def check_payload(text, archive, release):
    values = check_config(text)
    prefix = 'lib/modules/' + release + '/'
    members = {m.name.removeprefix('./'): m for m in archive.getmembers()}
    modules = {Path(name).name.split('.ko')[0] for name, m in members.items()
               if name.startswith(prefix + 'kernel/') and m.isfile()
               and name.endswith(('.ko', '.ko.xz', '.ko.zst', '.ko.gz'))}
    absent = [mod for key, mod in MODULES.items() if values[key] == 'm' and mod not in modules]
    if absent:
        raise ValueError('Container modules missing from delivery: ' + ', '.join(absent))
    # Existing verify.py checks every dependency and depmod's binary indexes.
    return {'scope': 'RESOLVED_CONFIG_AND_DELIVERED_CONTAINER_MODULES',
            'legacy_iptables': True, 'iptables_nft': True,
            'required_modules_present': len(MODULES), 'hardware_tested': False}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--modules', type=Path)
    p.add_argument('--release')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    text = a.config.read_text()
    check_config(text)
    report = {'scope': 'RESOLVED_CONTAINER_CONFIG', 'hardware_tested': False}
    if a.modules:
        if not a.release: p.error('--release is required with --modules')
        with tarfile.open(a.modules) as archive:
            report = check_payload(text, archive, a.release)
    if a.output: a.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, sort_keys=True))

if __name__ == '__main__': main()
