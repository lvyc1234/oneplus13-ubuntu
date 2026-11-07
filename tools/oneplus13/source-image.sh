#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Fresh source builds only. No Actions run IDs or old binary image inputs.
set -euo pipefail
cd "$(dirname "$0")/../.."
qemu_args=()
runtime_checks=static
if [[ ${ONEPLUS13_SOURCE_QEMU:-false} == true ]]; then
  qemu_args+=(--qemu)
  runtime_checks=qemu
fi
case "${1:-}" in
  kernel)
    bash tools/oneplus13/build.sh
    python3 tools/oneplus13/verify.py --artifacts out/oneplus13 "${qemu_args[@]}"
    ;;
  rootfs)
    sudo env ONEPLUS13_DESKTOP_PROFILE="${ONEPLUS13_DESKTOP_PROFILE:-full}" bash tools/oneplus13/rootfs.sh
    sudo python3 tools/oneplus13/verify-rootfs.py out/oneplus13-rootfs-artifacts --report out/oneplus13-rootfs-artifacts/download-verification.json
    ;;
  mesa)
    readarray -t pin < <(python3 -c 'import json; p=json.load(open("packaging/mesa/source.json"));print(p["source_url"]);print(p["sha256"])')
    mkdir -p cache
    curl --fail --location --retry 2 -o cache/mesa.tar.xz "${pin[0]}"
    printf '%s  %s\n' "${pin[1]}" cache/mesa.tar.xz | sha256sum --check -
    python3 tools/build-mesa.py --source-archive cache/mesa.tar.xz --profile desktop --output out/mesa-desktop --jobs 4
    python3 tools/package-mesa.py --build-output out/mesa-desktop --output out/mesa-debs
    ;;
  assemble)
    sudo python3 tools/oneplus13/assemble-source-image.py --base out/oneplus13-rootfs-artifacts --kernel out/oneplus13 --mesa out/mesa-debs --output out/oneplus13-source-image
    sudo python3 tools/oneplus13/desktop.py --source-build --kernel out/oneplus13 --rootfs out/oneplus13-source-image/rootfs.squashfs --output out/oneplus13-source-desktop "${qemu_args[@]}"
    ;;
  complete)
    python3 tools/oneplus13/audio-draft/check.py --output out/phone-audio-synthetic
    python3 tools/oneplus13/audio-draft/build-topology-drafts.py --output out/phone-audio
    python3 tools/oneplus13/build-video-runtime.py --output out/phone-video
    python3 tools/oneplus13/verify_qualified_data.py devices/oneplus13/qualified-data
    sudo python3 tools/oneplus13/package-source-local.py --kernel out/oneplus13 --desktop out/oneplus13-source-desktop --rootfs out/oneplus13-source-image --qualified-data devices/oneplus13/qualified-data --runtime-checks "$runtime_checks" --output out/oneplus13-complete-image
    sudo python3 tools/oneplus13/build-microphone-boot.py --kernel out/oneplus13 --image out/oneplus13-complete-image
    python3 tools/oneplus13/review-modem-firmware.py \
      --firmware devices/oneplus13/qualified-data/firmware/qcom/sm8750/oneplus/dodge \
      --stock devices/oneplus13/qualified-data/calibrated.dtb \
      --report out/oneplus13/modem-firmware-review.json
    sudo python3 tools/oneplus13/build-modem-probe-boot.py --kernel out/oneplus13 --image out/oneplus13-complete-image \
      --firmware devices/oneplus13/qualified-data/firmware/qcom/sm8750/oneplus/dodge \
      --firmware-review out/oneplus13/modem-firmware-review.json
    sudo python3 tools/oneplus13/build-camera-probe-boot.py --kernel out/oneplus13 --image out/oneplus13-complete-image
    sudo python3 tools/oneplus13/build-camera-capture-boot.py --kernel out/oneplus13 --image out/oneplus13-complete-image
    sudo python3 tools/oneplus13/build-video-probe-boot.py --kernel out/oneplus13 --image out/oneplus13-complete-image
    sudo python3 tools/oneplus13/build-camera-capture-modem-boot.py --kernel out/oneplus13 --image out/oneplus13-complete-image
    sudo python3 tools/oneplus13/build-targets-boot.py --kernel out/oneplus13 --image out/oneplus13-complete-image
    sudo python3 tools/oneplus13/build-targets-boot.py --kernel out/oneplus13 --image out/oneplus13-complete-image --automatic-otg
    complete_args=(--static-only)
    if [[ $runtime_checks == qemu ]]; then complete_args=(); fi
    sudo python3 tools/oneplus13/verify-complete-image.py --kernel out/oneplus13 --image out/oneplus13-complete-image "${complete_args[@]}"
    ;;
  persistent)
    sudo python3 tools/oneplus13/build-persistent-bundle.py
    ;;
  *) echo 'Usage: source-image.sh {kernel|rootfs|mesa|assemble|complete|persistent}' >&2; exit 2 ;;
esac
