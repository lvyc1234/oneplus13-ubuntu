"""One bounded NetworkManager WiFi acceptance; credentials stay in RAM files.

One requested owner-SSID scan with bounded read-only cache observations.
No raw iw scan, driver reset, module reload, persistent install or reconnect.
This file is safe to publish; the separate credentials JSON is private.
"""
import argparse
import importlib.util
import json
import math
import os
import re
import shutil
from pathlib import Path
import subprocess
import tempfile
import time
import uuid


HTTPS_WRITE_OUT = ('dns_s=%{time_namelookup}\ntcp_s=%{time_connect}\n'
                   'tls_s=%{time_appconnect}\nfirst_byte_s=%{time_starttransfer}\n'
                   'total_s=%{time_total}\nhttp_code=%{http_code}\nssl_verify_result=%{ssl_verify_result}\n')
HTTP_WRITE_OUT = ('dns_s=%{time_namelookup}\ntcp_s=%{time_connect}\n'
                  'first_byte_s=%{time_starttransfer}\ntotal_s=%{time_total}\n'
                  'http_code=%{http_code}\n')


def https_metrics(output):
    """Keep numeric timings only; never publish resolver/peer/error strings."""
    result = {}
    for line in output.splitlines():
        key, sep, value = line.partition('=')
        if not sep:
            continue
        if key == 'http_code' and re.fullmatch(r'[0-9]{3}', value):
            result[key] = int(value)
        elif key == 'ssl_verify_result' and re.fullmatch(r'[0-9]{1,4}', value):
            result[key] = int(value)
        elif key in {'dns_s', 'tcp_s', 'tls_s', 'first_byte_s', 'total_s'}:
            try:
                seconds = float(value)
            except ValueError:
                continue
            if math.isfinite(seconds) and 0 <= seconds <= 60:
                result[key] = seconds
    return result


def run(argv, seconds=6):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=seconds)
        if argv[0] == 'curl' and '--write-out' in argv:
            # --silent and --output /dev/null leave only our numeric write-out
            # on stdout, even on failure. Discard raw stderr from this probe.
            return p.returncode, p.stdout[:4096]
        return p.returncode, p.stdout if p.returncode==0 else p.stderr[:4096]
    except subprocess.TimeoutExpired:
        return 124, ''
    except FileNotFoundError:
        return 127, 'executable missing'
    except OSError:
        return 126, 'executable unavailable'


def guarded_run(argv, seconds=6):
    """Use the packaged full-history gate before each live WiFi command."""
    path = Path(__file__).with_name('oneplus13-ram-kernel-health.py')
    if not path.is_file():
        path = Path(__file__).with_name('ram-kernel-health.py')
    spec = importlib.util.spec_from_file_location('wifi_kernel_health', path)
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    history = subprocess.run(['dmesg'], capture_output=True, timeout=4)
    if history.returncode or len(history.stdout) > 4 * 1024 * 1024:
        raise ValueError('Kernel history unavailable; stop WiFi commands')
    facts = health.classify(history.stdout.decode(errors='replace'))
    if not facts['pass']:
        print('RAM_KERNEL_HEALTH_RESULT ' + json.dumps(facts, sort_keys=True), flush=True)
        raise ValueError('Kernel history fault; stop WiFi commands')
    return run(argv, seconds)


