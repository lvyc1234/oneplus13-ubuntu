#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install and observe the reviewed OnePlus 13 desktop sensor stack in RAM.

The setup phase only writes inside the protected RAM overlay.  The check phase
does not claim sensors: after the desktop starts, GNOME Settings Daemon owns the
accelerometer and Mutter applies the automatic display transform.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import runpy
import subprocess
import time
from pathlib import Path

ROOT = Path('/')
BINDINGS = Path('/usr/lib/oneplus13-sensors/runtime-bindings.json')
MARKER = Path('/run/oneplus13/sensor-runtime-ready.json')
FILESERVER = 'oneplus13-sensor-fileserver.service'
PROXY = 'oneplus13-sensor-proxy.service'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_tree(root, entries, persistent=False):
    """Verify regular files and the three reviewed absolute data mappings."""
    for name, (content, mode, target) in entries.items():
        path = Path(root) / name
        if target is not None:
            if not path.is_symlink() or str(path.readlink()) != target:
                raise ValueError('Sensor runtime mapping changed')
        elif not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o777 != mode:
            raise ValueError('Sensor runtime mode or file type changed')
        elif persistent and name.startswith('var/lib/erhai/sensors/'):
            # SSC updates its registry and version on the installed system.
            continue
        elif sha(path) != hashlib.sha256(content).hexdigest():
            raise ValueError('Sensor runtime bytes changed')


def existing_tree(root, entries):
    return any((Path(root) / name).exists() or (Path(root) / name).is_symlink()
               for name in entries)


def setup(root=ROOT, bindings=BINDINGS, marker=MARKER, chown=os.chown,
          persistent=False):
    root = Path(root)
    bindings = Path(bindings)
    marker = Path(marker)
    if not root.is_relative_to(ROOT):
        raise ValueError('Sensor setup root is outside the host namespace')
    if persistent:
        hardware = runpy.run_path(str(root / 'run/oneplus13/persistent/persistent-hardware.py'))
        hardware['validate_root'](root)
    else:
        if not (root / 'run/oneplus13/ram-session').is_file():
            raise ValueError('Protected RAM session required')
        if not any(len(row := line.split()) >= 3 and row[1:3] == ['/', 'overlay']
                   for line in (root / 'proc/mounts').read_text().splitlines()):
            raise ValueError('RAM overlay required')
    doc = json.loads(bindings.read_text())
    if doc.get('format') != 'ONEPLUS13_IMAGE_SENSOR_INPUTS_V1':
        raise ValueError('Unreviewed sensor bindings')
    archive = Path(doc['inputs']['archive']['path'])
    manifest_path = Path(doc['inputs']['manifest']['path'])
    for path, expected in ((archive, doc['inputs']['archive']['sha256']),
                           (manifest_path, doc['inputs']['manifest']['sha256'])):
        if path.is_symlink() or sha(path) != expected:
            raise ValueError('Sensor setup input changed')
    manifest = json.loads(manifest_path.read_text())
    fs = load('reviewed_sensor_fileserver',
              root / 'usr/libexec/oneplus13-ram-sensor-fileserver.py')
    entries = fs.archive_entries(archive, manifest)

    if existing_tree(root, entries):
        verify_tree(root, entries, persistent=persistent)
    else:
        fs.install(root, entries, chown=chown)
        verify_tree(root, entries)
    data = {
        'scope': 'ONEPLUS13_RAM_DESKTOP_SENSOR_RUNTIME_READY',
        'archive_sha256': doc['inputs']['archive']['sha256'],
        'manifest_sha256': doc['inputs']['manifest']['sha256'],
        'native_elf_count': manifest['native_elf_count'],
        'data_files': manifest['text_data_files'],
        'attach_started': False,
        'physical_storage_writes': None if persistent else 0,
    }
    marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker.write_text(json.dumps(data, sort_keys=True) + '\n')
    marker.chmod(0o644)
    return data


def service_active(run=subprocess.run):
    result = run(['systemctl', 'is-active', '--quiet', FILESERVER, PROXY],
                 capture_output=True, text=True, timeout=6)
    if result.returncode:
        raise ValueError('Desktop sensor services are not active')


