#!/usr/bin/env python3
"""Explicit RAM-only GDM login and bounded user-session software acceptance.

Never installed as a boot service. This does not test visible pixels or touch.
"""
import argparse
import configparser
import io
import importlib.util
import json
from pathlib import Path
import re
import stat
import subprocess
import time

OBSERVATIONS = []
COMPLETED = {}
SAMPLES = []


def load_wifi_helper():
    """Use the image's prefixed sibling without substituting a host overlay."""
    here = Path(__file__)
    name = ('oneplus13-automatic-wifi-check.py' if here.name.startswith('oneplus13-')
            else 'automatic-wifi-check.py')
    spec = importlib.util.spec_from_file_location('desktop_wifi', here.with_name(name))
    wifi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wifi)
    return wifi


def probe_http(wifi, iface, execute=None, probe_url=None):
    if probe_url is None:
        config=json.loads(Path('/run/oneplus13-wifi-probe.private.json').read_text())
        probe_url=config['url']
    rc, output = (execute or wifi.run)(['curl', '--ipv4', '--noproxy', '*', '--interface', iface,
                                      '--connect-timeout', '4', '--max-time', '7', '--fail', '--silent',
                                      '--output', '/dev/null', '--write-out', wifi.HTTP_WRITE_OUT,
                                      probe_url], 9)
    metrics = wifi.https_metrics(output)
    return {'kind':'owner-supplied-private-LAN-http','returncode': rc, 'metrics': metrics,
            'passed': rc == 0 and metrics.get('http_code') == 200}

def ram_only(mounts, marker):
    if not marker or not any(len(p := line.split()) >= 3 and p[1:3] == ['/', 'overlay']
                             for line in mounts.splitlines()):
        raise ValueError('RAM overlay and session marker required')


def autologin_config(source):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_string(source)
    if not config.has_section('daemon'):
        config.add_section('daemon')
    for key in list(config['daemon']):
        if key.lower() in ('automaticloginenable', 'automaticlogin', 'timedloginenable', 'timedlogin', 'initialsetupenable'):
            config.remove_option('daemon', key)
    for key, value in [('AutomaticLoginEnable', 'True'), ('AutomaticLogin', 'a'),
                       ('TimedLoginEnable', 'False'), ('InitialSetupEnable', 'false')]:
        config.set('daemon', key, value)
    result = io.StringIO()
    config.write(result, space_around_delimiters=False)
    return result.getvalue()


def properties(text):
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def accepted_session(p, uid):
    return (p.get('Name') == 'a' and p.get('User') == str(uid) and p.get('Type') == 'wayland'
            and p.get('Class') == 'user' and p.get('Active') == 'yes'
            and p.get('State') == 'active' and p.get('Remote') == 'no')


def command(args, seconds=4):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=seconds)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('bounded desktop command unavailable or timed out') from None
    if p.returncode:
        raise ValueError('bounded desktop command failed')
    return p.stdout


def process_ids(uid, executable):
    result = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid == uid and (entry/'exe').resolve().name == executable:
                result.append(entry.name)
        except OSError:
            continue
    return result


def user_session(uid):
    global OBSERVATIONS
    ids = command(['loginctl', 'show-user', 'a', '-p', 'Sessions', '--value']).split()
    observed = []
    for sid in ids[:8]:
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}', sid):
            continue
        p = properties(command(['loginctl', 'show-session', sid, '--all',
                                '-p', 'Name', '-p', 'User', '-p', 'Type', '-p', 'Class',
                                '-p', 'Active', '-p', 'State', '-p', 'Remote', '-p', 'Desktop']))
        observed.append({key: value for key, value in p.items()
                         if key in ['User', 'Name', 'Type', 'Class', 'Active', 'State', 'Remote', 'Desktop']
                         and re.fullmatch(r'[a-zA-Z0-9_:.-]{0,80}', value)})
        if accepted_session(p, uid):
            OBSERVATIONS = observed
            return p
    OBSERVATIONS = observed
    return None


def socket_file(path):
    try:
        return stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        return False