def set_stock_country(iface, country, command):
    """One disconnected user hint; require the selected self-managed PHY ack.

    The private input must come from the owner's stock device in the same
    location. This never clears channel flags or changes SAR/power tables.
    """
    if not isinstance(country,str) or not re.fullmatch('[A-Z]{2}',country):
        raise ValueError('Invalid stock country code')
    result={'country_hint_sent':False,'country_phy_acknowledged':False}
    rc,link=command('country_link_guard',['iw','dev',iface,'link'],4)
    if rc or link.strip()!='Not connected.':
        result['country_refused_associated_or_unknown']=True
        return result
    rc,info=command('country_phy',['iw','dev',iface,'info'],4)
    phys=re.findall(r'^\s*wiphy ([0-9]+)\s*$',info,re.M)
    if rc or len(phys)!=1:return result
    phy=phys[0]
    def acknowledged(text):
        blocks=re.split(r'(?m)^phy#',text)
        selected=[b for b in blocks[1:] if re.match(re.escape(phy)+r' \(self-managed\)\s*\n',b)]
        return len(selected)==1 and re.findall(r'(?m)^country ([A-Z0-9]{2}):',selected[0])==[country]
    rc,reg=command('country_before',['iw','reg','get'],4)
    if rc:return result
    if acknowledged(reg):
        result['country_phy_acknowledged']=True
        return result
    # Recheck immediately before the mutation; a GUI/autoconnect race is a
    # refusal, never permission to change country on an associated link.
    rc,link=command('country_link_recheck',['iw','dev',iface,'link'],4)
    if rc or link.strip()!='Not connected.':
        result['country_refused_associated_or_unknown']=True
        return result
    rc,_=command('country_hint',['iw','reg','set',country],4)
    result['country_hint_sent']=rc==0
    if rc:return result
    deadline=time.monotonic()+8
    for observation in range(6):
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        rc,reg=command('country_ack',['iw','reg','get'],min(3,remaining))
        result['country_ack_observations']=observation+1
        if rc:return result
        if acknowledged(reg):
            result['country_phy_acknowledged']=True
            break
        time.sleep(min(.5,max(0,deadline-time.monotonic())))
    return result


