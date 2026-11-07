#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Opt-in RAM orchestration. No firmware, SAR or channel-flag overrides.

Hold NetworkManager before the WLAN PHY exists. Only an exact fresh stock
observation and an unmanaged, disconnected PHY permit a normal country hint.
Failures before acknowledgment retain the hold. Any failure after release
requires immediate recovery; this tool never retries or resets the driver.
"""
import argparse,configparser,datetime,hashlib,importlib.util,json,os,re,subprocess
from pathlib import Path

CONFIG=b'[keyfile]\nunmanaged-devices+=type:wifi\n'
# /etc is an immutable-base RAM overlay in this experiment. It is parsed
# after the existing USB exclusion; += preserves that transport policy.
REL='etc/NetworkManager/conf.d/99-oneplus13-ram-wifi-hold.conf'
USB_POLICY='etc/NetworkManager/conf.d/80-oneplus13-ram-usb.conf'
USB_TOKEN='mac:02:13:00:00:00:01'
MARKER='run/oneplus13/wifi-quiescent-preparation.json'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def execute(args,seconds=5):
    result=subprocess.run(args,capture_output=True,text=True,timeout=seconds)
    return result.returncode,result.stdout if not result.returncode else ''


def checked(args,run=execute):
    rc,text=run(args,5)
    if rc:raise ValueError('Bounded preparation command failed')
    return text


def guard(root,release,run=execute,uname=None):
    if (uname or os.uname)().release!=release:raise ValueError('Wrong kernel')
    if not (root/'run/oneplus13/ram-session').is_file():raise ValueError('RAM session missing')
    lines=(root/'proc/mounts').read_text().splitlines()
    if not any(len(p:=line.split())>=3 and p[1:3]==['/','overlay'] for line in lines):raise ValueError('RAM overlay missing')
    blocks=[p for p in (root/'sys/class/block').iterdir() if not p.name.startswith(('ram','zram','loop'))]
    if not blocks or any((p/'ro').read_text().strip()!='1' for p in blocks):raise ValueError('Storage protection missing')
    for unit in ['oneplus13-ram-guard.service','oneplus13-ram-deadline.timer']:
        checked(['systemctl','is-active','--quiet',unit],run)


def health_check(module,run=execute):
    text=checked(['dmesg'],run)
    if len(text)>4*1024*1024:raise ValueError('Oversized kernel history')
    facts=module.classify(text)
    if not facts['pass']:raise ValueError('Kernel history contains a fault; preserve hold and recover')


def unmanaged(iface,run):
    value=checked(['nmcli','--wait','3','-g','GENERAL.STATE','device','show',iface],run)
    if not re.match(r'^10(?:\s|\()',value.strip()):raise ValueError('WLAN must remain strictly unmanaged')


def effective_config(run=execute):
    text=checked(['NetworkManager','--print-config'],run)
    parsed=configparser.ConfigParser(interpolation=None,strict=False);parsed.read_string(text)
    devices=[item.strip() for item in re.split('[;,]',parsed.get('keyfile','unmanaged-devices',fallback=''))]
    if USB_TOKEN not in devices or 'type:wifi' not in devices or any(item.startswith('except:') for item in devices):
        raise ValueError('Effective hold must preserve USB and strictly exclude WiFi')


def country_when_quiescent(wifi,iface,country,run=execute,health=None):
    if not re.fullmatch('[A-Z]{2}',country):raise ValueError('Invalid observed stock country')
    unmanaged(iface,run)
    def command(label,args,seconds):
        # The immutable strict-unmanaged config prevents API manage overrides;
        # recheck state before every operation in the existing country helper.
        if health is not None:health_check(health,run)
        unmanaged(iface,run)
        return run(args,seconds)
    facts=wifi.set_stock_country(iface,country,command)
    if not facts.get('country_phy_acknowledged'):raise ValueError('Selected PHY did not acknowledge stock country')
    return facts


def owned(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_uid!=0 or path.stat().st_mode&0o077:
        raise ValueError('Unexpected RAM preparation file')


def stock_observation_fresh(captured_utc,host_epoch):
    if not isinstance(host_epoch,int) or host_epoch<=0:raise ValueError('Host observation clock required')
    try:stamp=datetime.datetime.fromisoformat(captured_utc).timestamp()
    except (TypeError,ValueError):raise ValueError('Invalid stock observation time') from None
    if not 0<=host_epoch-stamp<=3600:raise ValueError('Stock observation is stale')


def hold(root,release,run=execute):
    guard(root,release,run)
    phy=root/'sys/class/ieee80211'
    if phy.exists() and any(phy.iterdir()):raise ValueError('Hold must precede WLAN PHY creation')
    conf=root/REL;marker=root/MARKER
    if conf.exists() or conf.is_symlink() or marker.exists():raise ValueError('Preserve prior preparation evidence')
    for parent in [root/'etc/NetworkManager',root/'run/oneplus13']:
        if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid!=0 or parent.stat().st_mode&0o022:
            raise ValueError('Unexpected RAM preparation ancestry')
    conf.parent.mkdir(mode=0o755,exist_ok=True)
    if conf.parent.is_symlink() or conf.parent.stat().st_uid!=0 or conf.parent.stat().st_mode&0o022:
        raise ValueError('Unsafe NetworkManager RAM config directory')
    with conf.open('xb') as stream:stream.write(CONFIG)
    conf.chmod(0o600)
    effective_config(run)
    checked(['nmcli','general','reload','conf'],run)
    with marker.open('x') as stream:json.dump({'release':release,'config_sha256':digest(conf),'usb_policy_sha256':digest(root/USB_POLICY)},stream)
    marker.chmod(0o600)
    return {'scope':'PRE_PHY_NETWORKMANAGER_HOLD','no_phy_before_hold':True,'country_hint_sent':False}


def initialize(root,release,observation,observation_sha256,wifi,health,host_epoch=None,run=execute):
    guard(root,release,run);conf=root/REL;marker=root/MARKER
    owned(conf);owned(marker)
    if conf.read_bytes()!=CONFIG or json.loads(marker.read_text())!={'release':release,'config_sha256':digest(conf),'usb_policy_sha256':digest(root/USB_POLICY)}:
        raise ValueError('Preparation binding changed')
    effective_config(run)
    if digest(observation)!=observation_sha256:raise ValueError('Stock observation changed')
    facts=json.loads(Path(observation).read_text())
    if (facts.get('scope')!='FRESH_READ_ONLY_STOCK_WIFI_OBSERVATION' or
        not facts.get('target_5ghz_ssid_exact_match') or facts.get('scan_or_setting_change') is not False or
        len(facts.get('country_codes',[]))!=1 or len(facts.get('frequency_mhz',[]))!=1 or
        not 4900<=facts['frequency_mhz'][0]<5925):raise ValueError('Fresh matching stock 5 GHz observation required')
    stock_observation_fresh(facts['captured_utc'],host_epoch)
    interfaces=[p.parent.name for p in (root/'sys/class/net').glob('*/phy80211')]
    if len(interfaces)!=1:raise ValueError('Exactly one WLAN interface required')
    iface=interfaces[0]
    health_check(health,run)
    result=country_when_quiescent(wifi,iface,facts['country_codes'][0],run,health)
    health_check(health,run)
    unmanaged(iface,run);owned(conf)
    if conf.read_bytes()!=CONFIG:raise ValueError('Hold changed before release')
    # Only the file created by hold() is removed, after the selected-PHY ack.
    conf.unlink();checked(['nmcli','general','reload','conf'],run)
    checked(['nmcli','--wait','3','device','set',iface,'managed','yes'],run)
    return {'scope':'QUIESCENT_STOCK_COUNTRY_PREPARATION','country':facts['country_codes'][0],
            'selected_phy_acknowledged':True,'networkmanager_hold_released':True,
            'channel_flags_or_sar_overridden':False,**result}


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=['hold','initialize'],required=True)
    p.add_argument('--release',required=True);p.add_argument('--stock-observation',type=Path);p.add_argument('--stock-observation-sha256')
    p.add_argument('--host-epoch',type=int)
    a=p.parse_args()
    if os.geteuid()!=0:raise ValueError('Root RAM orchestration required')
    if a.mode=='hold':result=hold(Path('/'),a.release)
    else:
        if not a.stock_observation or not re.fullmatch('[a-f0-9]{64}',a.stock_observation_sha256 or ''):raise ValueError('Bound stock observation required')
        result=initialize(Path('/'),a.release,a.stock_observation,a.stock_observation_sha256,
            module('installed_wifi','/usr/libexec/oneplus13-automatic-wifi-check.py'),
            module('installed_health','/usr/libexec/oneplus13-ram-kernel-health.py'),a.host_epoch)
    print('QUIESCENT_WIFI_PREPARATION '+json.dumps(result,sort_keys=True),flush=True)
