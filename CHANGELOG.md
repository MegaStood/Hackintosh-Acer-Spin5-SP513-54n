# Changelog / Debugging Notes

## Issues Encountered and Solutions

### 1. Stale NVRAM — boot-args not reaching kernel

**Symptom:** Config.plist had `-v keepsyms=1 debug=0x100` but boot.efi log showed only `igfxonln=1 forceRenderStandby=0 -noDC9 slide=131` — old values from a previous config.

**Root cause:** `NVRAM > Delete` section was empty. OpenCore's `NVRAM > Add` only writes if the variable doesn't already exist. Old NVRAM values persisted indefinitely.

**Fix:** Added `NVRAM > Delete` entries for `boot-args`, `csr-active-config`, `prev-lang:kbd`, `UIScale`. Set `WriteFlash = true`.

### 2. PlatformSupport check rejecting Caboom_IL

**Symptom:** Stop sign with "This version of Mac OS X is not supported on this platform! Reason: Caboom_IL"

**Root cause:** The Sonoma installer's `PlatformSupport.plist` was rejecting the MacBookPro16,2 board-id. Unclear if the installer was modified or if this is a late Sonoma build quirk.

**Fix:** Added `-no_compat_check` to boot-args, which skips the PlatformSupport validation entirely.

### 3. EC0._REG AE_NOT_EXIST hang

**Symptom:** Boot hangs after `[ PCI configuration end, bridges 6, devices 18 ]` with ACPI errors:
```
ACPI Error: No handler for Region [RAM_] [EmbeddedControl]
ACPI Error: Method parse/execution failed [\_SB.PCI0.LPCB.EC0._REG], AE_NOT_EXIST
```

**Root cause:** `DevirtualiseMmio = true` was stripping the MMIO mapping at `0xFF600000` that the EC's `_REG` method needs on Ice Lake Acer firmware.

**Fix:** Added `0xFF600000` (decimal `4284481536`) to `Booter > MmioWhitelist`.

### 4. Insyde firmware hiding OpenCore boot entry name

**Symptom:** Custom EFI boot entry created via `efibootmgr` from Linux persists across reboots but shows as a **blank entry with no name** in the BIOS Boot tab. The entry boots correctly — it's just unnamed in the BIOS UI. Some Insyde firmware versions may delete the entry entirely.

**Root cause:** Acer InsydeH20 firmware only renders display names for boot entries it recognizes (Microsoft, Ubuntu/GRUB). Custom entries have their device path stored correctly in NVRAM, but the BIOS UI doesn't display the description string for unknown loaders.

**Fix:** Three approaches available — see README for details:
- **Approach A (recommended):** Create entry via `efibootmgr` from Linux (or a Linux Live USB). Entry works but shows blank in BIOS.
- **Approach B:** Replace `EFI/Microsoft/Boot/bootmgfw.efi` with `OpenCore.efi`. Requires pre-existing Windows Boot Manager files on ESP.
- **Approach C:** Set `Misc > Boot > LauncherOption = Full` in config.plist. OpenCore re-registers itself every boot. Firmware-dependent.

### 5. Bluetooth not working — firmware v0 c0

**Symptom:** `system_profiler SPBluetoothDataType` shows `Address: NULL`, `Firmware Version: v0 c0`, `Transport: UART`.

**Root cause:** `USBMap.kext` does not include the internal Intel Bluetooth USB port. The BT controller is invisible to macOS, so `IntelBluetoothFirmware.kext` has nothing to upload firmware to.

**Fix (pending):** Disable USBMap.kext temporarily, identify the BT port, regenerate USBMap including the port with connector type 255 (internal).

### 6. npci=0x2000 regression

**Symptom:** Adding `npci=0x2000` caused boot to hang DURING PCI enumeration instead of AFTER it.

**Root cause:** `npci=0x2000` skips PCI probe phase. On this hardware, the firmware-provided PCI configuration is incomplete and the kernel's probe phase is actually needed to complete device setup.

**Lesson:** `npci=0x2000` is not appropriate for Ice Lake laptops. Removed from boot-args.

### 7. Config edits not reaching the USB

**Symptom:** Multiple boot attempts showed unchanged behavior despite editing config.plist.

**Root cause:** Editing copies of config.plist on different machines (Mac, Linux, Windows) without verifying the file actually made it to the USB's EFI partition. Some edits were lost to copy failures, unsaved buffers, or wrong file paths.

**Fix:** Adopted MD5 verification workflow: always `md5sum` the file on the USB after copying, comparing against the known-good hash.
