# OnePlus13 Resources diagnostic package

This is the generic GPL-3.0-or-later MSM/Adreno telemetry work by yzddmr6,
imported from the exact tree recorded in `ubuntu/source.json` of
https://github.com/yzddmr6/xiaomipad-8pro-mainline. Original patch authors,
attribution and COPYING are retained. No tablet firmware or calibration is used.
`upstream/` contains the generic patches; `ubuntu/` contains their pinned Ubuntu
1.10.2 source inputs and context backport. The local package version is
`1.10.2-0ubuntu3.1+op13.1`.

`tools/oneplus13/build-resources.sh` verifies the three Ubuntu source archive
hashes, applies the backport, uses Debian's ARM64 build/test rules in a disposable
Ubuntu chroot, and publishes binaries plus corresponding patched source.
No program is run against a phone. Missing telemetry remains unavailable;
maximum frequency is not substituted for a missing live frequency, and
platform DRM clients must resolve to the actual card instead of defaulting to0.

The new package is a candidate pending OnePlus13 device acceptance. It does
not transfer Xiaomi hardware acceptance to the phone. Do not permanently pin
an old package against security updates; rebuild/drop the backport when Ubuntu
ships equivalent telemetry, and repeat the relevant tests.
