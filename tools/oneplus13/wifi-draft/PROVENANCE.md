# Phone WiFi SAR wire support draft

Codex implements a transport-free codec under GPL-2.0-only. It is a new phone
adaptation component, not a power policy, driver integration, or radio acceptance.
No sockets, device operations, firmware commands or phone files are accessed.

The protocol is pinned to OnePlusOSS/
android_kernel_modules_and_devicetree_oneplus_sm8750 at
d50b305f7da9e14715a25120a4ac7b1a4b8b97c3. `sources.json` records exact public
file hashes. The build fetches these public sources, preserves their license
headers, and compiles the actual extracted `send_sar_limit_cmd_tlv` function
with a fake transport as an independent byte-for-byte oracle. Its original
copyright and license remain in the fetched source and generated oracle.
No private firmware, calibration, identifiers or credentials are included.

QCA vendor USER=6 and V2=7 map to WMI USER=7 and V2=8. QCA NONE=5 disables
SAR; this codec rejects it. BDF selectors are translated explicitly. The
single-message encoder validates every row before modifying output; the
decoder rejects extra/truncated rows, unknown bands, duplicates and disabled
or unsupported selections. GET values are half-dBm limits, including when
the selection is V2; they must not be compared directly to V2 profile indexes.
Result parsing returns the actual firmware result separately from parser
success. Capability versions remain unmodified, including unknown values.

The vendor TLV table spells the GET row tag differently from its enum. The
codec uses numeric tag710, cross-checked against the enum and the kernel tag;
it does not silently copy that spelling mismatch into a driver.

Still required: ath12k WiFi7 integration, firmware capability/response collection,
serialization across queries and firmware restart, exact phone BDF/regdb and
firmware delivery, a phone-specific effective policy, enforcement before scans,
and current-rootfs scan/authentication/DHCP/verified-HTTPS acceptance. Earlier
run66 network traffic used reference inputs and cannot substitute for this.
The current phone candidate and all prior stop ledgers remain unchanged.
# Dormant kernel transport follow-up

Follow-up after passive Android GET219/GET164: the vendor API advertises TAS
(QCA version5), with NONE/empty legacy limits and unresolved CTL state. This
does not prove unrestricted power or reveal a complete TAS policy. Do not
replace it with guessed USER limits or V2 indexes. No SET was sent. The codec
now preserves a valid OFF/empty WMI **observation**, while SET-to-NONE remains
refused. Actual-driver tests confirm GET observation under raw capability5
without making V2 SET eligible. Current host counts are15 codec and29 driver
groups, each release and ASan/UBSan/LSan. Native follow-up remains pending.

The first dormant integration already passed native ARM64 Actions40316488478,
including the real phone-sar/sar-wire/wmi/core objects with warnings as errors.
No module runtime, cfg80211 callback or phone RF qualification is implied.

`phone-sar.c` is new GPL-2.0-only code by Codex. The integration tool binds every
modified original file to kernel7a81d184e3098624d4342d8f4eff5e23674bcc9f and
preserves the original files' notices. It adds the actual WMI receive hooks,
EXT capability and nested EXT2 flags, serialized bounded GET and V2 SET/result/GET
observation, and lifecycle cancellation. Ambiguous timeout, duplicate/unexpected
events or firmware detach permanently close this instance, including later SSR.
There is no automatic query, command, policy replay, cfg80211 callback or RF gate.
Sleepable internal callers must hold the existing core lifecycle mutex.

`check-driver.py` compiles the **actual driver translation unit** with synthetic
transport/completions and real pthread locks. Four static assertions bind command
and event IDs to the actual pinned vendor enum, separately from the TLV codec
oracle. Twenty-eight groups pass release and ASan/UBSan/LSan on the host. This
does not exercise the real kernel scheduler or establish firmware compatibility.
That initial native ARM64 and kernel object compilation subsequently passed as
recorded above; the empty-table follow-up requires its own native result.

The V2 API returns the raw SAR2 result and a complete GET table as observations.
V2 SET indexes and GET half-dBm limits are deliberately never compared as one
unit. Successful observation does not qualify an effective phone power policy;
no production caller or RF admission code is attached. Unknown version/CTL flag,
missing required flags or USER mode are refused by this V2-only API. Exact phone
firmware delivery and qualified phone policy remain necessary before radio use.

