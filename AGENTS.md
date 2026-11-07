# OnePlus 13 Ubuntu integration

The main branch targets OnePlus 13 dodge (PJZ110, 24 GB RAM / 1 TB storage).
Read README.md, docs/INSTALL.md and docs/oneplus13/STATUS.md before changes.
Preserve the accepted persistent A/B startup, native GPU, touch, Wi-Fi,
rotation, charging, power-key and off-mode charging behavior.

Build through .github/workflows/oneplus13-source-image.yml. Use source-built
components from the same commit. Fresh images use GNOME Initial Setup and
contain no normal account, password, machine identity or saved network.
Run relevant existing tests on Linux and git diff --check before publishing.
Physical acceptance applies only to the tested image and configuration.

The onepluspad-2pro-mainline branch mirrors the original upstream main.
Use sync-upstream.yml for fast-forward synchronization. Keep phone changes
on main. Retain upstream copyright, LICENSE, NOTICE, third-party license
files and pinned source provenance. qbootctl is a separate GPL-3.0-or-later
program. Firmware has separate vendor terms.

Keep credentials, device identifiers, private keys, personal root filesystems
and raw private logs out of Git and artifacts. Device operations require the
user's authorization in the current task. AI-created commits identify Codex
as their author. Never forge another person's sign-off or test attestation.