def check(credentials, iface, execute=run, runtime=Path('/run'), host_epoch=None, existing_link=False):
    ssid, password = credentials['ssid'], credentials['password']
    probe_url = credentials.get('http_probe_url', 'http://example.com/')
    band = credentials.get('band')
    if band not in (None, 'a', 'bg'):
        raise ValueError('Invalid requested WiFi band')
    country=credentials.get('stock_country_code')
    if country is not None and (not isinstance(country,str) or not re.fullmatch('[A-Z]{2}',country)):
        raise ValueError('Invalid stock country code')
    if country is not None:
        raise ValueError('Country transitions are held after a runtime WLAN restart; use the unchanged firmware regulatory state')
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
        raise ValueError('Invalid SSID length')
    if not isinstance(password, str) or not 8 <= len(password) <= 63 or '\n' in password or '\r' in password:
        raise ValueError('Invalid WPA passphrase format')
    if not isinstance(probe_url, str) or not probe_url.startswith('http://') or any(c.isspace() for c in probe_url):
        raise ValueError('Invalid HTTP probe URL')
    result = {'scope': 'ONE_NETWORK_RAM_ONLY', 'interface_found': True,
              'association_pass': False, 'dhcp_pass': False, 'http_pass': False, 'https_pass': False,
              'automatic_acceptance_pass': False,
              'password_logged': False, 'low_level_scan_or_reset': False}
    result['requested_band'] = {'a': '5GHz', 'bg': '2.4GHz'}.get(band, 'any')
    result['band_pass'] = False

    def command(label, args, seconds):
        rc, output = execute(args, seconds)
        result[label + '_rc'] = rc
        if rc:
            # Store only a fixed error category; raw diagnostics may name SSIDs.
            low=output.lower()
            result[label + '_error_class'] = (
                'executable_missing' if rc==127 else
                'executable_unavailable' if rc==126 else
                'transfer_timeout' if label in ('http','https') and rc==28 else
                'certificate_verification_failed' if label=='https' and rc==60 else
                'device_unavailable' if 'unavailable' in low else
                'permission_denied' if 'not authorized' in low or 'permission denied' in low else
                'secrets_required' if 'secrets were required' in low else
                'timeout' if rc==124 else 'command_failed')
        # Print progress codes only. Never expose nmcli stdout/stderr or credentials.
        print('AUTO_WIFI_PHASE ' + json.dumps({'phase': label, 'returncode': rc}), flush=True)
        return rc, output

    rc, _ = command('supplicant_dbus', ['busctl','--system','--timeout=4','get-property',
            'fi.w1.wpa_supplicant1','/fi/w1/wpa_supplicant1','fi.w1.wpa_supplicant1','Interfaces'], 6)
    if rc:
        return result
    rc, state = command('nm_ready', ['nmcli', '--wait', '4', '-g', 'GENERAL.STATE', 'device', 'show', iface], 6)
    if rc:
        return result
    code=re.match(r'\s*(\d+)',state)
    result['nm_device_state_code']=int(code[1]) if code else None
    result['nm_device_ready']=code is not None and 30<=int(code[1])<=100
    if not result['nm_device_ready']:
        return result
    if existing_link:
        # Preservation observes the already-active connection. It never scans,
        # creates a second profile, changes management or reauthenticates.
        result.update(scope='ONE_NETWORK_EXISTING_LINK_RAM_ONLY',
                      directed_scan_requested=False, connection_reactivation_requested=False)
        if result['nm_device_state_code'] != 100:
            return result
        rc, active = command('active_profile', ['nmcli', '--wait', '4', '-g',
                             'GENERAL.CON-UUID', 'device', 'show', iface], 6)
        ident = active.strip()
        if rc or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', ident):
            return result
        rc, configured = command('active_ssid', ['nmcli', '--wait', '4', '--escape', 'no',
                                '-g', '802-11-wireless.ssid', 'connection', 'show', 'uuid', ident], 6)
        result['association_pass'] = rc == 0 and configured.removesuffix('\n') == ssid
        if not result['association_pass']:
            return result
        return observe_link_and_traffic(credentials, iface, result, command, host_epoch, expected_ssid=ssid)
    rc, _ = command('managed', ['nmcli', '--wait', '4', 'device', 'set', iface, 'managed', 'yes'], 6)
    if rc:
        return result
    if country is not None:
        result.update(set_stock_country(iface,country,command))
        if not result['country_phy_acknowledged']:return result
    rc, device_path = command('device_path', ['nmcli', '--wait', '4', '-g', 'GENERAL.DBUS-PATH', 'device', 'show', iface], 6)
    device_path = device_path.strip()
    if rc or not re.fullmatch(r'/org/freedesktop/NetworkManager/Devices/[0-9]+',device_path):
        result['scan_property_unavailable'] = True
        return result
    scan_property = ['busctl','--system','--timeout=2','get-property','org.freedesktop.NetworkManager',
                     device_path,'org.freedesktop.NetworkManager.Device.Wireless','LastScan']
    rc, before = command('scan_before',scan_property,3)
    initial = re.fullmatch(r'x (-?[0-9]+)\s*',before)
    if rc or not initial:
        result['scan_property_unavailable'] = True
        return result
    rc, _ = command('rescan', ['nmcli', '--wait', '8', 'device', 'wifi', 'rescan', 'ifname', iface, 'ssid', ssid], 10)
    result['directed_scan_requested'] = True
    if rc:
        return result
    # NetworkManager documents LastScan as completion in CLOCK_BOOTTIME ms.
    # RequestScan success only acknowledges a request. Wait for its completion
    # without requesting a second scan, driver reset or reconnect.
    # An associated radio may complete a foreground scan later than a
    # disconnected one. Keep one request and a finite, explicit budget.
    result['scan_wait_budget_seconds'] = 45 if result['nm_device_state_code'] == 100 else 16
    scan_deadline = time.monotonic() + result['scan_wait_budget_seconds']
    result['scan_completed'] = False
    for attempt in range(24 if result['nm_device_state_code'] == 100 else 9):
        remaining = scan_deadline - time.monotonic()
        if remaining <= 0:break
        rc, after = command('scan_completion',scan_property,min(3,remaining))
        observed = re.fullmatch(r'x (-?[0-9]+)\s*',after)
        result['scan_observations'] = attempt + 1
        if rc or not observed:return result
        if int(observed[1]) > int(initial[1]):
            result['scan_completed'] = True
            break
        time.sleep(min(2,max(0,scan_deadline-time.monotonic())))
    if not result['scan_completed']:return result
    cache_deadline = time.monotonic() + 10
    result['maximum_visible_ssid_rows'] = 0
    for observation in range(3):
        remaining = cache_deadline - time.monotonic()
        if remaining <= 0:break
        rc, output = command('ap_list', ['nmcli', '--wait', '4', '--escape', 'no', '-g', 'SSID',
                                        'device', 'wifi', 'list', 'ifname', iface, '--rescan', 'no'], min(6,remaining))
        rows = output.splitlines() if rc == 0 else []
        result['ap_list_observations'] = observation + 1
        result['scan_result_rows'] = len(rows)
        result['visible_ssid_rows'] = sum(bool(row) for row in rows)
        result['unique_visible_ssids'] = len({row for row in rows if row})
        result['maximum_visible_ssid_rows'] = max(result['maximum_visible_ssid_rows'],result['visible_ssid_rows'])
        result['target_seen'] = rc == 0 and ssid in rows
        if rc or result['target_seen']:break
        if observation < 2:time.sleep(min(1,max(0,cache_deadline-time.monotonic())))
    if rc or not result['target_seen']:
        return result
    ident = str(uuid.uuid4())
    profile = ['nmcli', '--wait', '4', 'connection', 'add', 'type', 'wifi',
                               'ifname', iface, 'con-name', 'oneplus13-ram-acceptance',
                               'connection.uuid', ident, 'ssid', ssid,
                               'connection.autoconnect', 'no', 'wifi-sec.key-mgmt', 'wpa-psk']
    if band:
        profile += ['802-11-wireless.band', band]
    rc, _ = command('profile', profile, 6)
    if rc:
        return result
    fd, name = tempfile.mkstemp(prefix='oneplus13-wifi-', dir=runtime)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write('802-11-wireless-security.psk:' + password + '\n')
        rc, _ = command('authentication', ['nmcli', '--wait', '15', 'connection', 'up', 'uuid', ident,
                                          'ifname', iface, 'passwd-file', name], 18)
    finally:
        Path(name).unlink(missing_ok=True)
    if rc:
        return result
    rc, active = command('active_profile', ['nmcli', '--wait', '4', '-g', 'GENERAL.CON-UUID',
                                          'device', 'show', iface], 6)
    result['association_pass'] = rc == 0 and active.strip() == ident
    if not result['association_pass']:
        return result
    return observe_link_and_traffic(credentials, iface, result, command, host_epoch)


