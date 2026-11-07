# Owner-authorized qualified hardware data

The owner explicitly authorized publication of the firmware and calibration on
2026-10-27, excluding credentials, keys, personal data and device identifiers.
The original 74 payloads are the unchanged hardware data used by the successful run90
RAM WiFi/HTTP acceptance, except that old `/chosen/bootargs` were removed from
the calibrated DTB. The packager generates new image hashes and session
parameters; no old test session, network configuration or USB serial is needed.

- 58 ADSP files: the phone's verified signed stock firmware, retained under the
  historical `qcom/sm8750/erhai` path required by the qualified DT. That directory
  name does not mean these bytes were taken from a tablet.
- Four GPU files: verified phone stock firmware (including product-signed ZAP).
- Nine WLAN/Bluetooth files: the exact previously qualified legacy reference
  set. These are deliberately not relabeled as new phone-stock radio firmware.
  Source provenance and hashes are in `packaging/firmware-manifest.json`;
  ROCKNIX extra-firmware is pinned there at
  `2b7207cfcb80c53c09dc6e04165789ecaf2d3348`.
- `display-profile.bin`: the validated 176-byte AA569 SPR data profile.
- `firmware/qcom/sm8750/oneplus/dodge/vpu35_4v.mbn`: the owner-provided C.93 vendor Iris VPU 3.5 image, retained under the phone-specific path named by the manual video DT candidate; its firmware authentication and decoding remain unqualified.
- `calibrated.dtb`: the qualified ABL/memory/device tree for the owner's
  **PJZ110 24 GB** variant, compatible only with the public native DTB hash in
  `manifest.json`. It contains no serial-number or MAC-address properties.
- `boot-geometry.json`: boot header address geometry, not a unique identifier.

Firmware remains proprietary data; the original vendor terms apply. Inclusion
does not relicense firmware under the repository's GPL. No raw partition images,
factory identity stores, persistent WiFi settings or cryptographic private keys
are included. Firmware signatures/certificates are public verification data,
not private signing keys. Every payload has a size and SHA-256 in the manifest.

This dataset reproduces the qualified experimental RAM configuration. It is not
a generic image for other RAM variants and does not qualify audio, cameras,
sensor streams, suspend or a new SAR transport. It does not authorize flashing.

## Reviewed sensor runtime text, 2026-10-28

The additional `sensors/reviewed-runtime-data.json` is a hash-bound text
container of922 configuration/registry/support files. It derives from the
owner's C.93 ROM configuration and owner-authorized per-unit sensor calibration
already exercised by the bounded vector diagnostics. It excludes the private
import ledger and both serial-related registry files. Key and content checks
reject serial/UUID/IMEI/IMSI/MAC/network credential fields, credentials and key
material. Six socinfo selectors are hardware model/revision choices, not a
device serial. No userspace binary, service hook or partition image is in it.

This data remains vendor configuration/calibration rather than GPL code.
Publication does not qualify copying this unit's calibration to another phone.
The sensor userspace is built separately from pinned public sources in Actions,
then joined to this allowlist. Its hardware revalidation remains required;
no sensor service or radio is automatically started by the dataset.

## Reviewed C.93 modem firmware, 2026-10-30

The owner also authorized the generic modem firmware needed for online builds.
The `qcom/sm8750/oneplus/dodge/modem*` files are the unchanged C.93 vendor
firmware splits and modem resource JSON, retained under the phone-specific
path required by the reviewed MPSS device tree. This set contains the signed
`modem.mdt` and `modem_dtb.mdt` role files plus their referenced split segments;
it contains no EFS, per-unit IMEI/IMSI store, SIM data, network credentials or
firmware caches from the phone.

The host review verifies every MDT program header, split length, hash-segment
identity and load span against the saved 246 MiB MPSS and 512 KiB DTB carveouts.
The owner-provided RAM test separately started MPSS, observed the modem GLINK
`IPCRTR` and service router endpoints, and returned it to offline without a
fault. Those runtime facts qualify this exact experiment only. Vendor terms
still apply, QSEE authentication remains performed by the device, and the data
does not authorize flashing, modem RF calibration copying or use on another
product.
