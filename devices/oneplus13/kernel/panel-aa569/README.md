# AA569 offline panel component

This module is compiled for API/ABI checking and instantiated only by the separate
experimental native DTB. Default/touch DTBs keep MDSS disabled. The guarded
static SPR candidate is not physical display acceptance.

The generated header is the exact public OnePlusOSS production WQHD120 sequence,
not AA594, an EVB/EVT variant or a private calibration dump. Regenerate with
tools/oneplus13/generate-panel.py and run check-panel.py against the pinned Linux
source. The generator verifies the original packet digest, type, channel and
transport flags; the real C sender is independently compared against the fixture.

One fixed120Hz mode is advertised:1440x3168,10bpc,native422DSC1.2,720x22 slices,
two slices per packet,four lanes,HS ceiling1363200000bit/s/lane. The full public
vendor PPS remains in the init payload. Physical8bpp stays in DRM/MSM config.
Prepare follows the phone's custom supply/reset sequence and LP command state.
The final display-on packet is deferred to enable. Disable sends display-off;
unprepare sends sleep-in and then the ordered rail teardown. Failed regulator
disable retains its enable ownership and blocks a second power-on until cleanup.
The final rail result controls unprepare's return value, so an earlier logged
DSI error cannot leave DRM's prepared state stale after successful power-off.
Supply phandles are mandatory and exact voltages are requested before enables.

The component uses fixed DBV1433 (`51 05 99`) in HS mode. This matches the active
Oplus callback's inverted-DBV byte order without exposing unimplemented PWM,
low-DBV, HBM, AOD, temperature compensation or brightness-control UI. Patch0008
submits explicit complete vendor groups with one lock/resource lifetime. Wire
bytes, packet types, boundaries and delays pass host C checks; physical timing
equivalence is untested. See docs/oneplus13/DSI-BATCH.md and technical debtD07.

Future board wiring requires L12B fixed1.8V VDDIO, L13B fixed3.0V VCI, TLMM98
active-low reset, PM8550VE_F GPIO7 active-high VDDR, eight GPIOs on that PMIC,
and TLMM86 TE. This is a wiring requirement, not an enabled board description.

References are pinned in docs/oneplus13/NATIVE-DISPLAY.md. Packet provenance:
OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750 at
d50b305f7da9e14715a25120a4ac7b1a4b8b97c3, production
dsi-panel-AA569-P-3-A0019-dsc-cmd.dtsi and its common.dtsi. Host fault-injection
tests do not access the phone or establish electrical correctness.
