# Camera component source staging

These are public mailing-list changes by their original authors, preserved
with their original signoffs and source URLs/hashes in sources.json. Codex
prepared the selection, source-bound builder and isolated workflow; this is
not an upstream merged camera implementation or a claim of phone testing.

The first native W=1/-Werror build found an unused local left by the external
PHY conversion. A separate Codex correction removes that single declaration;
the original23 patches retain their hashes. The compiler gate stays enabled.

A second correction removes another obsolete local and checks the fallible
DPHY timing/mode calls before configure or power-on. The test builder extracts
that actual staged function, uses explicit hardware-free doubles and tests
seven success/failure boundaries with release and ASan/UBSan. This does not
validate electrical sequencing, generic-PHY implementation or real captures.

The sequence is X1E PHY v13 (two patches), CAMSS external PHY v12 (six),
Kaanapali v15 (eight), then SM8750 v6 (seven). All23 apply with fuzz=0 on
fresh affected source files from the actual phone kernel pin. Native object
compilation remains a separate gate. The source discussions contain unresolved
review/dependency comments; preserving the series is not an assertion that
those concerns disappeared. Driver SPDX notices remain authoritative; binding
files retain their original dual-license notices.

SM8750 support here covers RDI transport, CSID980, VFE980, TPG and standalone
CSI PHY support. It does not add the phone's populated sensor driver, board
power/clock/lane graph, ISP processing, autofocus/OIS or a libcamera pipeline.
Generic S5KJN5 capture reported by the patch author does not identify a sensor
in the owner's phone. The saved phone FDT additionally selects a custom
ultrawide PHY sequence; do not replace that with a generic sequence silently.

The builder also compiles the existing isolated AW88461 draft and runs its
synthetic parser/power failure checks against the same kernel configuration.
This supplies neither missing factory calibration nor dynamic protection.
No phone DTS, service, firmware, data, module installer or boot image is
generated, and no running candidate is overwritten.

2026-10-29: the isolated component builder also stages three disabled SM8750
CCI controllers, six child I2C hosts and camera MCLK pinctrl definitions from
Hangxiang Ma's 20260618 v2 resource series. The original two messages are kept
byte-for-byte with SHA256 in sources.json. A separate Codex context rebase
moves those additions onto the exact kernel pin without adding the old CAMSS
node, sensor nodes or enabling any CCI controller. The series was rejected by
the maintainer pending driver dependencies; this staging does not imply upstream
acceptance. Source: https://patchew.org/linux/20260618-pakala-camera-v2-0-9fbb729fd242@oss.qualcomm.com/

The component builder now accepts the pinned, SHA256-checked kernel source
archive and an explicit aarch64-linux-gnu- cross compiler for WSL. Native ARM64
remains the Actions default. It compiles the phone base DTB only in the isolated
output, checks all three CCI controllers remain disabled and all six I2C hosts
exist, and exports that DTB for independent review. The normal phone DTS source,
module install, rootfs and RAM boot image remain unchanged. Neither the generic
resource graph nor a build success establishes sensor power sequencing or capture.

2026-10-29 sensor preparation: Wenmeng Liu's public S5KJN5 v6 binding/driver
messages are preserved with their original trailers and hashes. Source:
https://patchew.org/linux/20260930-sk5jn5-v6-0-517c9b34008a@oss.qualcomm.com/
A separate Codex compatibility patch removes only the unused client-info
parameter from two pad callbacks to match the fixed phone kernel API. The
sensor mode/register tables remain unchanged. WSL builds the actual AArch64
sensor object with W=1/-Werror alongside CAMSS/PHY/CCI and the disabled phone
base DTB. No camera sensor is added to the running board graph or powered.

The saved phone's ultrawide chip-ID check is0x38e5 and its PHY1 explicitly
selects the dodge-ultrawide variant. Public vendor PHY tables differ from the
generic SM8750 sequence, including common0x108c and C-PHY rate tables. The
upstream PHY currently supports D-PHY only. This is a graph/mode investigation,
not permission to silently substitute the generic PHY or claim a captured frame.
