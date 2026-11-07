# Phone amplifier candidate data

`aw88461_acf.bin` is the generic 1792-byte AWINIC ACF distributed as
`aw882xx_acf.bin` in the phone's ODM firmware. SHA256:
`941f23e9e315820a01170a97dbb0c88382c46a548993e5d2ca98c7619b1b1847`.
The four Music/Receiver channel profiles pass the restricted PID2308 parser.
They contain no per-unit calibration, credentials or hardware identifiers.
The file's existing proprietary vendor terms apply; it is not GPL relicensed.

The reviewed source copy is from
[the pinned dodge vendor repository](https://github.com/TheMuppets/proprietary_vendor_oneplus_dodge/tree/5cdea50164c2c9435e8612da132396877acd8730).
Its bytes match the saved owner ODM firmware measurement. The owner's supplied
C.93 full-package payload is byte-identical to the payload used for the earlier
phone image extraction: SHA256
`56f9116576b1c6428cc570c8eebeee2786a3eeea5597022b63fe08ecd563496e`.
This does not supply or establish the phone's dynamic DSP speaker protection.

Two bounded stock sysfs `rw` reads on 2026-10-28 returned register00=0x2308,
one from each bound AWINIC device, with stock driver v1.15.0.3. No selector,
register, gain, calibration or partition write was issued. The new mainline
driver independently checks the ID again before accepting the ACF.

The Linux card is explicitly named `OnePlus13-Speaker-Test`. Its initial graph
is 48kHz, stereo S16_LE; CPU and codec support that format. The 24bit Android
graph remains a separate source draft. Default amplifier attenuation is maximum;
the temporary test control is capped at256, at least72.1459dB below the ACF
profile level. Dynamic protection and ordinary-volume playback remain open.
All sound modules remain blocked until explicitly activated in a RAM test.