def observe_link_and_traffic(credentials, iface, result, command, host_epoch, expected_ssid=None):
    """Verify the current RF link and interface-bound traffic without a reset."""
    band = credentials.get('band')
    probe_url = credentials.get('http_probe_url', 'http://example.com/')
    # Profile identity and SSID do not prove the actual RF band. Read the
    # connected link through nl80211; never accept a 2.4GHz fallback as 5GHz.
    rc, link = command('link_frequency', ['iw', 'dev', iface, 'link'], 4)
    if expected_ssid is not None:
        associated = re.findall(r'^\s*SSID: (.*)$', link, re.M)
        result['active_link_ssid_matches'] = rc == 0 and associated == [expected_ssid]
        if not result['active_link_ssid_matches']:
            result['association_pass'] = False
            return result
    frequencies = re.findall(r'^\s*freq:\s*([0-9]{4})(?:\.0+)?\s*$', link, re.M)
    frequency = int(frequencies[0]) if len(frequencies) == 1 and not rc else None
    actual_band = ('2.4GHz' if frequency and 2400 <= frequency <= 2500 else
                   '5GHz' if frequency and 4900 <= frequency < 5925 else
                   '6GHz' if frequency and 5925 <= frequency <= 7125 else None)
    result['link_frequency_mhz'] = frequency
    result['associated_band'] = actual_band
    result['band_pass'] = actual_band is not None and (band is None or actual_band == result['requested_band'])
    if not result['band_pass']:
        return result
    # Activation can complete on IPv6 before DHCPv4 finishes. Observe the same
    # connection for at most 15 seconds; do not reconnect or invent a lease.
    deadline = time.monotonic() + 15
    for attempt in range(8):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        rc, addresses = command('ipv4_address', ['ip', '-j', '-4', 'address', 'show', 'dev', iface], min(3, remaining))
        remaining = deadline - time.monotonic()
        if rc or remaining <= 0:
            break
        rr, routes = command('ipv4_route', ['ip', '-j', '-4', 'route', 'show', 'dev', iface, 'default'], min(3, remaining))
        result['ipv4_observations'] = attempt + 1
        if rr:
            break
        try:
            has_ip = any(a.get('scope') == 'global' for dev in json.loads(addresses) for a in dev.get('addr_info', []))
            defaults = [x for x in json.loads(routes) if x.get('dst') == 'default' and x.get('gateway')]
        except (ValueError, TypeError, AttributeError):
            result['ipv4_observation_invalid'] = True
            break
        result['ipv4_global_address_present'] = has_ip
        result['ipv4_default_route_present'] = bool(defaults)
        result['dhcp_pass'] = has_ip and bool(defaults)
        if result['dhcp_pass']:
            break
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    if not result['dhcp_pass']:
        return result
    # These are diagnostics, not prerequisites: curl resolves the hostname and
    # proves traffic on the selected WiFi interface even in a minimal rootfs.
    diagnostics = credentials.get('external_diagnostics', False) is True
    if diagnostics:
        rc, _ = command('gateway', ['ping', '-I', iface, '-c', '2', '-W', '2', defaults[0]['gateway']], 6)
        result['gateway_probe_skipped'] = rc in (126, 127)
        rc, _ = command('dns', ['getent', 'ahostsv4', 'example.com'], 5)
        result['dns_probe_skipped'] = rc in (126, 127)
    rc, output = command('http', ['curl', '--ipv4', '--noproxy', '*', '--interface', iface,
                             '--connect-timeout', '4', '--max-time', '7', '--fail', '--silent',
                             '--output', '/dev/null', '--write-out', HTTP_WRITE_OUT, probe_url], 9)
    result['http_probe_endpoint_class'] = 'owner-supplied-private-LAN'
    result['http_metrics'] = https_metrics(output)
    result['http_pass'] = rc == 0 and result['http_metrics'].get('http_code') == 200
    if diagnostics:
        rc, output = command('https', ['curl', '--ipv4', '--noproxy', '*', '--interface', iface, '--connect-timeout', '4',
                             '--max-time', '7', '--fail', '--silent', '--output', '/dev/null',
                             '--write-out', HTTPS_WRITE_OUT,
                             'https://example.com/'], 9)
        result['https_address_family'] = 'IPv4'
        result['https_metrics'] = https_metrics(output)
        result['https_pass'] = (rc == 0 and result['https_metrics'].get('http_code') == 200
                            and result['https_metrics'].get('ssl_verify_result') == 0)
    result['external_diagnostics_requested'] = diagnostics
    result['automatic_acceptance_pass'] = all(result.get(k) for k in ['association_pass', 'band_pass', 'dhcp_pass', 'http_pass'])
    if isinstance(host_epoch, int) and host_epoch > 0:
        result['guest_epoch'] = int(time.time())
        result['clock_delta_seconds'] = result['guest_epoch'] - host_epoch
        result['clock_sane'] = abs(result['clock_delta_seconds']) <= 3600
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--host-epoch', type=int)
    parser.add_argument('--existing-link', action='store_true', help='Observe the active link without scanning or reconnecting')
    args = parser.parse_args()
    if ' / overlay ' not in Path('/proc/mounts').read_text():
        raise SystemExit('Refusing WiFi credential staging outside RAM overlay')
    credentials = json.loads(args.credentials.read_text())
    args.credentials.unlink()  # Remove transport copy before invoking any subprocess.
    required=['nmcli','busctl','ip','curl','iw']
    missing=[name for name in required if shutil.which(name) is None]
    interfaces = [p.name for p in Path('/sys/class/net').iterdir()
                  if (p/'phy80211').exists() or (p/'wireless').is_dir()]
    if missing:
        result={'missing_required_commands':missing,'automatic_acceptance_pass':False}
    elif len(interfaces) != 1:
        result = {'interface_found': False, 'interface_count': len(interfaces)}
    else:
        probe=Path('/run/oneplus13-wifi-probe.private.json')
        if probe.parent.is_dir():
            probe.write_text(json.dumps({'url':credentials.get('http_probe_url','http://example.com/')}),encoding='utf-8')
            if os.name=='posix':probe.chmod(0o600)
        result = check(credentials, interfaces[0], execute=guarded_run, host_epoch=args.host_epoch, existing_link=args.existing_link)
    if 'automatic_acceptance_pass' not in result:
        result['automatic_acceptance_pass'] = False
    print('AUTO_WIFI_RESULT ' + json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