def system_posture(bus):
    import gi
    gi.require_version('GLib', '2.0')
    from gi.repository import GLib
    value = bus.call_sync(
        'net.hadess.SensorProxy', '/net/hadess/SensorProxy',
        'org.freedesktop.DBus.Properties', 'GetAll',
        GLib.Variant('(s)', ('net.hadess.SensorProxy',)),
        GLib.VariantType.new('(a{sv})'), 0, 2000, None)
    properties = value.unpack()[0]
    orientation = properties.get('AccelerometerOrientation')
    tilt = properties.get('AccelerometerTilt')
    if orientation not in {'undefined', 'normal', 'bottom-up', 'left-up', 'right-up'}:
        raise ValueError('Unexpected SensorProxy orientation')
    if tilt not in {'undefined', 'vertical', 'tilted-up', 'tilted-down',
                    'face-up', 'face-down'}:
        raise ValueError('Unexpected SensorProxy tilt')
    return orientation, tilt


def system_orientation(bus):
    return system_posture(bus)[0]


def parse_display_transforms(value):
    transforms = []
    for monitor in value[2]:
        if len(monitor) < 4 or type(monitor[3]) is not int:
            raise ValueError('Unrecognized Mutter logical-monitor state')
        transforms.append(monitor[3])
    if not transforms or any(value not in (0, 1, 2, 3) for value in transforms):
        raise ValueError('Unrecognized Mutter monitor transform')
    return tuple(transforms)


def display_transforms(bus):
    import gi
    gi.require_version('GLib', '2.0')
    from gi.repository import GLib
    value = bus.call_sync(
        'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', None,
        GLib.VariantType.new('(ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv})'),
        0, 2000, None).unpack()
    return parse_display_transforms(value)


def collect(seconds, orientation, transforms, clock=time.monotonic, sleep=time.sleep,
            posture=None):
    if type(seconds) is not int or seconds not in (20,40):
        raise ValueError('Only the twenty/forty-second desktop orientation windows are reviewed')
    orientations = []
    display_states = []
    posture_transform_sequence = []
    samples = 0
    until = clock() + seconds
    while clock() < until:
        current_posture = posture() if posture else None
        current_orientation = current_posture[0] if current_posture else orientation()
        current_transforms = transforms()
        samples += 1
        if samples > (128 if seconds == 20 else 192):
            raise ValueError('Orientation sample bound exceeded')
        if not orientations or orientations[-1] != current_orientation:
            orientations.append(current_orientation)
        if not display_states or display_states[-1] != list(current_transforms):
            display_states.append(list(current_transforms))
        state = {
            'orientation': current_orientation,
            'tilt': current_posture[1] if current_posture else None,
            'transforms': list(current_transforms),
        }
        if not posture_transform_sequence or posture_transform_sequence[-1] != state:
            posture_transform_sequence.append(state)
        sleep(min(.25, max(0, until - clock())))
    orientation_set = set(orientations)
    transform_set = {value for state in display_states for value in state}
    vertical_states = [state for state in posture_transform_sequence
                       if state['tilt'] == 'vertical']
    normal_portrait_transforms = sorted({transform for state in vertical_states
                                         if state['orientation'] == 'normal'
                                         for transform in state['transforms']})
    inverted_portrait_transforms = sorted({transform for state in vertical_states
                                           if state['orientation'] == 'bottom-up'
                                           for transform in state['transforms']})
    passed = (samples >= 20 and len(orientation_set) >= 3
              and len(transform_set) >= 2)
    return {
        'scope': 'AUTOMATIC_DESKTOP_ORIENTATION_OBSERVATION',
        'seconds': seconds,
        'samples': samples,
        'orientation_sequence': orientations,
        'display_transform_sequence': display_states,
        'posture_transform_sequence': posture_transform_sequence,
        'normal_portrait_transforms': normal_portrait_transforms,
        'bottom_up_portrait_transforms': inverted_portrait_transforms,
        'portrait_mapping_matches_upright': (
            normal_portrait_transforms == [0] if normal_portrait_transforms else None),
        'portrait_inversion_observed': (
            (2 in normal_portrait_transforms or 0 in inverted_portrait_transforms)
            if normal_portrait_transforms or inverted_portrait_transforms else None),
        'distinct_orientations': sorted(orientation_set),
        'distinct_display_transforms': sorted(transform_set),
        'automatic_rotation_pass': passed,
        'physical_axes_calibration_accepted': False,
    }


