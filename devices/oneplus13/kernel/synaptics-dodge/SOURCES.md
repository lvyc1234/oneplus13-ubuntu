# TouchCom source provenance

The Synaptics protocol core, runtime/platform declarations and health stub are
from renhiyama/pagani-linux commit c49d202f6c16e2b0f9e4d762a031ebf082a7f2cc,
drivers/input/touchscreen/synaptics_tcm_hbp. Original Synaptics copyright and
GPL notices are retained. That port derives its core from the public Oplus
synaptics_hbp implementation. Corresponding official reference is OnePlusOSS
android_kernel_modules_and_devicetree_oneplus_sm8750 d50b305f7da9e14715a25120a4ac7b1a4b8b97c3.

The community SPI/input wrapper and vendor variable-bytecode touch parser are
not imported. dodge-s3910-spi.c and s3910-report.h implement a separate wrapper
and fixed descriptor, negotiated in controller RAM. They never flash or update
touch firmware. The protocol core can detect both TouchCom versions; a device
outside application firmware fails without attempting firmware recovery.

Local compatibility edit: syna_tcm2_runtime.h replaces the removed Linux 7.2
strncpy call with bounded strnlen/memcpy/zero-padding preserving its fixed-buffer
semantics. Actions 39888606647 found this issue; no warning suppression or fake
function declaration was added to disguise an unsupported API.

The single canonical source is this directory in the integration repository.
build.sh copies it into the disposable kernel build tree and exports all edits
in phone-kernel.patch. Do not edit that generated copy or maintain another fork
of the driver beside this one.

Status: experimental offline candidate. No real fingers or power cycles have
been tested. Standard LBP support in the owner's actual firmware is unverified.
