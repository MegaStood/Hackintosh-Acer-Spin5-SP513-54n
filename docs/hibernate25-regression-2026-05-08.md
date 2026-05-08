# Hibernate-25 Regression — Spin 5 SP513-54N (2026-05-08)

Side-by-side comparison of two OpenCore boot logs from the same physical hardware:

- **S — May 2 2026 15:58:56** — successful hibernate-25 resume from sleepimage
- **F — May 8 2026 14:35:29** — failed hibernate cycle (cold boot after `0x002A001F` EFI/BootROM fault)

The system reproduces an EFI/BootROM `Failure during sleep: 0x002A001F : EFI/Bootrom Failure after last point of entry to sleep` on every hibernate-25 attempt since the config drifted from the May 2 state. This document enumerates every meaningful difference between the two boots and ranks them by causal relevance.

## Symptom

```
pmset -g log
... Entering Sleep state due to ... :TCPKeepAlive=active Using Batt
... ShutdownCause: SMC shutdown cause: 5: Software initiated shutdown
... HibernateStats: hibmode=0 standbydelaylow=0 standbydelayhigh=0
... Failure: Failure during sleep: 0x002A001F : EFI/Bootrom Failure after last point of entry to sleep
```

Tracepoint `0x002A` = `kIOPMTracePointSleepPlatformActions` — the kernel had finished suspending drivers and handed off to firmware (UEFI runtime services) for the actual power-off / hibernate image write. The fault occurs inside firmware, not in any kext. SMC then watchdog-reboots the box.

The next boot finds no `boot-image` NVRAM variable, so OpenCore falls through to the picker (cold boot), and the user typically lands in Windows because Insyde wipes `BootOrder`/`Boot####` on every cold boot from this state.

## Differences ranked by causal relevance

### 1. Missing `_PTS to ZPTS` and `_WAK to ZWAK` ACPI patches  ⚠ **likely root cause**

May 2 applies 12 ACPI patches at indices 0–7, 9–11. May 8 applies only 9, missing indices **4** and **5**:

```
S (works):  patch (_PTS to ZPTS)  at 4   ← present
S (works):  patch (_WAK to ZWAK)  at 5   ← present
F (fails):  patch (GPRW to XPRW)  at 6   ← jumps directly from 3 to 6
```

These two patches rename the firmware's native `_PTS` (Prepare to Sleep) and `_WAK` (Wake) methods to `ZPTS`/`ZWAK`. Combined with `SSDT-PTSWAKTTS-iGPU.aml` and `SSDT-NameS3-disable.aml`, they replace Insyde's native sleep/wake methods with patched versions that don't trigger the `0x002A001F` fault.

**Without the rename**, the SSDT replacements load but never execute — the original Insyde `_PTS`/`_WAK` runs at sleep entry and wake, and that path is exactly where `0x002A001F` originates.

### 2. Missing `SSDT-PTSWAKTTS-iGPU.aml` SSDT  ⚠ **paired with #1**

May 2 inserts SSDT at index 44 with OEM ID `00004B4157535450` ("PTSWAK") of 517 bytes. May 8's log shows `OC: Skipping add ACPI SSDT-PTSWAKTTS-iGPU.aml (0)` — this SSDT is now `Enabled=false` in `ACPI > Add`.

This SSDT defines the replacement `_PTS`/`_WAK` methods that pair with the rename patches in #1. Both must be on or both off; on its own each is useless.

### 3. NVRAM emulation does not work for hibernate-25  ⚠ **breaks resume even when sleep entry succeeds**

May 2 driver list (3 drivers): `HfsPlus`, `OpenCanopy`, `OpenRuntime`.
May 8 driver list (4 drivers): adds `OpenVariableRuntimeDxe.efi` ("Emulated NVRAM"), with both `OpenVariableRuntimeDxe` and `OpenRuntime` set to `LoadEarly=true`.

May 8 OC log:
```
OCVAR: Found FW NVRAM, forcing redirect 1
OCVAR: Loading NVRAM from storage...
OCVAR: Restoring FW NVRAM...
```

The chain *intended* to work as: macOS writes `boot-image` → emulated NVRAM intercepts → LogoutHook flushes to `EFI/NVRAM/nvram.plist` on shutdown → next boot OC restores it → resume succeeds.

The actual failure mode: hibernate-25 writes `boot-image` at sleep entry and immediately powers off via firmware. **LogoutHook is a launchd-on-logout hook — it does not fire on the hibernate path**. So the variable lives only in volatile emulated storage and dies with the power. Next boot:

```
F:  OCB: boot-image is 0 bytes - Not Found
F:  OCB: NVRAM hibernation is 0 / Not Found / 0
F:  OC: Hibernation activation - Not Found, hibernation wake - no
```