def check(check_wifi=True):
    import pwd
    uid = pwd.getpwnam('a').pw_uid
    if uid < 1000:
        raise ValueError('test account must be an ordinary user')
    until = time.monotonic() + 25
    session = None
    while time.monotonic() < until:
        session = user_session(uid)
        if session:
            break
        time.sleep(1)
    if not session:
        raise ValueError('active ordinary GNOME Wayland session not found')
    COMPLETED['ordinary_user_wayland_session'] = True
    runtime = Path('/run/user')/str(uid)
    displays = [p for p in runtime.glob('wayland-*') if socket_file(p)]
    if not displays or not socket_file(runtime/'bus'):
        raise ValueError('user Wayland socket or session bus missing')
    shells = process_ids(uid, 'gnome-shell')
    if len(shells) != 1:
        raise ValueError('one user GNOME shell required')
    maps = (Path('/proc')/shells[0]/'maps').read_text()
    if '/usr/lib/erhai/mesa/lib/' not in maps:
        raise ValueError('ordinary user shell does not map project Mesa')
    COMPLETED['user_shell_project_mesa'] = True
    user = ['runuser', '-u', 'a', '--', 'env', 'XDG_RUNTIME_DIR='+str(runtime),
            'DBUS_SESSION_BUS_ADDRESS=unix:path='+str(runtime/'bus')]
    dock_expected={'dock-fixed':'true','autohide':'false','intellihide':'false',
                   'dock-position':"'LEFT'",'extend-height':'true'}
    dock_actual={key:command(user+['gsettings','get',
        'org.gnome.shell.extensions.dash-to-dock',key]).strip() for key in dock_expected}
    if dock_actual!=dock_expected:
        raise ValueError('Ubuntu Dock is not configured to remain visible')
    COMPLETED['ubuntu_dock_always_visible']=True
    command(user + ['systemd-run', '--user', '--collect', '--unit=oneplus13-ram-settings',
                    '--property=RuntimeMaxSec=90s', '--setenv=WAYLAND_DISPLAY='+displays[0].name,
                    '/usr/bin/gnome-control-center', 'network'], 6)
    app_until = time.monotonic() + 10
    while time.monotonic() < app_until:
        if process_ids(uid, 'gnome-control-center'):
            break
        time.sleep(.5)
    else:
        raise ValueError('settings application did not start')
    COMPLETED['settings_process_started'] = True
    wifi = None
    if check_wifi:
        wifi = load_wifi_helper()
    interfaces = [p.name for p in Path('/sys/class/net').iterdir() if (p/'wireless').exists()]
    if check_wifi and len(interfaces) != 1:
        raise ValueError('exactly one WiFi interface required')
    for index in range(3):
        if index:
            time.sleep(5)
        if not user_session(uid):
            raise ValueError('ordinary user session exited')
        if not process_ids(uid, 'gnome-control-center'):
            raise ValueError('settings application exited')
        command(user + ['gdbus', 'call', '--session', '--dest', 'org.gnome.Settings',
                        '--object-path', '/org/gnome/Settings', '--method', 'org.freedesktop.DBus.Peer.Ping'])
        COMPLETED['settings_bus_replied'] = True
        for unit in ['gdm', 'bluetooth', 'oneplus13-ram-guard.service', 'oneplus13-ram-deadline.timer']:
            command(['systemctl', 'is-active', '--quiet', unit])
        if not (Path('/proc')/shells[0]).exists():
            raise ValueError('user compositor restarted')
        probe = probe_http(wifi, interfaces[0]) if check_wifi else None
        SAMPLES.append({'session_active': True, 'app_process_present': True, 'app_bus_replies': True,
                        'guard_timer_gdm_bluetooth_active': True, 'same_compositor': True,
                        'bound_wifi_http': probe})
        # Record short transfer/certificate failures, but do not repeat hung
        # processes or missing executables. No association or radio retries.
        if probe is not None and probe['returncode'] in (124, 126, 127):
            raise ValueError('bounded stability client unavailable or hung')
    return completed_result(check_wifi)


def completed_result(check_wifi):
    wifi_pass = check_wifi and len(SAMPLES) == 3 and all(s.get('bound_wifi_http',{}).get('passed') for s in SAMPLES)
    return {'software_acceptance_pass': True, 'wifi_stability_pass': wifi_pass,
            'wifi_tested': check_wifi,
            'combined_pass': wifi_pass, 'ordinary_user_wayland_session': True,
            'user_shell_project_mesa': True, 'settings_network_process_and_bus': True,
            'ubuntu_dock_always_visible': True,
            'session_observations': OBSERVATIONS,
            'samples': SAMPLES, 'completed_gates': COMPLETED,
            'physical_touch_tested': False, 'visual_screen_tested': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--configure', action='store_true')
    parser.add_argument('--software-only', action='store_true')
    args = parser.parse_args()
    try:
        ram_only(Path('/proc/mounts').read_text(), Path('/run/oneplus13/ram-session').exists())
        if args.configure:
            import pwd
            pwd.getpwnam('a')
            path = Path('/etc/gdm3/custom.conf')
            if path.is_symlink() or not path.is_file():
                raise ValueError('regular existing GDM configuration required')
            path.write_text(autologin_config(path.read_text()))
            print('RAM_DESKTOP_AUTOLOGIN_CONFIGURED')
        else:
            print('RAM_DESKTOP_RESULT '+json.dumps(check(check_wifi=not args.software_only), sort_keys=True))
    except (ValueError, OSError, KeyError, configparser.Error) as exc:
        # Do not publish arbitrary subprocess output, environment or identifiers.
        print('RAM_DESKTOP_RESULT '+json.dumps({'software_acceptance_pass': False,
                                               'failure_category': type(exc).__name__,
                                               'session_observations': OBSERVATIONS,
                                               'completed_gates': COMPLETED, 'samples': SAMPLES,
                                               'reason': str(exc) if isinstance(exc, ValueError) else 'desktop runtime unavailable'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
