# Acer Spin 5 SP513-54N Hackintosh — macOS Sonoma 14.8

> **Author:** [MegaStood](https://github.com/MegaStood)

OpenCore 1.0.7 configuration for the Acer Spin 5 SP513-54N (10th Gen Ice Lake) running macOS Sonoma 14.8.5.

This guide documents a complete installation including **every problem encountered and its solution** — especially the Ice Lake + Acer Insyde firmware-specific issues that aren't covered by generic Hackintosh guides. If you're stuck on an Acer Ice Lake laptop, the [Troubleshooting](#troubleshooting--problems-encountered) section is where you want to start.

## Table of Contents

- [Hardware](#hardware)
- [What Works / What Doesn't](#what-works)
- [BIOS Settings](#bios-settings)
- [Critical Fix: MMIO Whitelist](#critical-fix-mmio-whitelist-0xff600000)
- [NVRAM Configuration](#nvram-configuration)
- [Boot Arguments](#boot-arguments)
- [ACPI SSDTs](#acpi-ssdts)
- [Kexts and Load Order](#kexts)
- [SMBIOS](#smbios)
- [iGPU Configuration](#igpu-configuration)
- [Installation Guide](#installation-guide)
- [**Troubleshooting — Problems Encountered**](#troubleshooting--problems-encountered)
  - [Problem 1: NVRAM Changes Don't Take Effect](#problem-1-nvram-changes-dont-take-effect)
  - [Problem 2: Caboom_IL Platform Rejection](#problem-2-this-version-of-mac-os-x-is-not-supported-on-this-platform--caboom_il)
  - [Problem 3: EC0._REG Hang — MMIO Whitelist](#problem-3-boot-hangs-at-ec0_reg--the-mmio-whitelist-fix)
  - [Problem 4: Config Edits Not Reaching USB](#problem-4-config-edits-not-reaching-the-usb)
  - [Problem 5: npci=0x2000 Regression](#problem-5-npci0x2000-makes-things-worse)
  - [Problem 6: Insyde Won't Show Boot Entry Name](#problem-6-insyde-firmware-wont-show-opencore-boot-entry-name)
  - [Problem 7: Bluetooth Firmware Not Loading](#problem-7-bluetooth-firmware-not-loading--v0-c0)
- [USB Port Map](#usb-port-map-verified-2026-05-01)
- [Thunderbolt 3 Status & Test Plan](#thunderbolt-3-status--test-plan)
- [Credits](#credits)

## Hardware

| Component | Specification |
|---|---|
| **CPU** | Intel Core i7-1065G7 (Ice Lake, 4C/8T, 1.3 GHz base / 3.9 GHz turbo) |
| **iGPU** | Intel Iris Plus Graphics G7 (64 EU) |
| **RAM** | LPDDR4X (onboard) |
| **Storage** | NVMe SSD (AHCI mode required) |
| **Display** | 13.5" 2256×1504 (3:2 aspect ratio), touchscreen |
| **WiFi/BT** | Intel CNVi (WiFi 6 + Bluetooth 5.0) |
| **Trackpad** | I2C HID (Precision Touchpad) |
| **Touchscreen** | I2C HID |
| **Audio** | Realtek (layout-id 13) |
| **Ports** | 2× USB-A (left + right), 2× Thunderbolt 3 USB-C (both left side) — see [USB Port Map](#usb-port-map-verified-2026-05-01) |
| **BIOS** | InsydeH20 |

## What Works

| Feature | Status | Notes |
|---|---|---|
| macOS Sonoma 14.8 | ✅ | Fully installed and running |
| CPU Power Management (XCPM) | ✅ | SpeedStep, turbo boost, C-states all working |
| iGPU Acceleration (Metal 3) | ✅ | HiDPI at 1600×1066 effective resolution |
| WiFi (802.11ac) | ✅ | Via AirportItlwm, 5GHz working |
| Trackpad (I2C, gestures) | ✅ | Full macOS gesture support via VoodooI2C |
| Touchscreen (I2C) | ✅ | Via VoodooI2CHID |
| Keyboard (PS2) | ✅ | Via VoodooPS2 |
| Battery Readout | ✅ | Via ECEnabler + SMCBatteryManager |
| Audio (speakers + mic) | ✅ | Via AppleALC, alcid=13 |
| USB-C Ports | ✅ | Both ports, data + charging + display output |
| Bluetooth | ✅ | Via OpenIntelWireless stack — see [Problem 7](#problem-7-bluetooth-firmware-not-loading--v0-c0) for the USBMap `model` gating fix and the three required `-btlfx*` boot-args |
| NVMe Storage | ✅ | Via NVMeFix |
| Brightness Keys | ✅ | Via BrightnessKeys + SSDT-PNLF |
| Accelerometer (Sensor Hub) | ✅ | Via VoodooI2C |
| Dual-boot (Linux) | ✅ | F12 boot menu for Linux, OpenCore for macOS |

## What Doesn't Work

| Feature | Status | Notes |
|---|---|---|
| Thunderbolt 3 Protocol | 🧪 | **Stack initializes correctly but not yet tested with real TB3 hardware.** Both Ice Lake iTBT controllers (PCI `0x8a17` and `0x8a0d`) are claimed by `AppleThunderboltNHIType4`; full Type4 stack (HAL → NHI → Controller → LocalNode → Port) is up and waiting for devices. The MacBookPro16,2 SMBIOS spoof matches the real Apple Ice Lake TB3 platform exactly, so Apple's drivers configure themselves correctly. See [Thunderbolt 3 Status & Test Plan](#thunderbolt-3-status--test-plan). |
| Sleep/Wake | ❓ | Not fully tested. SSDTs for sleep are included (GPRW, PTSWAKTTS, NameS3-disable) |
| Fingerprint Reader | ❌ | macOS has no driver for ELAN 0x04f3:0x0c4f. Visible as a generic USB device on USBMap label `HS05` (controller port 6); not usable as Touch ID. (`SSDT-ShutFPReaderDown.aml` was archived to `ACPI/_bak/` — see note below.) |
| SD Card Reader | ❓ | Controller not visible on PCIe at idle (no card inserted). May surface on insert as USB or PCIe; verify with `system_profiler SPCardReaderDataType` after inserting a card |

## BIOS Settings

Access hidden settings with `Ctrl+S` at the BIOS screen.

### Required

| Setting | Value |
|---|---|
| SATA Mode | AHCI |
| Secure Boot | Disabled |
| Fast Boot | Disabled |
| F12 Boot Menu | Enabled |

### Recommended

| Setting | Value |
|---|---|
| Network Boot | Disabled |
| Touchpad | I2C |

**Note:** CFG Lock cannot be disabled on Acer Spin 5 firmware without hardware flashing. The `AppleXcpmCfgLock` quirk handles this in software.

## Critical Fix: MMIO Whitelist (0xFF600000)

This is the single most important fix specific to this laptop. Without it, macOS hangs during boot with:

```
ACPI Error: Method parse/execution failed [\_SB.PCI0.LPCB.EC0._REG], AE_NOT_EXIST
```

**Root cause:** OpenCore's `DevirtualiseMmio` aggressively devirtualizes MMIO regions, including address `0xFF600000` which the Embedded Controller needs for its `_REG` method on Ice Lake Acer firmware.

**Fix:** Add this entry to `Booter > MmioWhitelist`:

```xml
<dict>
    <key>Address</key>
    <integer>4284481536</integer>
    <key>Comment</key>
    <string>MMIO 0xFF600000 IceLake EC fix</string>
    <key>Enabled</key>
    <true/>
</dict>
```

Credit: [Zhihu article on Acer Spin 5 SP513-54N Hackintosh](https://zhuanlan.zhihu.com/p/438210561)

## NVRAM Configuration

### Essential: NVRAM Delete Block

Acer InsydeH20 firmware has aggressive NVRAM persistence. Without a `Delete` block, changes to `boot-args` and `csr-active-config` in `config.plist` will never reach the kernel — stale NVRAM values from previous boots take precedence.

```xml
<key>Delete</key>
<dict>
    <key>4D1EDE05-38C7-4A6A-9CC6-4BCCA8B38C14</key>
    <array>
        <string>UIScale</string>
    </array>
    <key>7C436110-AB2A-4BBB-A880-FE41995C9F82</key>
    <array>
        <string>boot-args</string>
        <string>csr-active-config</string>
        <string>prev-lang:kbd</string>
    </array>
</dict>
```

Also set `WriteFlash = true` to ensure NVRAM changes persist to firmware flash.

### Boot Arguments

```
keepsyms=1 debug=0x100 -btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck agdpmod=vit9696 darkwake=0 igfxonln=1 -noDC9 forceRenderStandby=0 alcid=13 -v -no_compat_check
```

| Argument | Purpose |
|---|---|
| `keepsyms=1` | Keep kernel symbols for readable panic logs |
| `debug=0x100` | Prevent auto-reboot on panic |
| `-btlfxallowanyaddr` | BlueToolFixup: allow BT controller at any USB address |
| `-btlfxboardid` | BlueToolFixup: bypass board-id check that routes T2-era SMBIOS to UART transport |
| `-btlfxnvramcheck` | BlueToolFixup: skip the NVRAM `bluetoothInternalControllerInfo` precondition |
| `agdpmod=vit9696` | Disable Apple GPU Display Policy board-id check |
| `darkwake=0` | Disable dark wake (prevents partial-wake issues) |
| `igfxonln=1` | Force all display connectors online (WhateverGreen) |
| `-noDC9` | Disable DC9 power state (prevents Ice Lake sleep issues) |
| `forceRenderStandby=0` | Disable render standby (prevents iGPU power issues) |
| `alcid=13` | AppleALC layout ID for audio codec |
| `-v` | Verbose boot (remove once stable) |
| `-no_compat_check` | Skip PlatformSupport.plist board-id validation |

**Note on the three `-btlfx*` flags:** All three are required together on T2-era SMBIOS (e.g., MacBookPro16,2). With only `-btlfxallowanyaddr`, `bluetoothd` still selects UART transport because the board-id and NVRAM checks veto USB. Adding `-btlfxboardid` and `-btlfxnvramcheck` flips bluetoothd to the USB transport path, which is what the OpenIntelWireless stack actually provides for AX201.

## ACPI SSDTs

| SSDT | Purpose |
|---|---|
| SSDT-XOSI | OS interface spoof for Windows-specific ACPI paths |
| SSDT-HPET | Fix HPET IRQ conflicts (paired with _CRS→XCRS rename) |
| SSDT-EC-USBX-LAPTOP | Embedded Controller + USB power properties |
| SSDT-PLUG-DRTNIA | CPU power management plugin injection (X86PlatformPlugin) |
| SSDT-AWAC | Fix incompatible AWAC real-time clock |
| SSDT-PNLF-CFL | Backlight control for Coffee Lake+ framebuffers |
| SSDT-ALS0 | Fake Ambient Light Sensor (enables auto-brightness slider) |
| SSDT-SBUS | SMBus device injection |
| SSDT-DMAC | Missing DMA Controller definition |
| SSDT-MCHC | Missing Memory Controller Hub definition |
| SSDT-PPMC | Missing PMC definition |
| SSDT-PMCR | Missing PMCR definition |
| SSDT-SLPB | Missing Sleep Button definition (**Note:** may cause "1 table load failure" — can be disabled if SLPB already exists in your DSDT) |
| SSDT-PWRB | Missing Power Button definition |
| SSDT-EXT3-WakeScreen | Wake screen on _WAK |
| SSDT-GPRW | Sleep fix (GPRW→XPRW rename pair) |
| SSDT-NameS3-disable | AOAC S3 sleep state management |
| SSDT-TPXX-ACER-SP5 | I2C Touchscreen (model-specific) |
| SSDT-TPD1-ACER-SP5 | I2C Trackpad (model-specific) |
| SSDT-GPI0-GPHD-TPDM | GPIO setup for I2C HID devices |
| SSDT-PTSWAKTTS-iGPU | Sleep/wake hooks for iGPU |
| SSDT-MEM2 | Reserve memory regions for macOS |

### Note on `SSDT-ShutFPReaderDown.aml` (archived in `ACPI/_bak/`)

This file ships with many Acer/Lenovo Ice Lake EFIs and claims to disable the fingerprint reader. On Sonoma it does nothing useful — but the reason is *not* what its name implies.

**Two namespaces, same `HSnn` names — easy to confuse:**

- **ACPI namespace** (DSDT): `\_SB.PCI0.XHC.RHUB.HS01..HSnn` — these names map to the controller's *physical port numbers* (HS06 = port 6).
- **USBMap.kext labels**: `HS01..HS07` — these are sequential labels that USBToolBox happens to assign; the actual physical port comes from the personality's `port` data byte (USBMap label `HS05` has `port = 0x06`, USBMap label `HS06` has `port = 0x07`, etc.).

So for this hardware:

| Physical port | ACPI name | USBMap label | Device |
|---|---|---|---|
| 6 | `HS06` | `HS05` | ELAN Fingerprint reader |
| 7 | `HS07` | `HS06` | HD Webcam |
| 10 | `HS10` | `HS07` | AX201 Bluetooth |

The SSDT issues `_STA = 0` for `\_SB.PCI0.XHC.RHUB.HS06` — i.e. **ACPI** HS06, which is physical port 6 = the fingerprint reader. So the *target was correct*; whoever wrote the SSDT did mean to shut down the FP reader. (My earlier commit message claimed this targeted the webcam — that was wrong; I had conflated ACPI HS06 with USBMap label HS06.)

**Why the SSDT is still a no-op:** macOS's xHCI driver creates port nubs from the controller's hardware port registers, not from the ACPI namespace. ACPI `_STA = 0` only hides the device from the ACPI tree; the underlying USB port still enumerates and devices still attach. The dortania docs reflect this — modern guidance is to disable ports in the USBMap (`UsbConnector` / map exclusion), not via ACPI SSDTs.

The fingerprint reader appears in `system_profiler SPUSBDataType` regardless of this SSDT. It's "not working" only in the sense that macOS has no driver for ELAN `0x04f3:0x0c4f` — it's never offered as a Touch ID source.

The SSDT is moved to `EFI/OC/ACPI/_bak/` and disabled in `config.plist > ACPI > Add`. If you want the FP reader actually shut down for power/sleep reasons, the right fix is removing the FP reader's entry (USBMap label `HS05`, port `0x06`) from `USBMap.kext` or marking it disabled there.

## Kexts

### Load Order (important)

Kext load order matters. The config.plist `Kernel > Add` entries must follow dependency chains:

```
 1. Lilu.kext                        (must be first — all plugins depend on it)
 2. VirtualSMC.kext                  (depends on Lilu)
 3. NVMeFix.kext                     (Lilu plugin)
 4. AppleALC.kext                    (Lilu plugin)
 5. ECEnabler.kext                   (Lilu plugin)
 6. IntelBluetoothFirmware.kext      (uploads firmware — must be before BT patches)
 7. IntelBTPatcher.kext              (patches Intel BT quirks — after firmware upload)
 8. BlueToolFixup.kext               (Sonoma BT compatibility — after Intel BT kexts)
 9. BrightnessKeys.kext              (Lilu plugin)
10. CpuTscSync.kext
11. RestrictEvents.kext              (Lilu plugin)
12. SMCBatteryManager.kext           (VirtualSMC plugin)
13. SMCProcessor.kext                (VirtualSMC plugin)
14. SMCSuperIO.kext                  (VirtualSMC plugin)
15. USBMap.kext                      (model-specific USB port map)
16. WhateverGreen.kext               (Lilu plugin)
17. VoodooI2CServices.kext           (I2C plugin — before main VoodooI2C)
18. VoodooGPIO.kext                  (I2C plugin — before main VoodooI2C)
19. VoodooInput.kext                 (I2C plugin — before main VoodooI2C)
20. VoodooI2C.kext                   (main I2C driver)
21. VoodooI2CHID.kext                (I2C HID satellite — after main VoodooI2C)
22. VoodooPS2Controller.kext         (PS2 keyboard)
23. VoodooPS2Keyboard.kext           (PS2 keyboard plugin)
24. AirportItlwm.kext                (Intel WiFi — MinKernel=22.0.0)
```

### Notes on WiFi Kext Variants

The Intel WiFi project ships separate kexts per macOS version. Use the correct one:

| macOS Version | Kext | MinKernel | MaxKernel |
|---|---|---|---|
| Ventura (13.x) | AirportItlwmV.kext | 22.0.0 | 22.8.0 |
| Sonoma (14.x) | AirportItlwm.kext (Sonoma build) | 22.0.0 | — |

Ensure the `.kext` bundle on disk matches the macOS version you're installing. The filename alone doesn't indicate the build — verify with `otool -l` or check the release notes.

## SMBIOS

Uses **MacBookPro16,2** (13-inch 2020, Ice Lake, Iris Plus G7).

**You must generate your own serial number, MLB, UUID, and ROM.** The included `config.plist` has these fields blanked out. Use [GenSMBIOS](https://github.com/corpnewt/GenSMBIOS) to generate valid values:

```bash
git clone https://github.com/corpnewt/GenSMBIOS
cd GenSMBIOS
python3 GenSMBIOS.py
# Pick option 1 (MacBookPro16,2)
# Copy SystemSerialNumber, MLB, SystemUUID into config.plist
# Generate ROM from your network MAC address or use a random 6-byte hex value
```

Verify the serial at [Apple Check Coverage](https://checkcoverage.apple.com/) — it should return **"not valid"** (meaning it's not a real Mac's serial).

## iGPU Configuration

The Iris Plus G7 uses `ig-platform-id = 0x8A520000` with these WhateverGreen patches in DeviceProperties:

| Property | Value | Purpose |
|---|---|---|
| `AAPL,ig-platform-id` | `0000528A` | Ice Lake G7 laptop framebuffer |
| `device-id` | `528A0000` | Match G7 device ID |
| `AAPL,GfxYTile` | `01000000` | Enable Y-tiling for performance |
| `framebuffer-fbmem` | `00009000` | Framebuffer memory |
| `framebuffer-stolenmem` | `00003001` | Stolen memory size |
| `framebuffer-unifiedmem` | `00000080` | Unified memory |
| `igfxfw` | `02000000` | Load Apple GuC firmware |
| `enable-backlight-registers-fix` | `01000000` | Fix backlight control |
| `enable-cdclk-frequency-fix` | `01000000` | Fix CD clock |
| `enable-cfl-backlight-fix` | `01000000` | CFL backlight compatibility |
| `enable-dbuf-early-optimizer` | `01000000` | Display buffer optimization |
| `enable-dvmt-calc-fix` | `01000000` | Fix DVMT pre-allocation calculation |
| `enable-hdmi20` | `01000000` | Enable HDMI 2.0 |

## Installation Guide

### Prerequisites

- USB drive (16 GB+)
- macOS Sonoma 14.x installer (download via `softwareupdate --fetch-full-installer` on a Mac, or use [macadmin-scripts](https://github.com/munki/macadmin-scripts) on Linux)
- This EFI folder
- Generated SMBIOS values (see above)

### Step 1: Prepare the Installer USB

On a Mac:
```bash
sudo "/Applications/Install macOS Sonoma.app/Contents/Resources/createinstallmedia" \
    --volume /Volumes/YOUR_USB_NAME --nointeraction
```

### Step 2: Copy EFI to USB

Mount the USB's EFI partition and copy the `EFI` folder:

```bash
sudo diskutil mount disk2s1    # adjust disk number
cp -r EFI /Volumes/EFI/
```

### Step 3: Generate SMBIOS

Use GenSMBIOS to generate `SystemSerialNumber`, `MLB`, `SystemUUID`, and `ROM`. Edit `config.plist > PlatformInfo > Generic` with your values.

### Step 4: BIOS Setup

Enter BIOS (F2 at boot), press `Ctrl+S` to reveal hidden options:
- SATA Mode → AHCI
- Secure Boot → Disabled
- Fast Boot → Disabled

### Step 5: Boot and Install

1. Boot from USB (F12 boot menu)
2. Select OpenCore → "Install macOS Sonoma"
3. Open Disk Utility → View → Show All Devices
4. Erase target partition as APFS / GUID Partition Map
5. Install macOS
6. The install takes 30–60 minutes and may include a long SSV sealing phase — be patient

### Step 6: Post-Install — Copy OpenCore to Internal SSD

After installation, copy the EFI folder from the USB to the internal SSD's EFI System Partition so you can boot without the USB.

### Step 7: Boot Entry (Insyde Firmware Workaround)

Acer InsydeH20 firmware doesn't display custom EFI boot entry names. See [Problem 6](#problem-6-insyde-firmware-wont-show-opencore-boot-entry-name) for three approaches. The recommended method is creating the entry from Linux:

```bash
sudo efibootmgr --create --disk /dev/nvme0n1 --part 2 \
    --label "OpenCore" --loader '\EFI\OC\OpenCore.efi'
```

The entry appears as a blank line in BIOS but boots OpenCore correctly. Use `efibootmgr -v` from Linux to see the real names and manage boot order.

## Fixing Bluetooth

See [Problem 7: Bluetooth Firmware Not Loading](#problem-7-bluetooth-firmware-not-loading--v0-c0) in the Troubleshooting section.

## Troubleshooting — Problems Encountered

This section documents every issue encountered during the installation, in the order they appeared. Each problem blocked the install completely until resolved. If you're stuck on a similar Acer Ice Lake system, check these first.

---

### Problem 1: NVRAM Changes Don't Take Effect

**Symptom:** You edit `boot-args` or `csr-active-config` in `config.plist`, reboot, but the kernel sees the old values. Verbose mode (`-v`) doesn't activate. Changes to SIP settings are ignored. Boot.efi log shows stale values like `slide=131` that aren't in your config.

**How to verify:** Check the boot.efi log for the `MBA:OUT` line — this shows what boot-args the kernel actually receives. If it doesn't match your `config.plist`, NVRAM is stale.

**Root cause:** OpenCore's `NVRAM > Add` only writes a variable **if it doesn't already exist**. Once a value is written to NVRAM (by a previous boot, a panic recovery, or an older config), it persists forever. Your new config values are silently ignored.

The Acer Insyde firmware is especially aggressive about NVRAM persistence. Even `CleanNvram` from the OpenCore picker may not fully clear all variables because:
- Apple's boot.efi writes `slide=N` and `csr-active-config` to NVRAM after panics
- The panic recovery mechanism re-injects old boot-args via LoadOptions on the next boot, bypassing NVRAM entirely

**Fix:** Add a `NVRAM > Delete` block that forces OpenCore to wipe and rewrite the variables every boot:

```xml
<key>Delete</key>
<dict>
    <key>4D1EDE05-38C7-4A6A-9CC6-4BCCA8B38C14</key>
    <array>
        <string>UIScale</string>
    </array>
    <key>7C436110-AB2A-4BBB-A880-FE41995C9F82</key>
    <array>
        <string>boot-args</string>
        <string>csr-active-config</string>
        <string>prev-lang:kbd</string>
    </array>
</dict>
```

Also set `NVRAM > WriteFlash = true` to ensure changes persist to firmware flash rather than just RAM.

**Lesson:** For Hackintosh maintenance, **anything you set in `NVRAM > Add` that you ever expect to change should also be listed in `NVRAM > Delete`.** Treat `Add` as "default if absent" and `Delete + Add` as "force this value every boot."

---

### Problem 2: "This version of Mac OS X is not supported on this platform" — Caboom_IL

**Symptom:** Stop sign (🛑) with text: *"This version of Mac OS X is not supported on this platform! Reason: Caboom_IL"*. Appears before the kernel even loads.

**How to verify:** Check the boot.efi log for `EB|STOP 0x11` preceded by the PlatformSupport check loading `PlatformSupport.plist`.

**Root cause:** Apple's `boot.efi` reads `PlatformSupport.plist` from the installer and checks whether the current machine's platform code (`Caboom_IL` = MacBookPro16,2 Ice Lake) is on the supported list. On some Sonoma installer builds, this check fails even though MacBookPro16,2 is officially Sonoma-supported.

This can happen because the installer image was modified, downloaded from an unofficial source, or is a late point release with a different support matrix.

**Fix:** Add `-no_compat_check` to `boot-args`. This tells boot.efi to skip the PlatformSupport validation entirely:

```
boot-args: ... -no_compat_check
```

**Note:** This fix only works if NVRAM is actually applying your boot-args (see Problem 1). If `-no_compat_check` is in your config but NVRAM is stale, the flag never reaches boot.efi and the rejection continues.

---

### Problem 3: Boot Hangs at EC0._REG — The MMIO Whitelist Fix

**Symptom:** Boot hangs after `[ PCI configuration end, bridges 6, devices 18 ]` with these ACPI errors:

```
ACPI Error: No handler for Region [RAM_] (ffffff909c53ce88) [EmbeddedControl]
ACPI Error: Region EmbeddedControl (ID=3) has no handler
ACPI Error: Method parse/execution failed [\_SB.PCI0.LPCB.EC0._REG] (Node fffff9f018ee370), AE_NOT_EXIST
```

The error repeats twice, then the screen freezes. No panic, no reboot — just a permanent hang.

**How to verify:** Boot with `-v` (verbose). If you see the EC0._REG errors followed by a hang, this is your problem. Note: if `-v` doesn't work, fix Problem 1 first.

**Root cause:** OpenCore's `Booter > Quirks > DevirtualiseMmio = true` aggressively devirtualizes MMIO regions to free up boot memory for macOS. On Ice Lake Acer firmware, this strips the MMIO mapping at address `0xFF600000` — which the Embedded Controller's `_REG` method needs to communicate with hardware.

Without this mapping, EC initialization fails. The kernel can't determine power state, lid state, battery status, or thermal zones. It spins on EC retry loops and never progresses.

The EC SSDT (`SSDT-EC-USBX-LAPTOP.aml`) is correct and loaded — the issue isn't missing ACPI, it's that the memory the ACPI code needs to access has been torn out from under it.

**Fix:** Add the MMIO address to `Booter > MmioWhitelist`, telling OpenCore "don't devirtualize this address":

```xml
<key>MmioWhitelist</key>
<array>
    <dict>
        <key>Address</key>
        <integer>4284481536</integer>
        <key>Comment</key>
        <string>MMIO 0xFF600000 IceLake EC fix</string>
        <key>Enabled</key>
        <true/>
    </dict>
</array>
```

`4284481536` = `0xFF600000` in decimal.

**Credit:** This fix was discovered via a [Zhihu article](https://zhuanlan.zhihu.com/p/438210561) documenting the exact same problem on the same laptop model (Acer Spin 5 SP513-54N). The author tried many ACPI fixes before finding that the root cause was MMIO devirtualization, not missing SSDTs.

**Lesson:** If you see `EC0._REG AE_NOT_EXIST` on an Ice Lake system with `DevirtualiseMmio = true`, the fix is almost certainly an MMIO whitelist entry — not an ACPI patch. The specific address may vary between firmware revisions. Other addresses reported for Ice Lake: `0xFE000000`, `0xFED00000`, `0xFED40000`.

---

### Problem 4: Config Edits Not Reaching the USB

**Symptom:** You edit `config.plist`, reboot, but behavior doesn't change. Same errors, same boot-args in the log. Feels like OpenCore is ignoring your edits.

**Root cause:** This is a workflow problem, not a technical one. When editing `config.plist` across multiple machines (Mac for validation, Linux for editing, Windows for OCAT), it's easy to:
- Edit a copy on your desktop but forget to copy it back to the USB
- Save on a USB partition mounted read-only (the save appears to succeed but doesn't write)
- Forget to run `sync` before unplugging a USB on Linux (writes are buffered and lost)
- Edit the wrong `config.plist` (internal ESP vs. USB vs. a backup copy)

**Fix:** Adopt a verification workflow:

```bash
# After copying config.plist to the USB, verify with MD5:

# Linux
md5sum /mnt/usb-efi/EFI/OC/config.plist

# macOS
md5 /Volumes/EFI/EFI/OC/config.plist

# Windows PowerShell
Get-FileHash X:\EFI\OC\config.plist -Algorithm MD5
```

Compare the hash against the file you intended to copy. If they differ, the copy failed.

**Lesson:** When debugging a Hackintosh, **always verify the file on the USB matches what you think you put there.** Most "my edit didn't work" situations are actually "my edit didn't make it to the USB."

---

### Problem 5: npci=0x2000 Makes Things Worse

**Symptom:** Adding `npci=0x2000` to boot-args (a commonly recommended fix for PCI hangs) causes the boot to hang **during** PCI enumeration instead of after it. PCI flags change from `0xc3080` to `0xc1080`, confirming the flag is active, but the kernel never reaches `[ PCI configuration end ]`.

**Root cause:** `npci=0x2000` tells the kernel to skip the PCI device probe phase and trust whatever the firmware provides. On this hardware, the firmware's PCI configuration is incomplete — the kernel's probe phase is actually needed to complete device setup. Skipping it leaves devices in an uninitialized state.

**Fix:** Don't use `npci=0x2000` on Ice Lake laptops. Remove it from boot-args. The PCI hang (if it occurs) is caused by the EC issue (Problem 3), not by PCI enumeration itself.

**Lesson:** `npci=0x2000` is a legacy fix from the Snow Leopard era. It still works on some desktop systems, but on modern laptops with complex PCI topologies (Thunderbolt, NVMe, I2C controllers), it often causes regressions.

---

### Problem 6: Insyde Firmware Won't Show OpenCore Boot Entry Name

**Symptom:** You create an OpenCore boot entry using `efibootmgr` from Linux:

```bash
sudo efibootmgr --create --disk /dev/nvme0n1 --part 2 \
    --label "OpenCore" --loader '\EFI\OC\OpenCore.efi'
```

The entry appears in `efibootmgr -v` output with the correct label. But in the BIOS Boot tab, it shows as a **blank entry with no name**. The entry **does boot correctly** — it's just unnamed in the BIOS UI.

**Root cause:** Acer InsydeH20 firmware only renders display names for boot entries it recognizes (Microsoft, Ubuntu/GRUB). Custom entries have their device path stored correctly in NVRAM, but the BIOS UI doesn't display the description string for unknown loaders.

Note: some Insyde firmware versions **delete** custom entries entirely on reboot. The SP513-54N firmware (as tested) **keeps the entry but strips the display name**. Your experience may vary with different BIOS versions.

**Three approaches, pick one:**

#### Approach A: Linux efibootmgr (recommended — used in this build)

Create the entry from Linux. It persists across reboots but shows as a blank entry in BIOS.

First, find your internal SSD's EFI System Partition:

```bash
# List all partitions and their types
sudo parted -l
# Or
lsblk -f
```

Look for the FAT32 partition labeled "EFI" or with type "EFI System". Note the **disk** (e.g. `/dev/nvme0n1`, `/dev/sda`) and **partition number** (e.g. `1`, `2`). These vary by system — don't blindly copy the example below.

Then create the entry:

```bash
# Replace --disk and --part with YOUR values
# Example: EFI is partition 2 on /dev/nvme0n1
sudo efibootmgr --create --disk /dev/nvme0n1 --part 2 \
    --label "OpenCore" --loader '\EFI\OC\OpenCore.efi'

# Verify
sudo efibootmgr -v

# Set boot order (adjust hex IDs from efibootmgr output)
sudo efibootmgr --bootorder XXXX,YYYY,ZZZZ
```

Common disk/partition combinations:

| Disk layout | Disk | Partition |
|---|---|---|
| NVMe SSD, ESP is partition 1 | `/dev/nvme0n1` | `1` |
| NVMe SSD, ESP is partition 2 (this build) | `/dev/nvme0n1` | `2` |
| SATA SSD, ESP is partition 1 | `/dev/sda` | `1` |

The BIOS Boot tab shows:
```
1. (blank)              → boots OpenCore  ✅
2. ubuntu               → boots Linux     ✅
3. Windows Boot Manager → boots Windows (or nothing if no Windows installed)
```

**Don't have Linux installed?** You can run `efibootmgr` from a **Linux Live USB** (Ubuntu installer, Fedora Live, etc.) — no installed Linux needed. Boot the Live USB, select "Try Ubuntu", open Terminal, and run the same commands above. The boot entry is written to firmware NVRAM, so it persists after you remove the Live USB.

```bash
# On Ubuntu Live, install efibootmgr if not present
sudo apt install efibootmgr
# Then run the commands above
```

**Pros:** Simple, one command, non-destructive, entry survives reboots. Works from any Linux environment including Live USBs.
**Cons:** Blank name in BIOS — you have to remember which blank entry is OpenCore. `efibootmgr -v` from Linux always shows the real name.

#### Approach B: Replace Windows Boot Manager with OpenCore

**Prerequisites:** This approach only works if you have a **pre-existing Windows installation** (or at least Windows Boot Manager files) on your EFI partition. If you've never had Windows on this machine, `EFI/Microsoft/Boot/bootmgfw.efi` won't exist and this approach doesn't apply — use Approach A or C instead.

**How it works:** The firmware NVRAM has a "Windows Boot Manager" entry that points to `\EFI\Microsoft\Boot\bootmgfw.efi`. The firmware trusts this path and never deletes it. By replacing the *contents* of `bootmgfw.efi` with OpenCore, the firmware loads OpenCore while thinking it's loading Windows. The filename must stay as `bootmgfw.efi` — the firmware looks for that exact name at that exact path.

**Why copy `OpenCore.efi` and not `BOOTx64.efi`?** OpenCore's `BOOTx64.efi` is a small bootstrap (~80 KB) that *searches* for `\EFI\OC\OpenCore.efi` and chainloads it. `OpenCore.efi` (~800 KB) is the full bootloader that runs directly. When placed at `\EFI\Microsoft\Boot\bootmgfw.efi`, using `OpenCore.efi` directly is more reliable — no search step, no path resolution, just runs. `BOOTx64.efi` would likely work too (it scans for `\EFI\OC\OpenCore.efi` across partitions), but adds an unnecessary layer of indirection.

```bash
# Back up original (so you can restore Windows Boot Manager later if needed)
sudo cp /Volumes/EFI/EFI/Microsoft/Boot/bootmgfw.efi ~/bootmgfw.original.bak

# Replace contents with OpenCore — keep the filename unchanged
sudo cp /Volumes/EFI/EFI/OC/OpenCore.efi /Volumes/EFI/EFI/Microsoft/Boot/bootmgfw.efi
```

**Important:** Do NOT rename the file. It must remain `bootmgfw.efi` — the firmware's NVRAM entry references this exact filename. If you rename it, the firmware won't find it and the entry stops working.

**Can Windows still boot after this?** Yes — Windows itself is untouched on its partition. OpenCore's picker can chainload Windows directly from the Windows partition. You lose the firmware-level "Windows Boot Manager" shortcut, but gain OpenCore as the universal picker for all OSes.

**To restore Windows Boot Manager later:**
```bash
sudo cp ~/bootmgfw.original.bak /Volumes/EFI/EFI/Microsoft/Boot/bootmgfw.efi
```

**Pros:** Named entry in BIOS ("Windows Boot Manager"), firmware will never delete it, no Linux required.
**Cons:** Requires pre-existing Windows Boot Manager files on ESP. Misleading name in BIOS. Windows Updates may overwrite your fake `bootmgfw.efi`, requiring you to re-copy OpenCore.

#### Approach C: OpenCore LauncherOption=Full

Let OpenCore manage its own boot entry automatically — no Linux or Windows workaround needed. OpenCore re-registers itself in the firmware's NVRAM on every boot.

In `config.plist`, change:

```
Misc > Boot > LauncherOption:  Disabled → Full
```

On the next boot, OpenCore writes a boot entry pointing to `\EFI\OC\OpenCore.efi` and sets itself as the highest boot priority. If the firmware deletes the entry, OpenCore recreates it on the next boot (as long as you can reach OpenCore via USB or fallback path at least once).

**Additional settings that help:**

| Setting | Value | Purpose |
|---|---|---|
| `Misc > Boot > LauncherOption` | `Full` | Register boot entry every boot |
| `Misc > Boot > LauncherPath` | `Default` | Uses `\EFI\OC\OpenCore.efi` as the registered path |
| `NVRAM > WriteFlash` | `true` | Ensures the entry persists to firmware flash |

If `Full` doesn't work (firmware deletes the entry before OpenCore runs), try `Short` instead — it uses a shorter UEFI device path that some Insyde firmwares handle better:

```
Misc > Boot > LauncherOption:  Full → Short
```

**Pros:** Fully automatic, no external tools needed, no Linux or Windows knowledge required.
**Cons:** On some Insyde firmware versions, the firmware deletes the entry before OpenCore gets a chance to recreate it — creating a chicken-and-egg problem. If it fails, you need a USB to recover. The entry may also appear with a blank name in BIOS (same as Approach A).

**Recovery if it doesn't work:** Boot from your OpenCore USB, set `LauncherOption` back to `Disabled`, and use Approach A or B instead.

**Recommendation:** Start with Approach A (Linux `efibootmgr`) if you have Linux available. Try Approach C if you don't have Linux access and don't want to touch the Windows bootloader. Fall back to Approach B as a last resort if the firmware actively deletes entries on your BIOS version.

---

### Problem 7: Bluetooth Firmware Not Loading — v0 c0

**Symptom (macOS 14.8.5):** `system_profiler SPBluetoothDataType` shows:

```
Address: NULL
State: Off
Firmware Version: v0 c0
Transport: UART
```

All three Bluetooth kexts are enabled (`IntelBluetoothFirmware`, `IntelBTPatcher`, `BlueToolFixup`) but firmware never uploads. `system_profiler SPUSBDataType` shows no Intel Bluetooth device (vendor `0x8087`) — the BT chip is invisible to macOS.

This regression appeared specifically on the macOS 14.7.5 → 14.8.5 update; the same symptom hit a separate i7-8250U Hackintosh on the same OS bump. Both with T2-era SMBIOS.

**How to verify:** After a boot, run:

```bash
ioreg -p IOService -lw0 -n XHC | grep -E '\+-o (HS|SS)[0-9]+'
```

You'll see only 4 HS + 2 SS ports under XHC even though the controller has 14. The internal-facing ports (where AX201 BT, webcam, IR, fingerprint live) are absent. **Even with `XhciPortLimit = true`**, those ports never appear without a matching USBMap merge.

**Root cause: USBMap `model` gating.**

The codeless `USBMap.kext` (USBToolBox v1.1) embeds a `model` key inside each `IOKitPersonality`. `AppleUSBHostMergeProperties` honors that `model` key as a gating predicate (the same mechanism Apple's stock USBPorts.kext uses to ship per-Mac-model port maps in one bundle). If your OpenCore SMBIOS doesn't match the `model` value, the personality silently never matches — no merge, port-count stays at the native xHCI default (6 on this machine), and internal USB devices never enumerate.

For maps generated by USBToolBox, the `model` is whatever SMBIOS was selected at generation time (often `MacBookAir9,1`). If your live SMBIOS differs (e.g., `MacBookPro16,2` for this build), the merge silently dies.

`Transport: UART` in the bluetoothd output is a separate symptom — T2-era SMBIOS routes BT to UART transport via Apple's board-id table, regardless of the USBMap state. Even after USB enumeration is fixed, you still need the three `-btlfx*` boot-args (see [Boot Arguments](#boot-arguments)) to flip `bluetoothd` from UART to USB.

**Fix: edit USBMap.kext to match your SMBIOS.**

```bash
# In your repo
grep -n "model" EFI/OC/Kexts/USBMap.kext/Contents/Info.plist
# Should print two lines, e.g.:
#   <string>MacBookAir9,1</string>
#   <string>MacBookAir9,1</string>

# Replace with your live SMBIOS (this build uses MacBookPro16,2)
# Both XHC and TXHC personalities have their own model key — change BOTH
```

After fixing `model`, copy the updated kext to your ESP and reboot. To verify the merge applied:

```bash
ioreg -p IOService -lw0 -n XHC | grep -E '\+-o (HS|SS)[0-9]+'
# Should now show 14 ports, including HS05/HS06/HS07 + SS01/SS02 under XHC

system_profiler SPUSBDataType | grep -B 2 -A 10 "0x8087"
# Should now show Intel BT (0x8087:0x0026) at LocationID 0x14700000 (HS07)
```

Then check that bluetoothd actually came up:

```bash
system_profiler SPBluetoothDataType | head -20
# State: On, Address: a real BD address, Transport: USB
```

**Why this matters:** Many published Acer/HP/Lenovo Ice Lake configurations were built with USBToolBox under one SMBIOS and shipped with a different SMBIOS in `config.plist`. The map appears correct (right port-count, right UsbConnector tags), and on macOS versions before 14.8 the `model` mismatch was less strictly enforced. macOS 14.8 tightened the merge gating. This is the most likely root cause for "BT broke after the 14.7 → 14.8 update" reports on this hardware family.

**Cosmetic non-issues — do not investigate:**

- `system_profiler` reports `Chipset: THIRD_PARTY_DONGLE` and `Vendor ID: 0x004C (Apple)` for the BT controller after the fix. Normal for OpenIntelWireless transport masquerade.
- NVRAM `bluetoothInternalControllerInfo` populated but all zeros. Harmless on T2-spoofed SMBIOS.
- First `bluetoothd` init pass on boot can fail with "Still cannot find USB Bluetooth Controller after 15 seconds"; the auto-restart succeeds. Race between kext stack ready and `bluetoothd` init timeout.

**Also important — kext load order:** The three Bluetooth kexts must load in this sequence:

```
IntelBluetoothFirmware.kext    → loads FIRST (uploads firmware)
IntelBTPatcher.kext            → loads SECOND (patches Intel quirks)
BlueToolFixup.kext             → loads THIRD (Sonoma compatibility shim)
```

If `BlueToolFixup` loads before `IntelBluetoothFirmware`, it patches Apple's Bluetooth framework before the controller has received its firmware — this also causes `v0 c0`.

---

## USB Port Map (verified 2026-05-01)

Derived from Windows USBTreeView with USB 2 + USB 3 thumb drives in every external port. Use this table when adding new internal devices to `USBMap.kext` or debugging "device not enumerating" issues.

**Physical layout:**
- Left side (front → rear): USB-A, USB-C, USB-C
- Right side: USB-A

**Key wiring quirk:** The two left-side USB-C ports each split into two lanes routed through different controllers — USB 2 lanes go to XHC, USB 3 SuperSpeed lanes go to TXHC (Thunderbolt). This is normal Thunderbolt 3 USB-C wiring; TXHC has no USB 2 root-hub ports at the hardware level.

### XHC controller — pcidebug `0:20:0` (PCI `0:14:0` hex), 18-port root hub

| Phys port | macOS label | `port` byte | UsbConnector | Device / role |
|---|---|---|---|---|
| 1 | HS01 | `0x01` | 9 (Type-C) | Left **front** USB-C, USB 2 lane (companion: TXHC port 3) |
| 2 | HS02 | `0x02` | 9 | Left **rear** USB-C, USB 2 lane (companion: TXHC port 2) |
| 3 | HS03 | `0x03` | 3 (USB 3 Type-A) | **Left** USB-A, USB 2 lane (companion: XHC port 13) |
| 4 | HS04 | `0x04` | 3 | **Right** USB-A, USB 2 lane (companion: XHC port 14) |
| 6 | HS05 | `0x06` | 255 (internal) | ELAN WBF Fingerprint Sensor (`04F3:0C4F`) |
| 7 | HS06 | `0x07` | 255 | Chicony HD Webcam (`04F2:B5C5`) |
| 10 | HS07 | `0x0A` | 255 | Intel AX201 Bluetooth (`8087:0026`) |
| 13 | SS01 | `0x0D` | 3 | Left USB-A SuperSpeed lane (companion of HS03) |
| 14 | SS02 | `0x0E` | 3 | Right USB-A SuperSpeed lane (companion of HS04) |
| 5, 8, 9, 11, 12, 15–18 | — | — | — | Empty (unmapped) |

### TXHC controller — pcidebug `0:13:0` (PCI `0:0D:0` hex), 5-port root hub, SS-only

| Phys port | macOS label | `port` byte | UsbConnector | Device / role |
|---|---|---|---|---|
| 2 | SS01 | `0x02` | 9 | Left rear USB-C SuperSpeed lane (companion of XHC HS02) |
| 3 | SS02 | `0x03` | 9 | Left front USB-C SuperSpeed lane (companion of XHC HS01) |
| 1, 4, 5 | — | — | — | Empty |

### Companion pairing summary

| Physical port | USB 2 lane | USB 3 SS lane |
|---|---|---|
| Left USB-A | XHC HS03 | XHC SS01 |
| Right USB-A | XHC HS04 | XHC SS02 |
| Left front USB-C | XHC HS01 | TXHC SS02 |
| Left rear USB-C | XHC HS02 | TXHC SS01 |

### Outstanding: SD card reader

The SD reader is not enumerated on PCIe and not visible on any USB port at idle. It almost certainly only enumerates when media is inserted. To find its port: insert a microSD card in Windows, run USBTreeView, look for a newly-lit port — most likely XHC port 5/8/9/11/12/15–18 or TXHC port 1/4/5. Once the port is known, add a corresponding `HSnn`/`SSnn` entry to `USBMap.kext` with `UsbConnector = 255` (internal).

## Thunderbolt 3 Status & Test Plan

### Why this is promising on the SP513-54N

The Acer Spin 5 SP513-54N uses the i7-1065G7 (Ice Lake U-series), which has **integrated Thunderbolt (iTBT)** — the TBT controllers are part of the CPU package itself, not a discrete Titan Ridge / Alpine Ridge chip. Two iTBT NHI controllers appear at PCI device IDs `0x8a17` (`pcidebug 0:13:2`) and `0x8a0d` (`pcidebug 0:13:3`).

Crucially, **the reference Apple platform with this exact CPU + iTBT combination is the MacBookPro16,2** (13" MBP 2020). Since `config.plist` already spoofs `MacBookPro16,2`, Apple's `AppleThunderboltNHI` (v7.2.81) and `IOThunderboltFamily` (v9.3.3) configure themselves correctly without any per-machine SSDT patches.

Earlier guidance about needing `SSDT-TbtOnPCH-XHCI`, force-power scripts, or `SSDT-TB3-HotPlug` does **not** apply here — those are for Type1/2/3 discrete TBT controllers (Alpine Ridge, Titan Ridge add-in cards). The Type4 stack handles iTBT natively.

### Live verification (run any time on macOS)

```bash
# 1. Confirm both NHIs are visible to PCI
system_profiler SPPCIDataType | grep -A8 "Ice Lake Thunderbolt 3 NHI"
#   Expected: NHI #0 (Device 0x8a17, slot 0,13,2) and NHI #1 (Device 0x8a0d, slot 0,13,3),
#             both with "Driver Installed: Yes"

# 2. Confirm Apple's Thunderbolt kexts are loaded
kextstat | grep -i thunder
#   Expected: com.apple.iokit.IOThunderboltFamily (9.3.3)
#             com.apple.driver.AppleThunderboltNHI (7.2.81)

# 3. Confirm the full Type4 stack is built
ioreg -lw 0 | grep "+-o.*Thunderbolt"
#   Expected: 2× AppleThunderboltHALType4
#             2× AppleThunderboltNHIType4
#             2× IOThunderboltControllerType4
#             2× IOThunderboltLocalNode
#             2× IOThunderboltPort

# 4. With NO TBT device connected, this will say "No drivers are loaded"
#    — that's misleading; it actually means "no peripheral attached"
system_profiler SPThunderboltDataType
```

If steps 1–3 pass, the host TBT stack is healthy and ready. The "no drivers loaded" message in step 4 only changes when a TBT device is plugged in.

### Test plan with real TBT3 hardware

Borrow or buy any one of the following (cheap → expensive):

| Device | Cost | What it tests |
|---|---|---|
| TB3 SSD (e.g. Samsung X5, OWC Envoy Pro) | ~$100 | PCIe tunneling, NVMe over TBT |
| TB3 dock (CalDigit TS3 Plus, OWC TB3 Dock) | ~$200 | DP tunneling, USB hub passthrough, Ethernet |
| TB3 eGPU enclosure + AMD GPU (RX 580 / 5700 XT) | ~$400 | Full PCIe x4, GPU bind, Metal display path |

Plug the device into either USB-C port. Then capture:

```bash
# Watch the kernel log live as you plug in
log stream --predicate 'subsystem == "com.apple.iokit.IOThunderboltFamily" OR subsystem == "com.apple.iokit.IOPCIFamily"' --style syslog

# Or after the fact
log show --predicate 'subsystem == "com.apple.iokit.IOThunderboltFamily"' --last 5m

# Check what enumerated under the TBT bus
system_profiler SPThunderboltDataType
ioreg -lw 0 | grep -A5 "IOThunderboltSwitch\|AppleThunderboltDPInAdapter\|IOThunderboltPCIDownAdapter"
```

### Pass criteria

- ✅ **Cold-plug** (boot with device attached): `IOThunderboltSwitch` nub appears under one of the local nodes; the device-specific child (e.g. NVMe controller, USB hub, GPU) shows up at the bottom of the chain.
- 🧪 **Hot-plug** (insert after boot): same as cold-plug. iTBT/Type4 *should* support this natively, unlike the older Type1–3 stacks. If hot-plug fails but cold-plug works, that's a known iTBT-on-Hackintosh quirk and not a deal-breaker.
- 🧪 **Sleep/wake**: with TBT device attached, sleep the laptop, wake it. Device should re-enumerate. If it doesn't, that's a power-management issue separate from basic functionality.
- ❓ **Charging passthrough through TBT dock**: depends on dock's PD profile and whether macOS's PD driver claims the channel. Not a pure TBT issue.

### Realistic eGPU prospects

If basic TBT enumeration works:
- **AMD eGPU is the supported path.** RX 580 (Polaris), Vega 56/64, RX 5700 XT (Navi). Apple's drivers are native, Metal works, no driver hacks needed. Apple's official eGPU support list (until 2018) was AMD-only, and MBP16,2 was sold as eGPU-capable.
- **NVIDIA is still a no-go** for display/Metal. The third-party TinyGPU compute-only driver (tinygrad/Tiny Corp, 2026) targets Apple Silicon + Thunderbolt 4; Intel Mac and Hackintosh paths are unverified. Don't count on it.

### What still might break TBT in practice

Even with the stack initialized:
1. **BIOS Thunderbolt settings.** Some Insyde firmwares default the TBT controller to "Pre-Boot ACL Disabled" or "Security Level: User Authorization" which may interfere with macOS's automatic "always allow" approach. Check BIOS for any Thunderbolt section before testing.
2. **Power gating.** iTBT controllers can deep-sleep when idle. macOS handles this on real Macs; on Hackintosh it usually works but watch for "TBT controller link down" in `log stream` after sleep.
3. **DROM (Device ROM) on cheap TBT devices.** Some no-name TBT3 enclosures have malformed DROM data that real Macs reject. Stick with Apple-friendly brands (CalDigit, OWC, Razer, Akitio, Sonnet) for first tests.

### What to update here when you test

After your first successful TBT device enumeration:
- Flip the status row in [What Works](#what-works) from 🧪 to ✅ (or a granular set: `✅ cold-plug, ⚠️ hot-plug, ❌ sleep/wake` etc.)
- Add a "Tested TBT devices" subsection listing what worked
- If you go down the eGPU path, document the GPU model, enclosure, boot-args (`-wegnoegpu` etc.), and any IOReg evidence

## Known ACPI Warnings (Benign)

During verbose boot, you will see:

```
ACPI Warning: Unsupported module-level executable opcode 0x70 ... (×12)
```

These are caused by Apple's outdated ACPICA implementation in XNU (from 2016) not recognizing newer firmware opcodes. They appear on real Macs too and are harmless.

```
ACPI Error: [SLPB] Namespace lookup failure, AE_ALREADY_EXISTS
ACPI Error: 1 table load failures, 34 successful
```

This is caused by `SSDT-SLPB.aml` injecting a Sleep Button that already exists in the DSDT. Can be fixed by disabling `SSDT-SLPB.aml` in `config.plist`. Does not affect functionality.

## Removing Debug Boot Arguments

Once your Hackintosh is stable, clean up boot-args for daily use:

Remove: `keepsyms=1 debug=0x100 -v`

These are debugging aids that slow boot and show verbose text. Keep everything else.

## Credits

- [Acidanthera](https://github.com/acidanthera) — OpenCore, Lilu, WhateverGreen, VirtualSMC, AppleALC
- [OpenIntelWireless](https://github.com/OpenIntelWireless) — AirportItlwm, IntelBluetoothFirmware
- [VoodooI2C](https://github.com/VoodooI2C) — I2C trackpad and touchscreen drivers
- [Dortania](https://dortania.github.io/) — OpenCore Install Guide
- [wfx1024](https://github.com/wfx1024/Acer-Spin-5-SP513-54N-hackintosh) — Reference EFI for this model
- [tunglamvghy](https://github.com/tunglamvghy/AcerSpin5-SP513-54N-hackintosh) — Reference EFI for this model
- [Zhihu article (zhuanlan.zhihu.com/p/438210561)](https://zhuanlan.zhihu.com/p/438210561) — MMIO 0xFF600000 fix for Acer Ice Lake EC issue

## License

This configuration is provided as-is for educational purposes. Use at your own risk. macOS is a trademark of Apple Inc.