Compare May 2 (no emulation, writes go to firmware NVRAM):

```
S:  OCB: boot-image is 70 bytes - Success
S:  OCB: NVRAM hibernation is 1 / Success / 44
S:  OC: Hibernation activation - Success, hibernation wake - yes
```

**Emulated NVRAM is the wrong tool for hibernate-25 persistence.** `RequestBootVarRouting=true` (already on in both configs) is the right Insyde-clobber mitigation; emulation adds nothing useful here and breaks the hibernate resume path.

### 4. Sleep-tuned boot-args removed or weakened

May 2 NVRAM `boot-args`:
```
keepsyms=1 debug=0x100 -btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck
agdpmod=vit9696 darkwake=0 igfxonln=1 -noDC9 forceRenderStandby=0
alcid=13 -v -no_compat_check
```

May 8 NVRAM `boot-args` (rewritten by config — note `OC: Deleting NVRAM ...:boot-args - Success` in the F log):
```
keepsyms=1 debug=0x100 -btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck
agdpmod=ignore alcid=13 -v -no_compat_check igfxonln=1 slide=66
```

| Lost or changed | Effect |
|---|---|
| `darkwake=0` removed | Dark wakes from `mDNSResponder`, `dasd` Safe Browsing, etc. fire from S3, re-entering the broken `_PTS`/`_WAK` path |
| `-noDC9` removed | iGPU enters DC9 deep sleep; firmware's GPU resume code is part of the `0x002A` failure surface |
| `forceRenderStandby=0` removed | iGPU Render Standby active; same family of fix |
| `agdpmod=vit9696` → `agdpmod=ignore` | Different `AppleGraphicsDevicePolicy` mode; `vit9696` is the known-good value for this hardware |

## Other observable differences (non-causal)

### Device properties drift

iGPU (`PciRoot(0x0)/Pci(0x2,0x0)`):
- **S only**: `enable-cfl-backlight-fix`
- **F only**: full `framebuffer-con1-*`, `framebuffer-con2-*`, `framebuffer-con3-*` blocks (busid/enable/flags/pipe/type per connector — these are the VBT-canonical overrides added in commit `1f15d62`)

HDA (`PciRoot(0x0)/Pci(0x1F,0x3)`):
- **S** sets `device-id`, `hda-gfx`, `layout-id`, `model`
- **F** sets `device-id`, `layout-id`, `model` — **`hda-gfx` removed**

### Boot orchestration

| | S | F |
|---|---|---|
| BootNext present | Yes — `Boot0082` autopilots to macOS boot.efi | No — `BootOrder/BootNext are not present or unsupported 0 0` |
| Picker shown | No (autopilot) | Yes — 3 s manual pick (02:909 → 05:954) |
| Filesystems registered | 1 (target only via Boot0082) | 7 (full enumeration) |
| Default selection | Macintosh HD index 0 | Macintosh HD index 2 (Windows is index 0) |

The absence of `BootNext`/`BootOrder` in F is a **symptom** of the prior failed hibernate, not a cause — Insyde wipes them on cold boot from this state.

### ESP partition resized (cosmetic)

May 2 boot-path: `HD(2,GPT,…,0x40800,0x32000)` (100 MiB)
May 8 boot-path: `HD(2,GPT,…,0x40800,0x6E800)` (226 MiB)

ESP was grown via `parted resizepart` + `fatresize`. No causal relation to hibernate behavior.

### Other config metadata

| | S | F |
|---|---|---|
| `config.plist` size | 97421 bytes | 101072 bytes |
| DMI table length | 0BC5 | 0BCA |
| DMI prev counts | 3013/31, 3013/24 | 3018/31, 3018/24 |

### Hibernation-only flow (S only — proves successful resume)

```
EB|H:IS 1                            ← Is hibernation = yes
EB|H:WCK                             ← Wake check
SmcWriteValue Key 48424B50           ← HBKP (hibernate boot key)
SmcWriteValue Key 48424B55           ← HBKU (hibernate user key)
EB|H:BOOT.1
EB|H:DLW 0
EB|H:SIG 0x73696D65                  ← 'emis' = sleepimage signature
EB|H:HBOPT 0 1 1 0
EB|H:RDST                            ← Read state
EB|LOG:REAN:START 2026-05-02T15:58:58
(7-second gap reanimating from sleepimage)
EB|LOG:REAN:END 2026-05-02T15:59:05
EB|H:RDEND
EB.H.HB|DT 3 0
```

### Cold-boot-only flow (F only — proves no resume happened)