def collect_static_right_up(seconds, orientation, transforms,
                            clock=time.monotonic, sleep=time.sleep,
                            posture=None, expected_orientation=None):
    if type(seconds) is not int or seconds != 8:
        raise ValueError('Only the eight-second static right-up window is reviewed')
    orientations = set()
    tilts = set()
    display_states = []
    samples = 0
    until = clock() + seconds
    while clock() < until:
        current_posture = posture() if posture else None
        current_orientation = (current_posture[0] if current_posture
                               else orientation())
        current_transforms = transforms()
        samples += 1
        if samples > 64:
            raise ValueError('Static orientation sample bound exceeded')
        orientations.add(current_orientation)
        if current_posture:
            tilts.add(current_posture[1])
        if not display_states or display_states[-1] != list(current_transforms):
            display_states.append(list(current_transforms))
        sleep(min(.25, max(0, until - clock())))
    transforms_seen = {value for state in display_states for value in state}
    expected_transforms = {'normal': 0, 'left-up': 1, 'bottom-up': 2, 'right-up': 3}
    if expected_orientation is not None and expected_orientation not in expected_transforms:
        raise ValueError('Unknown expected upright orientation')
    upright_matches = (len(orientations) == 1 and
                       next(iter(orientations)) in expected_transforms and
                       transforms_seen == {expected_transforms[next(iter(orientations))]})
    expected_matches = (expected_orientation is None or
                        orientations == {expected_orientation})
    flat_pass = (orientations == {'undefined'} and transforms_seen == {0}
                 and bool(tilts) and tilts <= {'face-up', 'face-down'})
    passed = samples >= 8 and expected_matches and upright_matches
    return {
        'scope': 'STATIC_DESKTOP_ORIENTATION_OBSERVATION',
        'seconds': seconds,
        'samples': samples,
        'orientations': sorted(orientations),
        'tilts': sorted(tilts),
        'display_transform_sequence': display_states,
        'distinct_display_transforms': sorted(transforms_seen),
        'expected_orientation': expected_orientation,
        'upright_transform_matches': bool(upright_matches and expected_matches),
        'flat_zero_transform_pass': flat_pass,
        'static_pose_pass': bool(passed or (expected_orientation is None and flat_pass)),
        'automatic_rotation_pass': False,
        'physical_axes_calibration_accepted': False,
    }


def check(seconds=20, system_bus=None, session_bus=None):
    if not MARKER.is_file():
        raise ValueError('Sensor runtime setup marker missing')
    data = json.loads(MARKER.read_text())
    if data.get('scope') != 'ONEPLUS13_RAM_DESKTOP_SENSOR_RUNTIME_READY':
        raise ValueError('Sensor runtime setup marker changed')
    service_active()
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio
    system_bus = system_bus or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    session_bus = session_bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
    result = collect(seconds,
                     lambda: system_orientation(system_bus),
                     lambda: display_transforms(session_bus),
                     posture=lambda: system_posture(system_bus))
    if not result['automatic_rotation_pass']:
        raise ValueError('Automatic desktop rotation transition incomplete')
    return result


def check_static_right_up(seconds=8, system_bus=None, session_bus=None):
    if not MARKER.is_file():
        raise ValueError('Sensor runtime setup marker missing')
    data = json.loads(MARKER.read_text())
    if data.get('scope') != 'ONEPLUS13_RAM_DESKTOP_SENSOR_RUNTIME_READY':
        raise ValueError('Sensor runtime setup marker changed')
    service_active()
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio
    system_bus = system_bus or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    session_bus = session_bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
    result = collect_static_right_up(
        seconds,
        lambda: system_orientation(system_bus),
        lambda: display_transforms(session_bus),
        posture=lambda: system_posture(system_bus))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('setup', 'check', 'check-static'))
    parser.add_argument('--seconds', type=int, default=20)
    parser.add_argument('--persistent', action='store_true')
    args = parser.parse_args()
    try:
        if args.mode == 'setup':
            if os.geteuid() != 0:
                raise ValueError('Sensor setup requires root')
            result = setup(persistent=args.persistent)
        elif args.mode == 'check-static':
            result = check_static_right_up(args.seconds)
            if not result['static_pose_pass']:
                raise ValueError('Static posture and Mutter transform disagree: ' +
                                 json.dumps({
                                     'orientations': result['orientations'],
                                     'tilts': result['tilts'],
                                     'transforms': result['distinct_display_transforms'],
                                 }, sort_keys=True))
        else:
            result = check(args.seconds)
        print('RAM_SENSOR_DESKTOP_RESULT ' + json.dumps(result, sort_keys=True), flush=True)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print('RAM_SENSOR_DESKTOP_FAILED ' + json.dumps({
            'category': type(exc).__name__,
            'reason': str(exc) if isinstance(exc, ValueError) else 'sensor desktop runtime unavailable',
        }, sort_keys=True), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
