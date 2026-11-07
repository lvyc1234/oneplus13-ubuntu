#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run in the dedicated CI VM with sudo' >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { echo 'Native ARM64 build host required' >&2; exit 1; }
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$repo"
root="$repo/out/oneplus13-rootfs-stage"
dest="$repo/out/oneplus13-rootfs-artifacts"
mkdir -p "$root" "$dest" "$repo/cache"
[[ ! -e $root/etc/os-release ]] || { echo 'Use a fresh staging directory' >&2; exit 1; }
profile=${ONEPLUS13_DESKTOP_PROFILE:-full}
case "$profile" in
  full) profile_file=devices/oneplus13/ubuntu.json ;;
  diagnostic) profile_file=devices/oneplus13/ubuntu-diagnostic.json ;;
  *) echo 'Unknown desktop profile' >&2; exit 2 ;;
esac
export ONEPLUS13_PROFILE_FILE="$profile_file"
readarray -t pin < <(python3 - <<'PY'
import json,os
s=json.load(open(os.environ['ONEPLUS13_PROFILE_FILE']))
print(s['base_url']); print(s['base_sha256']); print(s['apt_uri'])
print(' '.join(s['packages']))
PY
)
snapshot=${pin[2]%/}
snapshot=${snapshot##*/}
base="$repo/cache/oneplus13-ubuntu-base.tar.gz"
curl --fail --location --retry 5 --retry-delay 10 --retry-max-time 180 --output "$base" "${pin[0]}"
printf '%s  %s\n' "${pin[1]}" "$base" | sha256sum --check -
tar --extract --gzip --numeric-owner --preserve-permissions --file "$base" --directory "$root"
# Ubuntu Base intentionally omits ca-certificates. Bootstrap HTTPS using the
# runner's standard trust bundle; apt replaces it when installing the target
# ca-certificates package. Never disable TLS verification or archive signatures.
install -D -m 0644 /etc/ssl/certs/ca-certificates.crt "$root/etc/ssl/certs/ca-certificates.crt"
# A restored signed index is already the transport authority for this build.
# Probe the live snapshot only when no signed cache is available: the snapshot
# service can reject a redundant diagnostic request while serving cached
# authenticated indexes and packages correctly.
if [[ ! -d "$repo/cache/apt-lists/$snapshot/lists" ]]; then
  curl --fail --silent --show-error --retry 5 --retry-delay 10 --retry-max-time 180 --output /dev/null "${pin[2]}dists/resolute/InRelease"
fi
test -r "$root/etc/ssl/certs/ca-certificates.crt"
cat > "$root/etc/apt/apt.conf.d/80bootstrap-ca" <<'EOF'
Acquire::https::CaInfo "/etc/ssl/certs/ca-certificates.crt";
Acquire::Retries "5";
Acquire::Languages "none";
Acquire::https::Timeout "30";
EOF
# The archive's symlink layout is preserved; never copy the host machine identity.
printf 'nameserver 1.1.1.1\n' > "$root/etc/resolv.conf"
cat > "$root/etc/apt/sources.list.d/ubuntu.sources" <<EOF
Types: deb
URIs: ${pin[2]}
Suites: resolute resolute-updates resolute-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
rm -f "$root/etc/apt/sources.list"
cat > "$root/usr/sbin/policy-rc.d" <<'EOF'
#!/bin/sh
exit 101
EOF
chmod 755 "$root/usr/sbin/policy-rc.d"
cleanup() {
  for d in run sys proc dev; do
    if mountpoint -q "$root/$d"; then umount -R "$root/$d"; fi
  done
}
trap cleanup EXIT
for d in dev proc sys run; do
  mount --rbind "/$d" "$root/$d"
  mount --make-rslave "$root/$d"
done
if [[ -d "$repo/cache/apt-lists/$snapshot/lists" ]]; then
  install -d -m 0755 "$root/var/lib/apt/lists"
  cp -a "$repo/cache/apt-lists/$snapshot/lists/." "$root/var/lib/apt/lists/"
else
  chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
    apt-get -o APT::Update::Error-Mode=any update
fi
# Read the exact package set from the locked JSON. Package versions are fixed by
# the signed Ubuntu snapshot and recorded in the delivered manifest.
read -r -a packages <<< "${pin[3]}"
recommend_args=(--install-recommends)
if [[ $profile == diagnostic ]]; then recommend_args=(--no-install-recommends); fi
# Resolve the standard Ubuntu Desktop metapackage and its recommended apps from
# the validated snapshot, then fetch packages in bounded batches. Cached bytes
# are accepted only against this plan's size and SHA256.
chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
  apt-get -o Acquire::ForceHash=SHA256 --print-uris --download-only \
  install -y "${recommend_args[@]}" "${packages[@]}" > "$dest/apt-package-plan.txt"
python3 tools/oneplus13/prefetch-rootfs-debs.py \
  --plan "$dest/apt-package-plan.txt" --apt-uri "${pin[2]}" \
  --cache "$repo/cache/oneplus13-rootfs-debs" \
  --archives "$root/var/cache/apt/archives" --report "$dest/deb-prefetch.json"
time chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
  apt-get install --no-download -y "${recommend_args[@]}" "${packages[@]}"
chroot "$root" locale-gen en_US.UTF-8 zh_CN.UTF-8
test -f "$root/usr/share/zoneinfo/Asia/Shanghai"
ln -sfn /usr/share/zoneinfo/Asia/Shanghai "$root/etc/localtime"
printf 'Asia/Shanghai\n' > "$root/etc/timezone"
cat > "$root/etc/default/locale" <<'EOF'
LANG=zh_CN.UTF-8
LANGUAGE=zh_CN:zh
LC_MESSAGES=zh_CN.UTF-8
EOF
cat > "$root/etc/profile.d/90-oneplus13-locale.sh" <<'EOF'
export LANG=zh_CN.UTF-8
export LANGUAGE=zh_CN:zh
export LC_MESSAGES=zh_CN.UTF-8
EOF
# Mutter's rotated fractional-scale damage can extend one pixel outside the
# scanout buffer. Full GPU redraws avoid rejected DRM damage clips on this phone.
cat > "$root/etc/environment" <<'EOF'
LANG=zh_CN.UTF-8
LANGUAGE=zh_CN:zh
LC_MESSAGES=zh_CN.UTF-8
GSK_RENDERER=gl
CLUTTER_PAINT=disable-clipped-redraws
EOF
gdm_conf="$root/etc/gdm3/custom.conf"
if grep -Eq '^[[:space:]]*#?[[:space:]]*InitialSetupEnable[[:space:]]*=' "$gdm_conf"; then
  sed -i -E 's/^[[:space:]]*#?[[:space:]]*InitialSetupEnable[[:space:]]*=.*/InitialSetupEnable=true/' "$gdm_conf"
elif grep -q '^\[daemon\]' "$gdm_conf"; then
  sed -i '/^\[daemon\]/a InitialSetupEnable=true' "$gdm_conf"
else
  printf '\n[daemon]\nInitialSetupEnable=true\n' >> "$gdm_conf"
fi
grep -q '^InitialSetupEnable=true$' "$gdm_conf"
echo oneplus13-linux > "$root/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 oneplus13-linux\n::1 localhost ip6-localhost\n' > "$root/etc/hosts"
mkdir -p "$root/etc/systemd/system" "$root/etc/systemd/logind.conf.d"
ln -sfn /usr/lib/systemd/system/graphical.target "$root/etc/systemd/system/default.target"
cat > "$root/etc/systemd/logind.conf.d/50-oneplus13-bringup.conf" <<'EOF'
[Login]
HandlePowerKey=ignore
IdleAction=ignore
EOF
# Suspend/resume is not established on the phone; prevent an unattended desktop
# from entering an untested sleep path. This is an explicit staging policy.
for unit in sleep.target suspend.target hibernate.target hybrid-sleep.target; do
  ln -sfn /dev/null "$root/etc/systemd/system/$unit"
done
chroot "$root" passwd -l root
# Supply the real engine and both firewall frontends, but do not create a
# docker0 bridge or modify the phone's forwarding policy during qualification.
# The owner can start docker.service explicitly in the RAM desktop.
if [[ $profile == full ]]; then
  chroot "$root" systemctl disable docker.service docker.socket containerd.service
  install -D -m 0644 devices/oneplus13/docker/daemon.json "$root/etc/docker/daemon.json"
fi
install -D -m 0644 devices/oneplus13/desktop/90-oneplus13-wifi-identity.conf \
  "$root/etc/NetworkManager/conf.d/90-oneplus13-wifi-identity.conf"
mkdir -p "$root/etc/ssh/sshd_config.d"
cat > "$root/etc/ssh/sshd_config.d/60-oneplus13.conf" <<'EOF'
PasswordAuthentication yes
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
EOF
cat > "$root/usr/lib/systemd/system/oneplus13-ssh-host-keys.service" <<'EOF'
[Unit]
Description=Generate volatile OnePlus 13 SSH host keys
RequiresMountsFor=/etc/ssh
Before=ssh.service

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A
ExecStartPost=/usr/sbin/sshd -t
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
mkdir -p "$root/etc/systemd/system/multi-user.target.wants"
ln -sfn /usr/lib/systemd/system/oneplus13-ssh-host-keys.service \
  "$root/etc/systemd/system/multi-user.target.wants/oneplus13-ssh-host-keys.service"
ln -sfn /usr/lib/systemd/system/ssh.service \
  "$root/etc/systemd/system/multi-user.target.wants/ssh.service"
ln -sfn /usr/lib/systemd/system/rmtfs.service \
  "$root/etc/systemd/system/multi-user.target.wants/rmtfs.service"
ln -sfn /usr/lib/systemd/system/tqftpserv.service \
  "$root/etc/systemd/system/multi-user.target.wants/tqftpserv.service"

install -Dm0644 devices/oneplus13/rmtfs-uio.rules "$root/etc/udev/rules.d/90-oneplus13-rmtfs-uio.rules"
install -Dm0644 devices/oneplus13/rmtfs-wait.conf \
  "$root/etc/systemd/system/rmtfs.service.d/50-oneplus13-wait.conf"
rm -f "$root"/etc/ssh/ssh_host_*
desktop_args=("$root" --profile "$profile")
if [[ -n ${ONEPLUS13_KERNEL_ARTIFACT:-} ]]; then
  desktop_args+=(--kernel "$ONEPLUS13_KERNEL_ARTIFACT")
fi
if [[ -n ${ONEPLUS13_STOCK_FIRMWARE:-} ]]; then
  desktop_args+=(--firmware "$ONEPLUS13_STOCK_FIRMWARE")
fi
python3 tools/oneplus13/install-desktop.py "${desktop_args[@]}"
resource_debs=("$repo"/out/oneplus13-resources/resources_*_arm64.deb)
[[ ${#resource_debs[@]} == 1 && -f ${resource_debs[0]} ]] || { echo 'Matching Resources artifact required' >&2; exit 1; }
python3 - "$repo" <<'PY'
import hashlib,json,pathlib,subprocess,sys
repo=pathlib.Path(sys.argv[1]);out=repo/'out/oneplus13-resources'
m=json.loads((out/'manifest.json').read_text())
assert m['source_commit']==subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
assert m['hardware_tested'] is False and m['corresponding_source_included'] is True
for name,digest in m['files'].items():
 assert pathlib.Path(name).name==name
 assert hashlib.file_digest((out/name).open('rb'),'sha256').hexdigest()==digest
PY
cp "${resource_debs[0]}" "$root/tmp/oneplus13-resources.deb"
# Dependencies were resolved above using Ubuntu's Resources package. Install
# the verified local replacement directly; dpkg still refuses unmet Depends.
# APT's --no-download local-file path handling rejects the renamed archive.
chroot "$root" dpkg --install /tmp/oneplus13-resources.deb
test -z "$(chroot "$root" dpkg --audit)"
rm "$root/tmp/oneplus13-resources.deb"
python3 tools/oneplus13/install-user-diagnostics.py "$root"
glib-compile-schemas --strict "$root/usr/share/glib-2.0/schemas"
test "$(chroot "$root" env GSETTINGS_BACKEND=memory gsettings get org.gnome.desktop.a11y.applications screen-keyboard-enabled)" = true
test "$(chroot "$root" env GSETTINGS_BACKEND=memory gsettings get org.gnome.desktop.interface toolkit-accessibility)" = true
chroot "$root" /usr/bin/systemd-analyze verify \
  oneplus13-ram-guard.service oneplus13-ram-deadline.timer \
  oneplus13-ram-evidence.service oneplus13-ram-usb.service \
  oneplus13-ram-dhcp.service oneplus13-ram-http.service \
  oneplus13-ram-shell.service oneplus13-ram-serial.service oneplus13-firstboot-hardware.service \
  ssh.service oneplus13-ssh-host-keys.service rmtfs.service tqftpserv.service
chroot "$root" /usr/bin/systemd-analyze verify \
  oneplus13-display-power.socket oneplus13-display-power.service oneplus13-power-key@event0.service
udevadm verify "$root/usr/lib/udev/rules.d/10-oneplus13-ram-storage.rules"
udevadm verify "$root/usr/lib/udev/rules.d/82-oneplus13-power-key.rules"
chroot "$root" dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > "$dest/packages.tsv"
chroot "$root" /usr/bin/gnome-shell --version > "$dest/gnome-version.txt"
chroot "$root" /usr/bin/env bash -n /etc/profile
chroot "$root" dpkg-query -W systemd-resolved > /dev/null
chroot "$root" locale -a | grep -qx 'zh_CN.utf8'
test -x "$root/usr/sbin/sshd"
if [[ $profile == full ]]; then
  test -x "$root/usr/bin/docker"
  test -x "$root/usr/bin/dockerd"
  test -x "$root/usr/sbin/iptables-nft"
  test -x "$root/usr/sbin/iptables-legacy"
fi
test -x "$root/usr/bin/resources"
test -x "$root/usr/bin/gnome-system-monitor"
grep -qx 'PermitRootLogin no' "$root/etc/ssh/sshd_config.d/60-oneplus13.conf"
! grep -q '^AllowUsers ' "$root/etc/ssh/sshd_config.d/60-oneplus13.conf"
if awk -F: '$3 >= 1000 && $3 < 60000 { found=1 } END { exit found ? 0 : 1 }' "$root/etc/passwd"; then
  echo 'Refusing to ship a precreated desktop user' >&2
  exit 1
fi
test -x "$root/usr/sbin/gdm3"
test -x "$root/usr/libexec/gnome-initial-setup"
test ! -e "$root/etc/ssh/ssh_host_rsa_key"
test -L "$root/etc/systemd/system/multi-user.target.wants/ssh.service"
chroot "$root" apt-get clean
rm -f "$root/etc/apt/apt.conf.d/80bootstrap-ca"
rm -f "$root/usr/sbin/policy-rc.d"
# qmicli performs one action per process and therefore closes the QRTR client
# before a follow-up request can use it. Compile the bounded one-session helper
# against the same runtime library ABI packaged in the rootfs.
cc -O2 -Wall -Wextra -Werror \
  tools/oneplus13/location-session.c \
  -o "$root/usr/libexec/oneplus13-location-session" \
  $(pkg-config --cflags --libs qmi-glib qrtr-glib gio-2.0)
strip "$root/usr/libexec/oneplus13-location-session"
test -x "$root/usr/libexec/oneplus13-location-session"
chroot "$root" /usr/libexec/oneplus13-location-session --self-test
if chroot "$root" /usr/bin/ldd /usr/libexec/oneplus13-location-session |
    grep -q 'not found'; then
  echo 'Location-session helper has an unresolved runtime library' >&2
  exit 1
fi
cleanup
trap - EXIT
# Remove transient and machine-specific state only inside this fresh staging tree.
find "$root/var/lib/apt/lists" -type f -delete
find "$root/var/log" -type f -exec truncate -s 0 {} +
truncate -s 0 "$root/etc/machine-id"
rm -f "$root/var/lib/dbus/machine-id"
rm -f "$root/var/lib/systemd/random-seed"
ln -s /etc/machine-id "$root/var/lib/dbus/machine-id"
rm -f "$root/etc/resolv.conf"
ln -s /run/systemd/resolve/stub-resolv.conf "$root/etc/resolv.conf"
epoch=$(git show -s --format=%ct HEAD)
time mksquashfs "$root" "$dest/rootfs.squashfs" -noappend -comp zstd \
  -processors "$(nproc)" -mkfs-time "$epoch" -all-time "$epoch" -no-progress
unsquashfs -s "$dest/rootfs.squashfs" > "$dest/squashfs-info.txt"
unsquashfs -cat "$dest/rootfs.squashfs" etc/os-release > "$dest/os-release"
cp "$profile_file" "$dest/ubuntu-source.json"
cp docs/oneplus13/README.md "$dest/README.md"
test -z "$(unsquashfs -cat "$dest/rootfs.squashfs" etc/machine-id)"
unsquashfs -cat "$dest/rootfs.squashfs" etc/shadow | \
  awk -F: '$1 == "root" { if ($2 !~ /^[!*]/) exit 1; found=1 } END { if (!found) exit 1 }'
python3 - "$dest" <<'PY'
import hashlib,json,pathlib,subprocess,sys
out=pathlib.Path(sys.argv[1])
sha=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
result={'status':'OFFLINE_ROOTFS_BUILD_NOT_DEVICE_TESTED','hardware_tested':False,
 'integration_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
 'desktop_profile':json.loads((out/'ubuntu-source.json').read_text())['desktop_profile'],
 'user_account':'No precreated user or autologin; GDM launches GNOME Initial Setup to create the first user',
 'kernel_modules':'Delivered separately with the matching kernel artifact',
 'files':{p.name:{'sha256':sha(p),'size':p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()}}
(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
(out/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in sorted(out.iterdir()) if p.is_file() and p.name!='SHA256SUMS'))
PY