```
EB.H.LV|! Err(0xE) <- RT.GV boot-signature
EB.H.LV|! Err(0xE) <- RT.GV boot-image-key
EB.H.LV|! Err(0xE) <- RT.GV boot-image
EB|H:NOT                             ← Not a hibernation resume
... full kernel cache load + kext injection
```

`Err(0xE)` = `EFI_NOT_FOUND` — the hibernation NVRAM variables are absent (not corrupt).

## Recommended rollback (in order, test after each)

### Step 1 — Re-enable the `_PTS`/`_WAK` rename and SSDT

In `EFI/OC/config.plist`:

- `ACPI > Patch`: re-enable the patch with `Comment` containing `_PTS to ZPTS` (set `Enabled=true`)
- `ACPI > Patch`: re-enable the patch with `Comment` containing `_WAK to ZWAK` (set `Enabled=true`)
- `ACPI > Add`: re-enable `SSDT-PTSWAKTTS-iGPU.aml` (set `Enabled=true`)

Verify with `ocvalidate` and reboot. The OC log should show the patch indices match May 2 (12 patches at 0–7, 9–11) and SSDT count back to 20.

### Step 2 — Remove NVRAM emulation

In `EFI/OC/config.plist`:

- `UEFI > Drivers`: set `OpenVariableRuntimeDxe.efi` to `Enabled=false` (or remove the entry)
- `UEFI > Drivers > OpenRuntime.efi`: set `LoadEarly=false`
- `NVRAM > LegacyOverwrite`: leave `false`
- `NVRAM > LegacySchema`: leave empty `<dict/>`
- Optionally remove the `EFI/NVRAM/` directory and `nvram.plist` from the ESP — they're now unused

Keep `Booter > Quirks > RequestBootVarRouting=true` — that is the *correct* Insyde-clobber mitigation and works without emulation.

### Step 3 — Restore boot-args

In `EFI/OC/config.plist > NVRAM > Add > 7C436110-AB2A-4BBB-A880-FE41995C9F82 > boot-args`:

```
keepsyms=1 debug=0x100 -btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck agdpmod=vit9696 darkwake=0 igfxonln=1 -noDC9 forceRenderStandby=0 alcid=13 -v -no_compat_check
```

Make sure `NVRAM > Delete > 7C436110-… > boot-args` is present so OC actually overwrites whatever's currently in firmware NVRAM (otherwise the Add is a no-op when the key already exists).

### Step 4 — Optional: HDA `hda-gfx`

If audio routing on HDMI/DisplayPort outputs regresses, restore `hda-gfx` under `DeviceProperties > Add > PciRoot(0x0)/Pci(0x1F,0x3)`. Not related to hibernate but observed in the diff.

## Validation criteria

After step 1 + 2 + 3:

```sh
# OC config validity
ocvalidate /Volumes/EFI/EFI/OC/config.plist

# pmset baseline
sudo pmset -a hibernatemode 25
sudo rm -f /var/vm/sleepimage

# Trigger
sudo pmset sleepnow

# Wait until LED off (S4) — typically 30–60 s after sleepnow
# Press power. Expected:
#   - OC picker either does not appear (BootNext autopilot) OR appears briefly with macOS as default
#   - macOS resumes from sleepimage (fast, ~5–10 s)
#   - Apps and windows are exactly as left

# Post-resume verification
ls -la /var/vm/sleepimage                                  # exists, ~RAM size
pmset -g log | grep -iE "HibernateStats|Failure" | tail -5
# Expect:  HibernateStats: hibmode=25 ... time-to-write=Xs (Y MB)
# NOT:     Failure: ... 0x002A001F

# OC log on the resume boot should contain:
#   boot-image is N bytes - Success
#   NVRAM hibernation is 1 / Success / N
#   Hibernation activation - Success, hibernation wake - yes
```

If `0x002A001F` still fires after step 1 + 2 + 3, the firmware bug is in something other than the four regressions identified here, and we'd need to bisect further (likely starting with the iGPU framebuffer-con* additions from commit `1f15d62`, which are the only other meaningful sleep-affecting change since May 2).

## Why several earlier hypotheses were wrong

For the record, three pmset-side knobs we tried as part of the investigation turned out to be irrelevant:

- **`standby 0`** — May 2 had `standby=1` and worked fine. `standby` only governs S3→S4 auto-transition, which is moot under `hibernatemode=25`.
- **`tcpkeepalive 0`** — powerd's debug log reports `TCPKeepAliveState: unsupported` on this Hackintosh. The runtime kernel state ignores the pmset value entirely.
- **`powernap 0` / `proximitywake 0`** — defensible for sleep cleanliness but not the cause; both were `1` on May 2 and hibernate worked.

The actual differences are all in the OpenCore config, enumerated above.
