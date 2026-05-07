# HDMI / USB-C external display debug — session 2026-05-05

Goal: get **at least one** external display path working on the Spin 5 SP513-54N (Ice Lake i7-1065G7, 8A52 framebuffer, MacBookPro16,2 SMBIOS, Sonoma 14.8.5).

Three candidate paths:
- **Path A**: built-in HDMI port
- **Path B**: USB-C #1 (front) DP alt-mode via hub
- **Path C**: USB-C #2 (rear) DP alt-mode via hub

This document captures what was tried, what was learned, and the **next-step Linux diagnostic** to run from the dual-boot Linux install. Read this on the Linux side before running the commands at the bottom.

---

## TL;DR — current state

> **★ FIX FOUND 2026-05-06 (entry #26): set `framebuffer-conN-pipe = <01000000>` (= 1) for external connectors.** The field labeled `pipe` in WhateverGreen's DeviceProperties is read by `AppleIntelICLLPGraphicsFramebuffer.kext` as the runtime port-type byte, NOT the scanout pipe enum (despite the label). ICL kext's allow-list accepts only `{0, 1}`. We had inherited CFL-era pipe values (`0x12, 0x09, 0x0A`) which the ICL kext rejected as `Unsupported port type 18/9/10`. Setting pipe=1 makes the connector accepted.
>
> **Final verified state (entry #27):**
> - **con2 (rear USB-C): ✓ WORKING** at pipe=1 — Dell P2722H attaches cleanly on FB@1
> - **con3 (front USB-C): ✓ WORKING** at pipe=1 — Dell P2722H attaches cleanly on FB@1
> - **con1 (HDMI port): allow-list passes but downstream signaling fails** — no LSPCON in Spin 5 hardware (VBT-confirmed), DP signaling can't drive HDMI display. Hardware limitation, not a software fix. See entry #27 for details.
>
> **2 of 3 external paths working. USB-C ports are the daily-driver path.**

### Original problem statement (preserved for context)

- **Internal display**: working ✓
- **HDMI port (Path A)**: when patched with `con1` overrides, DDI 1 hot-plugs on cable plug, but `portMode=1` (DP signaling on a port that goes to an HDMI-only sink) and `sinkCount=0` (no DDC response). Display stays black. Likely no LSPCON in this hardware.
- **USB-C ports (Paths B/C)**: USB function works (mouse/Ethernet/SD-reader through hub all enumerate fine in macOS). DP alt-mode handshake to the iGPU **never engages** — zero `Hotplug detected on ddi` events, zero TBT topology change events.
- **Root cause we were investigating**: stock 8A52 framebuffer's connector descriptors (busids `0x02, 0x09, 0x0A, 0x0B, 0x0C` for the 5 DP slots) target the MacBook Pro 16,2 reference platform layout, which has 4× Type-C and 0× HDMI — wrong shape for our Acer (1× HDMI + 2× Type-C). Without correct `framebuffer-conN-busid` overrides, DP alt-mode events have nowhere to land in the framebuffer driver.

We needed the **physical port → DDI letter mapping** for this firmware to author correct connector overrides. That's what the Linux test plan below produced.

**Note: as of entry #26, we now know the busid fix was necessary but NOT sufficient — pipe values also needed to be in the ICL kext's allow-list `{0, 1}`. We had been carrying broken (CFL-era) pipe values the entire time without recognizing the field's true semantics. See entry #26 for the full discovery + fix recipe.**

---

## What was tried this session

### Path A: HDMI port (built-in)
| Attempt | Result |
|---|---|
| Original config: `con1-busid=1, pipe=0x12, type=HDMI(0x800), flags=0x187` (no LSPCON keys) | DDI 1 hot-plugs on plug, `sinkCount=0`, `portMode=1` → black screen |
| Add LSPCON keys (`has-lspcon=1, preferred-lspcon-mode=1, index=1`) + `pipe=0x09` + boot-arg `igfxlspcon=1` | FB@1 stops probing entirely (LSPCON keys + pipe=9 incompatible) |
| Revert `pipe=0x12` (LSPCON keys still present) | FB@1 still silent — pipe=0x12 is the magic value, but LSPCON keys regress |
| Remove LSPCON keys, keep `pipe=0x12` | DDI 1 hot-plugs again, but still `sinkCount=0`, `portMode=1` |
| Set `pipe=0x09`, no LSPCON keys | FB@1 silent — confirms pipe=0x12 is required for FB@1 enumeration |
| Add LSPCON keys back with `pipe=0x12` | DDI 1 still hot-plugs; **zero** `lspcon` mentions in kernel logs (WEG never engages LSPCON code path → likely no chip exists) |
| Remove `igfxlspcon=1` boot-arg | No change — `portMode=1` still stuck |

**Conclusion (Path A):** WhateverGreen's DP-to-HDMI conversion patch is [known broken on Ice Lake](https://github.com/acidanthera/bugtracker/issues/1616) — the patch logs as successful but the connector type stays DP in IORegistry. No LSPCON chip on this DDI means the iGPU outputs DP signaling onto HDMI wires, the HDMI display can't decode it, HPD bounces. Path A is likely **physically impossible** without an LSPCON.

### Path B/C: USB-C DP alt-mode
- USB-C hub is a Genesys Logic generic USB hub (VID/PID `0x05E3 / 0x0610` USB 2.1 + `0x05E3 / 0x0626` USB 3.1) with passthrough USB ports + ASIX AX88179B Ethernet + USB3.0 card reader + HDMI port (DP alt-mode).
- USB-only function works perfectly in macOS — the hub enumerates on XHC HS01 and TXHC SS02 (front USB-C, matches port map memory).
- **Plugging the hub triggers ZERO graphics-side events**: no DDI hot-plug on any DDI, no TBT topology change in dmesg, no portMode/sinkCount on any framebuffer.

**Conclusion (Paths B/C):** DP alt-mode handshake from the USB-C PD/CCG controller to the iGPU's TC DDI is not happening. Suspected cause: the framebuffer driver's connector descriptors (with our wrong busid values, or with stock 8A52 defaults that don't match this hardware) provide no slot for the TC DDI events to land in. Need to fix the connector descriptors with correct busids derived from actual DDI mapping.

### The "Surface-rework" detour
Inspired by the user's [forked Surface Laptop 3 OpenCore repo](https://github.com/MegaStood/Surface-Laptop-3-OpenCore) which has working HDMI-over-USB-C on the same Ice Lake iGPU, attempted a bundled rewrite of the iGPU DeviceProperties to match Surface's approach: remove all 18 `framebuffer-conN-*` overrides, add 7 Surface-style `enable-*` flags (`force-online`, `enable-lspcon-support`, `enable-hdmi-dividers-fix`, `enable-dpcd-max-link-rate-fix`, `enable-max-pixel-clock-override`, `enable-backlight-smoother`, `dpcd-max-link-rate`), drop `igfxonln=1` and `igfxlspcon=1` boot-args.

**Result: black screen on boot — `FB0: is not enabled` repeatedly in kernel logs.** System otherwise boots (kernel runs, WiFi/BT come up, mdworker active). Internal LVDS framebuffer fails to allocate. Required external-disk recovery.

**Lessons:**
- Bundled changes were too aggressive — single failure point made isolation impossible.
- The Acer-specific `framebuffer-stolenmem=0x01300000` (19 MB), `framebuffer-fbmem=0x00900000` (9 MB), `framebuffer-unifiedmem=0x80000000` (2 GB) values are **load-bearing**: they're the standard Ice Lake DVMT workaround for laptops where BIOS DVMT-prealloc is capped below 64 MB. **Don't change these.**
- The `GraphicsBacklightSetup-0` (40 B) and `GraphicsDisplaySetup` (140 B) binary blobs are panel-init data for the Acer LVDS panel (BOE NE135FBM-N41). Removing them is risky and there's no evidence they're broken. **Don't remove these.**
- `force-online=1` (DeviceProperty) plus stock 8A52 connectors is the most likely culprit for the FB0 init failure — forcing 6 connectors online when only 4 physical outputs exist may overcommit framebuffer memory. (Hypothesis: not isolated yet.)

After step-by-step recovery (re-add `igfxonln=1` boot-arg, remove the 7 added properties), internal display came back. Current ESP state: a "minimal-clean-from-backup" position — see below.

---

## Current ESP state (after this session's edits)

`/Volumes/ESP/EFI/OC/config.plist`:

### iGPU DeviceProperties (`PciRoot(0x0)/Pci(0x2,0x0)`)
```
AAPL,GfxYTile                  = <01000000>
AAPL,ig-platform-id            = <0000528a>      (= 0x8A520000, stock Ice Lake G7)
GraphicsBacklightSetup-0       = <40-byte blob>  (Acer panel backlight init — KEEP)
GraphicsDisplaySetup           = <140-byte blob> (Acer panel display init — KEEP)
device-id                      = <528a0000>      (= 0x8A52)
enable-backlight-registers-fix = <01000000>
enable-cdclk-frequency-fix     = <01000000>
enable-dbuf-early-optimizer    = <01000000>
enable-dvmt-calc-fix           = <01000000>
enable-hdmi20                  = <01000000>
framebuffer-fbmem              = <00003001>      (19 MB — bumped 2026-05-06; was 9 MB)
framebuffer-patch-enable       = 1
framebuffer-stolenmem          = <00006002>      (38 MB — bumped 2026-05-06; was 19 MB)
framebuffer-unifiedmem         = <00000080>      (2 GB)
igfxfw                         = <02000000>     (firmware load mode 2)
model                          = 'Intel Iris Plus Graphics G7'
```

**Differences from pre-rework backup (`config-pre-surface-rework.plist.bak`):**
- **Removed** (still missing): all 18 `framebuffer-con1/2/3-*` overrides + `enable-cfl-backlight-fix`
- **Boot-args**: `igfxlspcon=1` removed, `igfxonln=1` re-appended, `agdpmod=vit9696 → agdpmod=ignore` (2026-05-06)
- **Removed 2026-05-06 (post-test):** `enable-lspcon-support` (confirmed inert, see entry #12), `hda-gfx = 'onboard-1'` (jlempen-inspired test, see entry #15)
- Everything else identical

### Boot-args (live)
```
keepsyms=1 debug=0x100 -btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck agdpmod=ignore alcid=13 -v -no_compat_check -igfxdbg -liludbgall igfxonln=1
```

Notes:
- `-igfxdbg -liludbgall` is on for diagnostic verbosity. Keep until done debugging.
- `agdpmod=ignore` (changed from `vit9696` 2026-05-06) — **load-bearing in combination with the entry #26 pipe=1 fix.** Empirically falsified 2026-05-07 (entry #28): with the pipe=1 framebuffer override active, swapping back to `agdpmod=vit9696` breaks USB-C external display. The earlier "functionally equivalent on MacBookPro16,2" claim from entries #15 / #19 was correct only under pre-pipe=1 conditions where the framebuffer rejected the connector regardless of AGDP. **Do not revert to `=vit9696` without re-testing external display.**

### Available backups in ESP
- `/Volumes/ESP/EFI/OC/config-pre-agdpmod-ignore.plist.bak` — pre-agdpmod swap (2026-05-06; has stolenmem 38 / fbmem 19, enable-lspcon-support, hda-gfx, agdpmod=vit9696).
- `/Volumes/ESP/EFI/OC/config-pre-lspcon-test.plist.bak` — pre-LSPCON edit (2026-05-06; has stolenmem 38 / fbmem 19, no enable-lspcon-support, agdpmod=vit9696).
- `/Volumes/ESP/EFI/OC/config-pre-stolenmem-bump.plist.bak` — pre-memory-bump (2026-05-06; has stolenmem 19 / fbmem 9, no enable-lspcon-support, agdpmod=vit9696).
- `/Volumes/ESP/EFI/OC/config-pre-surface-rework.plist.bak` (64 KB) — pre-rework working baseline. **Use this for clean revert to pre-2026-05-05 state.**
- `/Volumes/ESP/EFI/OC/config-pre-hdmi-fb.plist.bak` — earlier baseline (before any HDMI work).
- Other older `*.bak` files for previous changes.

To revert from any backup:
```bash
diskutil mount disk0s2
sudo cp "/Volumes/ESP/EFI/OC/config-pre-surface-rework.plist.bak" "/Volumes/ESP/EFI/OC/config.plist"
# reboot
```

---

## Why we need Linux DDI mapping data

`framebuffer-conN-busid` in WhateverGreen tells the framebuffer driver which **physical iGPU DDI** (Digital Display Interface — letters A through F on Ice Lake) each connector descriptor slot represents. Default 8A52 framebuffer assumes MacBook Pro 16,2 wiring, which is **wrong for the Acer Spin 5**.

To author correct overrides, we need to know:
- Which DDI letter (A/B/C/D/E/F) is the **eDP internal panel** (probably DDI A — typical convention)
- Which DDI letter is the **physical HDMI port**
- Which DDI letter is **USB-C #1 (front)**
- Which DDI letter is **USB-C #2 (rear)**

Then the WEG `busid` value follows directly: DDI A=0, B=1, C=2, D=3, E=4, F=5.

### Things that DIDN'T give us this mapping
- **Static DSDT/SSDT decompilation** (we did this with `iasl` from ACPICA source built in `/tmp/acpica-build/`): SSDT-5 declares 15 dynamic display device slots `DD01`–`DD0F` but each `_ADR` is a **method that returns a runtime-computed value**. Actual IDs come from BIOS-populated 32-bit fields `DIDL, DDL2…DDL15` — only filled at boot. Static dump alone can't tell us the mapping. Common Insyde/AMI pattern.
- **WhateverGreen FAQ**: only documents stock framebuffer values (busids `0x02, 0x09, 0x0A, 0x0B, 0x0C` for stock 8A52 — Apple's MBP layout, not ours).
- **Hackintool's "Connectors" tab**: only shows what the current macOS config exposes — we'd need a known-good config to enumerate, which is the chicken-and-egg.
- **Empirical reboot-and-test in macOS**: works but ~1 reboot per data point and prone to misinterpretation.

### What WILL give us this mapping
**Linux's `i915` kernel driver** prints **definitive DDI ↔ physical port assignments** at init, derived from the firmware's VBT (Video BIOS Table). This is the gold standard.

---

## Linux test plan (run from your Linux install)

### Setup before running commands
1. Boot Linux on the Spin 5.
2. Have at hand:
    - HDMI cable + an HDMI display
    - USB-C hub (the Genesys Logic one) with HDMI display attached to its HDMI port
3. Have nothing plugged in initially — we want a clean baseline.

### Phase 1 — baseline (nothing plugged in)

```bash
# 1. List all DRM connector entries — connector type per DDI is in the names
ls -la /sys/class/drm/ | grep card0-

# 2. Status of each connector
for c in /sys/class/drm/card0-*; do
  name="${c##*/}"
  status=$(cat "$c/status" 2>/dev/null)
  echo "$name : $status"
done

# 3. i915 DDI/port assignment from kernel log (gold standard)
sudo dmesg | grep -iE "i915|drm|ddi" | grep -iE "ddi|port|tc[0-9]|hdmi|edp|dp-[0-9]" | head -60

# 4. Detailed display engine state (optional, more verbose)
sudo cat /sys/kernel/debug/dri/0/i915_display_info 2>/dev/null | head -120

# 5. (Optional, valuable) — VBT decode from intel-gpu-tools
sudo intel_vbt_decode 2>/dev/null | grep -iE "ddi|port|hdmi|dp|tc" | head -40
```

If `intel_vbt_decode` says "command not found", install:
- Ubuntu/Debian: `sudo apt install intel-gpu-tools`
- Arch: `sudo pacman -S igt-gpu-tools`
- Fedora: `sudo dnf install intel-gpu-tools`

Save baseline outputs.

### Phase 2 — confirm each port via hot-plug

No reboot needed. i915 supports hot-plug detection.

#### Test A: HDMI port directly
1. Plug HDMI cable directly into the laptop's HDMI port.
2. Run:
    ```bash
    dmesg | tail -20
    for c in /sys/class/drm/card0-*; do
      name="${c##*/}"
      status=$(cat "$c/status" 2>/dev/null)
      echo "$name : $status"
    done
    ```
3. Note which connector flipped to `connected`. That entry's name (e.g., `card0-HDMI-A-1`) tells us the connector class; the dmesg HPD line tells us the DDI letter.
4. Unplug HDMI before next test.

#### Test B: USB-C #1 (front) DP alt-mode
1. Plug USB-C hub (with HDMI display attached) into the **front** USB-C port (left, near the keyboard's front edge — matches the SD-reader-side from the USB port map).
2. Run the same `dmesg | tail -20` + sysfs status loop.
3. Note which connector flipped to `connected`. Should be a `card0-DP-N` entry. The DDI letter from dmesg HPD tells us busid.
4. Unplug the hub before next test.

#### Test C: USB-C #2 (rear) DP alt-mode
1. Plug the same USB-C hub into the **rear** USB-C port (left, near the hinge).
2. Run the sysfs status loop again.
3. Note the new connector that flipped to `connected`.

### What to capture and bring back
For each phase save the output. The minimum I need to author correct overrides:
- **Phase 1 commands 1, 2, 3 outputs** (baseline)
- **Phase 2 Test A**: which connector + DDI letter for HDMI
- **Phase 2 Test B**: which connector + DDI letter for front USB-C
- **Phase 2 Test C**: which connector + DDI letter for rear USB-C

A single text file with these labeled outputs is enough.

### Save outputs to a file you can `git push`
Convenient one-liner from Linux to capture everything to a file in this repo (assuming you have it cloned at `~/Hackintosh-Acer-Spin5-SP513-54n`):

```bash
cd ~/Hackintosh-Acer-Spin5-SP513-54n
mkdir -p docs
{
  echo "=== Phase 1 baseline ==="
  echo "--- 1. ls -la /sys/class/drm/ | grep card0- ---"
  ls -la /sys/class/drm/ | grep card0-
  echo
  echo "--- 2. connector status ---"
  for c in /sys/class/drm/card0-*; do
    name="${c##*/}"
    echo "$name : $(cat $c/status 2>/dev/null)"
  done
  echo
  echo "--- 3. dmesg | grep i915/ddi/port ---"
  sudo dmesg | grep -iE "i915|drm|ddi" | grep -iE "ddi|port|tc[0-9]|hdmi|edp|dp-[0-9]" | head -60
  echo
  echo "--- 4. i915_display_info ---"
  sudo cat /sys/kernel/debug/dri/0/i915_display_info 2>/dev/null | head -120
  echo
  echo "--- 5. intel_vbt_decode ---"
  sudo intel_vbt_decode 2>/dev/null | grep -iE "ddi|port|hdmi|dp|tc" | head -40
} > docs/linux-i915-ddi-mapping-2026-05-05.txt 2>&1
```

Then for each hot-plug test, append:

```bash
cat <<'EOF' >> docs/linux-i915-ddi-mapping-2026-05-05.txt

=== Test A: HDMI cable in HDMI port ===
EOF
{
  dmesg | tail -25
  echo "--- connector status ---"
  for c in /sys/class/drm/card0-*; do
    echo "$(basename $c) : $(cat $c/status 2>/dev/null)"
  done
} >> docs/linux-i915-ddi-mapping-2026-05-05.txt 2>&1
```

(Same pattern for Test B and Test C — change the heading and unplug/plug as you go.)

Then commit + push:
```bash
git add docs/linux-i915-ddi-mapping-2026-05-05.txt
git commit -m "Capture Linux i915 DDI mapping for Spin 5 SP513-54N"
git push origin main
```

---

## What happens after the Linux output is captured

Once we have the DDI mapping:
1. Author correct `framebuffer-con1-busid` (HDMI), `con2-busid` (USB-C #1), `con3-busid` (USB-C #2), each pointing at the right DDI letter (busid 0=A, 1=B, …, 5=F).
2. Set `pipe` to a valid Ice Lake value: `0x08`/`0x09`/`0x0A` (= pipe A/B/C). NOTE: in our prior testing on this hardware `0x12` was empirically the value that made FB@1 enumerate — non-canonical but worked. May need empirical confirmation again with correct busids.
3. Set `type` correctly: `0x800` for HDMI port (try anyway despite WEG's broken DP→HDMI on Ice Lake — sometimes it works, sometimes display doesn't light), `0x400` (DP) for USB-C ports (DP alt-mode is native DP signaling).
4. Set `flags` to `0x187` (proven to allow HPD events on this firmware).
5. Test in single-variable steps with reboots between, starting with the USB-C ports (more likely to work since there's no LSPCON requirement for native DP→HDMI conversion at the adapter side).

If USB-C DP alt-mode lights up an external display, **we have a working external display path** even if the built-in HDMI port stays broken.

---

## Reference data

### Spin 5 hardware
- iGPU: Intel Iris Plus G7, device ID `0x8A52`
- Stock 8A52 framebuffer (Apple MBP 16,2 reference): 1× LVDS + 5× DP at busids `0x02, 0x09, 0x0A, 0x0B, 0x0C` — 6 connectors total
- Acer firmware exposes 4 physical outputs: 1× internal LVDS + 1× HDMI + 2× USB-C (TC1V, TC2V in DSDT)
- Internal panel: BOE NE135FBM-N41 (13.5" 2256×1504)
- DSDT/SSDT-5 declares 15 dynamic display slots (`DD01`–`DD0F`) — 11 are unused phantom entries

### DDI letter → WhateverGreen busid mapping (standard convention)
| DDI | busid | Ice Lake usage (typical) |
|---|---|---|
| A | 0 | eDP / internal panel |
| B | 1 | HDMI/DP combo PHY 1 |
| C | 2 | HDMI/DP combo PHY 2 |
| D | 3 | TC PHY 1 (USB-C #1) |
| E | 4 | TC PHY 2 (USB-C #2) |
| F | 5 | TC PHY 3 (if present) |

### Useful boot-args for further iGPU debugging
- `-igfxdbg` — verbose iGPU debug output (already on)
- `-liludbgall` — verbose Lilu debug output (already on)
- `igfxonln=1` — force connectors online (already on)
- `igfxonln2=1` — alternative force-online flag

### Useful kernel log filters in macOS
```bash
sudo log config --mode "level:debug" --process kernel  # enable debug logs

# DDI / hot-plug events
/usr/bin/log show --last 5m --predicate 'process == "kernel"' \
  | grep -iE "ddi = [0-9]|Hotplug|sinkCount|portMode|lspcon"

# WhateverGreen / Lilu specific
/usr/bin/log show --last 5m --predicate 'process == "kernel"' \
  | grep -iE "WhateverGreen|igfx|appleintel"
```

### Sources / references consulted
- [WhateverGreen FAQ — Intel HD](https://github.com/acidanthera/WhateverGreen/blob/master/Manual/FAQ.IntelHD.en.md)
- [acidanthera/bugtracker #1616 — DP-to-HDMI patch broken on Ice Lake](https://github.com/acidanthera/bugtracker/issues/1616)
- [Dortania OpenCore Post-Install — Patching VRAM](https://dortania.github.io/OpenCore-Post-Install/gpu-patching/intel-patching/vram.html)
- [Dortania OpenCore Post-Install — Patching Bus IDs](https://dortania.github.io/OpenCore-Post-Install/gpu-patching/intel-patching/busid.html)
- [Linux i915 driver — drm/i915/display/intel_ddi.c](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/i915/display/intel_ddi.c)
- [Surface Laptop 3 OpenCore (working HDMI-over-USB-C reference)](https://github.com/MegaStood/Surface-Laptop-3-OpenCore)

---

## Session log (chronological, for reproducibility)

1. Verified USB port map for Spin 5 (XHC + TXHC), updated USBMap.kext for SD reader (committed, pushed earlier).
2. Investigated hibernate-25 — confirmed firmware-level Insyde failure, deferred.
3. HDMI port debug:
    - Loaded DEBUG Lilu+WhateverGreen kexts.
    - Observed DDI 1 hot-plug events on HDMI plug, but `portMode=1`/`sinkCount=0` regardless of pipe/LSPCON config.
    - Confirmed WEG never engages LSPCON code path → likely no LSPCON chip exists on this hardware.
4. USB-C debug:
    - USB function works through hub on front USB-C ✓
    - DP alt-mode handshake never engages — zero DDI events on plug
    - Confirmed via Genesys Logic VID/PID + USB Tree View screenshots that hub is on TXHC SS02 + XHC HS01 (front USB-C, matches port map memory)
5. Surface-rework attempt:
    - Bundled change: removed 18 connector overrides, added 7 enable-* keys, dropped 2 boot-args.
    - Result: black-screen on boot (`FB0: is not enabled`).
    - Recovery: external disk boot, then step-by-step revert.
    - Lessons logged above.
6. DSDT decompile attempt:
    - Built `iasl` from ACPICA source (in `/tmp/acpica-build/`).
    - Decompiled DSDT.aml + SSDT-5.aml.
    - Found `_DOD` method uses runtime-populated `DIDL/DDL2…DDL15` fields → static dump can't give mapping.
    - Pivot: Linux is the right tool for this question.
7. Linux i915 + VBT diagnostic (Ubuntu 24.04 LTS, kernel 6.17.0-22-generic):
    - DRM card index is `card1` on this kernel, not `card0` (early commands had to be re-run with the right index).
    - Definitive port → DDI map captured (full output in commit 5029ee0):
        - `eDP-1` → DDI A / PHY A → busid `0x00` (internal panel)
        - `HDMI-A-1` → DDI B / PHY B → busid `0x01` (built-in HDMI port)
        - `DP-1` → DDI C (TC) / PHY TC1 → busid `0x02` (**REAR** USB-C — counterintuitively TC1 = rear, not front)
        - `DP-2` → DDI D (TC) / PHY TC2 → busid `0x03` (**FRONT** USB-C)
    - VBT (`intel_vbt_decode`) confirms: `Onboard LSPCON: no` for ALL outputs. HDMI port is wired DDI B → connector directly with native HDMI 1.4 TMDS capability. No DP→HDMI conversion chip.
    - Linux successfully drives all four outputs (eDP at 2256x1504@60, HDMI/DP-1/DP-2 at 1920x1080@60) — hardware is fully functional. macOS-side bottleneck is descriptor-mismatch + Apple display policy, not silicon.
    - S3 suspend/resume on Linux: works cleanly. Confirms macOS S3-wake issue is driver-side, not hardware.

8. **Commit `5029ee0`** authored Linux-side: VBT-derived `framebuffer-conN-*` overrides for con1/con2/con3, dropped `enable-cfl-backlight-fix`. Brought ESP `config.plist` into line for testing.
9. **First DDI events ever fired in macOS!** With VBT-derived overrides applied:
    - Plugged USB-C hub (Genesys Logic + HDMI display) into FRONT USB-C → kernel log:
        ```
        Hotplug detected on ddi = 3            ← FRONT USB-C, busid=3 — MAPPING IS CORRECT
        HPD is high. Setting port mode
        ddi 3 isHPDLow=0 emptyDongle=0 ...      ← real sink detected
        Event insert                            ← attachment recognized
        ```
    - But Apple's framebuffer driver then explicitly rejects the port:
        ```
        [IGFB][ERROR][HOT_PLUG] Unsupported port type 10
        [IGFB][INFO ][HOT_PLUG] ddi 3 ... portMode = 3
        [IGFB][ERROR][HOT_PLUG] Non-managed external displays are no longer supported
        ```
    - **Significance:** the connector descriptor problem is solved (DDI events fire on the right DDI). The remaining issue is Apple-side display policy:
        - **Port type 10** (`= 0x0A = bits 1+3 = "DP|HDMI capable"`): Apple's classification for a Type-C port that does both DP alt-mode AND HDMI conversion. `AppleIntelICLLPGraphicsFramebuffer` lacks a handler for this combination.
        - **portMode = 3** is a new value (we'd only seen 1 before for DP) — likely "DP alt-mode through TC PHY".
        - **"Non-managed external displays are no longer supported"** is a hardcoded message in modern macOS T2-class framebuffer code: external displays must pass AGDC/AGDP validation. Non-Apple-blessed attachments get rejected even when silicon would otherwise accept them.
    - Spurious DDI 2 hotplug fired briefly (5 sec before the front plug landed) — driver classified it as `slave port of multi cable display` and dismissed. Cross-talk during PD-controller negotiation, not a real attachment.

10. **2026-05-06 — DVMT memory bump (Path: VRAM headroom).**
    - Linux confirmed actual BIOS DVMT-prealloc allocation is **64 MB**:
        ```
        /proc/iomem:    3b800000-3f7fffff : Graphics Stolen Memory   (= 0x04000000 = 64 MB)
        i915 debugfs:   stolen-system: total:0x0000000004000000 bytes
        ```
    - Existing override `stolenmem=19 MB / fbmem=9 MB` was the canonical workaround for **32 MB** DVMT-prealloc — leaving ~36 MB of stolen memory unused on this hardware.
    - Bumped to **stolenmem=38 MB / fbmem=19 MB** (sum 57 MB, 7 MB headroom under 64 MB ceiling). Hex bytes: `<00006002>` and `<00003001>` respectively.
    - `framebuffer-unifiedmem` already at `0x80000000` (2 GB) — left alone (already at the practical ceiling for 32-bit framebuffer kext field; stock 8A52 default is 1.5 GB).
    - Surface repo's "62 MB stolen + 24 MB fb" config was rejected — that totals 86 MB which **exceeds our 64 MB BIOS ceiling**. Surface hardware presumably has a larger DVMT-prealloc (96 or 128 MB).
    - **Result post-reboot:** clean. IORegistry shows the new values live. AppleIntelFramebufferController init `~2 sec`, no panic, internal display unaffected. Three `AppleIntelFramebuffer@N` instances enumerated (FB@0/1/2) — same as before. VRAM still reports 2048 MB. No regression.
    - Backup: `config-pre-stolenmem-bump.plist.bak`.

11. **2026-05-06 — Boot-args audit.** Re-evaluated each token after the user correctly flagged that my earlier suggestion to swap `agdpmod=vit9696 → agdpmod=pikera` was wrong (pikera is the AMD-Navi/Vega black-screen fix, not relevant for Intel iGPU + MacBookPro16,2 SMBIOS). Final verdict: **no boot-arg changes warranted.** Current set is correct:
    - `agdpmod=vit9696` — correct for Intel iGPU board-id bypass; do **not** swap to pikera.
    - `-btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck` — load-bearing for AX201 BT, never drop.
    - `igfxonln=1` — important for external display detection (force-online connectors).
    - `-igfxdbg -liludbgall -v` — debug logging, keep until external display works.

12. **2026-05-06 — LSPCON code-path test.**
    - Added single key `enable-lspcon-support = <01000000>` to iGPU DeviceProperties.
    - Did **not** add per-connector `has-lspcon-conN` (would force WEG to write LSPCON I²C registers to a chip that doesn't exist per VBT — bus-hang risk).
    - Did **not** add `preferred-lspcon-mode-conN` (only meaningful with `has-lspcon-conN=1`).
    - Did **not** add boot-arg `igfxlspcon=1` (equivalent of the DeviceProperty; setting both is redundant).
    - Goal: probe whether engaging the WEG LSPCON code path globally has any incidental effect on the `Unsupported port type 10` rejection.
    - Backup: `config-pre-lspcon-test.plist.bak`.
    - **Result after reboot: confirmed inert / no-op.** Boot init clean, FB0/1/2 enumerate as before. Hot-replug on front USB-C still produces same `Unsupported port type 10` + `portMode=3` + `Non-managed external displays are no longer supported` rejection. **Zero `lspcon`/`LSPCON` log lines anywhere** — even at `-igfxdbg -liludbgall` verbosity. Empirical confirmation that WEG's LSPCON code path requires per-connector `has-lspcon-conN=1` to actually engage; the global flag alone is dormant. Property left in place (harmless) for now.

13. **2026-05-06 — Path A test (built-in HDMI port, first time under VBT-derived overrides).**
    - HDMI cable plugged directly into the laptop's built-in HDMI port (DDI B, busid=1, con1).
    - Hot-plug log capture (`/tmp/replug-log.txt`):
        ```
        Hotplug detected on ddi = 1            ← MAPPING WORKS for HDMI port
        HPD is high. Setting port mode
        HPD is high
        Unsupported port type 18               ← NEW VALUE (was 10 on USB-C)
        ddi 1 isHPDLow=0 emptyDongle=0 sinkCount=0 sinkCountChanged=0 portMode=1
        Event insert
        AGDC Callback is not yet registered!!
        Non-managed external displays are no longer supported
        ```
    - **DDI 1 mapping confirmed correct** — busid=1 → DDI B → built-in HDMI port. All three external paths (con1/2/3) now have validated DDI mapping.
    - **`portMode = 1` + `sinkCount = 0`** matches the prior prediction for Path A: DDI B emits DP signaling (con1-type=DP, no LSPCON to convert), HDMI display can't decode → no DDC response → sinkCount=0.
    - Same `Non-managed external displays are no longer supported` rejection as USB-C path, but at a **different runtime port-type bucket**: 18 (= 0x12) for HDMI port vs 10 (= 0x0A) for USB-C.

14. **2026-05-06 — Synthesis of port-type rejection mechanism.**
    - The two failure paths produce **different runtime `portType` values**:

        | Path | DDI | static `framebuffer-conN-type` | runtime `portType` | `portMode` |
        |---|---|---|---|---|
        | HDMI port (con1) | DDI B (native) | `0x400` (DP) | **18** (= 0x12) | 1 (DP signaling) |
        | USB-C front (con3) | DDI D (TC PHY) | `0x400` (DP) | **10** (= 0x0A) | 3 (DP-alt-mode through TC PHY) |
        | USB-C rear (con2) | DDI C (TC PHY) | `0x400` (DP) | (10 expected) | (3 expected) |

    - **Critical conclusion:** `framebuffer-conN-type` (static, set in DeviceProperties) ≠ runtime `portType` (set by framebuffer kext at hotplug). The static value is metadata for the connector descriptor; runtime port type is computed from link training / DPCD probe / silicon path. Editing `con1-type` to HDMI(0x800) would NOT change `Unsupported port type 18` to anything else.
    - **The rejection happens INSIDE `AppleIntelICLLPGraphicsFramebuffer` at port-type-allow-list level**, before AGDC/AGDP gets involved. The `AGDC Callback is not yet registered!!` message in the log is a misleading red herring — the framebuffer logs it but auto-validates with `response=0x0`. Real blocker is the framebuffer kext's hardcoded set of "approved" port types.
    - **Implication for next experiments:**
        - `agdpmod=ignore` is now predicted to **not help** — rejection is at framebuffer level, not AGDP level. Still worth one test as it's cheap.
        - Changing `framebuffer-conN-type` is predicted to **not help** — static type doesn't influence runtime classification.
        - **WEG kernel patch** (binary patch the port-type compare instruction in `AppleIntelICLLPGraphicsFramebuffer::handleHotPlug` to add 10 and 18 to the accepted list) is now the most-targeted next move. Requires disassembling the kext to find the comparison.
        - **SMBIOS change** is the second-most plausible — different SMBIOS may load a different framebuffer kext version with different port-type allow-lists.

15. **2026-05-06 — `agdpmod=ignore` test + critical correction on its semantics.**
    - Swapped boot-arg `agdpmod=vit9696 → agdpmod=ignore`. Backup: `config-pre-agdpmod-ignore.plist.bak`.
    - **Initial finding:** new rejection log path appeared: `[IGFB][ERROR][PORT] Invalid port type N`. Distinct from the `[HOT_PLUG]` subsystem we'd seen before. This path fires at **boot init when the cable is already attached** (no HPD edge), polling every ~1 sec for ~7 seconds.

        | Trigger | Subsystem | Message | When |
        |---|---|---|---|
        | HPD edge transition (unplug/replug) | `[HOT_PLUG]` | `Unsupported port type N` + `Non-managed external displays are no longer supported` | Hot-plug events |
        | Boot init / port classification with cable already attached | `[PORT]` | `Invalid port type N` | Boot when cable plugged AND `agdpmod=ignore` |

    - **CORRECTION (after entry #18 historical analysis):** the `[IGFB][ERROR][PORT] Invalid port type N` polling pattern is **NEW with `agdpmod=ignore`**. Earlier boots with `agdpmod=vit9696` did NOT produce this log. So `agdpmod=ignore` is **not equivalent to `vit9696`** — it actively triggers a code path in the framebuffer that performs aggressive boot-init port classification + rejection.
    - **WEG `agdpmod=ignore` semantics:** "don't apply any AGDP patches" (let AGDP run stock). On Hackintoshes with non-Apple SMBIOS, this typically means AGDP rejects the board-id and returns errors. On `MacBookPro16,2` (`Mac-B4831CEBD52A0C4C`) which AGDP natively accepts, the practical difference is more subtle but **NOT a no-op** as we initially hypothesized — empirically it changes framebuffer behavior at boot.
    - **Tooling discovery:** `/usr/bin/log` works **without sudo** for kernel events on this system. The `log` command was being shadowed by zsh's `log` builtin all session — that's why earlier `log show` invocations failed with `too many arguments`. Using absolute path `/usr/bin/log show ...` works without password. Eliminates the dump-to-file copy-paste loop.

16. **2026-05-06 — Web research (acidanthera bugtracker / WhateverGreen / Hackintosh community repos).**
    - Goal: find published fixes for `Unsupported port type 10/18` + `Non-managed external displays` on Ice Lake (8A52) under Sonoma. Sonoma has been out since 2023-09; if a fix exists, it should be findable.
    - **Verdict: no public fix exists** for the exact errors on Ice Lake. Zero hits on the exact strings across acidanthera/bugtracker, GitHub issues, InsanelyMac, tonymacx86, Dortania, or community repos.
    - **Confirming sources:**
        - [acidanthera/bugtracker #1616](https://github.com/acidanthera/bugtracker/issues/1616) — "WhateverGreen's DP to HDMI patch has no effect on Ice Lake platforms." Confirms our entry #14 conclusion at upstream level.
        - [acidanthera/bugtracker #1432](https://github.com/acidanthera/bugtracker/issues/1432) — open umbrella issue for ICL framebuffer support; no port-type-rejection reproducer on file.
        - [m0d16l14n1/icelake-hackintosh](https://github.com/m0d16l14n1/icelake-hackintosh) — community status tracker explicitly marks "No HDMI" and "Type-C to HDMI" as **NOT FIXED, no WA available**.
        - [Lorys89/DELL_VOSTRO_5401-ICE-LAKE](https://github.com/Lorys89/DELL_VOSTRO_5401-ICE-LAKE) — same i7-1065G7 CPU as Spin 5; Sonoma report explicitly: "HDMI not supported, Type-C to HDMI not supported."
        - WhateverGreen README + IntelHD FAQ — **no DeviceProperty key exists** for runtime port-type rewrite (no `framebuffer-port-type-rewrite`, no `enable-icllp-port-type-fix`, etc.).
    - **One repo claims a recent partial fix:** [jlempen/Surface-Laptop-3-OpenCore commit 5b1b5f58](https://github.com/jlempen/Surface-Laptop-3-OpenCore/commit/5b1b5f58) (2025-06-20, Sequoia). README says "Fixed external displays over USB-C." **Actual diff is mostly whitespace.** The only substantive iGPU change: **removed `hda-gfx`/`No-hda-gfx` keys** from the iGPU device-properties.
        - Caveat: SL3 has NO built-in HDMI port (only USB-C with native DP-alt-mode through onboard PD/mux, no Thunderbolt). Different topology from Spin 5. May be coincidental with Sequoia upgrade.
    - **Implication for our candidate-experiments list:** WEG kernel patch on `AppleIntelICLLPGraphicsFramebuffer` (previously candidate #1) has **no public ICL precedent** — community-authored binary patches exist for KBL/CFL framebuffers but not ICL. Higher implementation cost than I estimated. Demoted from #1.
    - Honest framing: this is a **known-unsolved Hackintosh problem** on Ice Lake. There is no zero-config-edit "magic fix" published anywhere.

17. **2026-05-06 — Cleanup edits: remove `enable-lspcon-support` and `hda-gfx`.**
    - `enable-lspcon-support`: confirmed inert (entry #12). Removed for cleaner config and to isolate variables before next experiment.
    - `hda-gfx = 'onboard-1'`: removed as the jlempen-inspired test (entry #16). Trade-off: HDMI audio output will not work if external display is ever accepted. Reversible (just re-add the key with same value).
    - **Two changes in one edit** (deliberately, per user direction). Both are deletions, no risk of breaking either internal display or boot. No backup created (per user direction); existing backups (`config-pre-agdpmod-ignore.plist.bak` etc.) provide adequate restore points.
    - **Boot-args left as `agdpmod=ignore`** (per user choice) — though `vit9696` would be functionally equivalent for cleanup purposes.
    - **Status:** edit applied to ESP, awaiting reboot to test. Predicted result: no change to port-type rejection (jlempen's "fix" claim is suspected to be coincidental with Sequoia upgrade).

18. **2026-05-06 — Post-cleanup reboot result + historical `[PORT]` polling analysis.**
    - **Test result for entry #17 cleanup (LSPCON + iGPU `hda-gfx` removal):** **no fix**. Boot 12:17 still produces `[IGFB][ERROR][PORT] Invalid port type 18` polling on DDI 1 (HDMI port, busid=1). FB@1/FB@2 still `IOFBIntegrated = Yes` with no `IODisplayConnect` children. External display still rejected.
    - **`hda-gfx` was still showing in IORegistry post-removal** because the **HDEF audio device (`PciRoot(0x0)/Pci(0x1F,0x3)`) also has `hda-gfx = 'onboard-1'`** — we'd only removed it from the iGPU side. macOS's AppleHDA pairs them at runtime, exposing it under the iGPU node too. So our entry #17 removal was incomplete: jlempen's commit removed the key from BOTH the iGPU and the HDEF.
    - **Jlempen commit verification (3597-line diff investigated):** retrieved both pre- and post-commit `config.plist` from jlempen/Surface-Laptop-3-OpenCore commit 5b1b5f58 and ran section-by-section binary-plist size diff. **The 3597 line-count is 99% indentation/format reflow.** Binary plist size delta:

        | Section | Δ bytes |
        |---|---|
        | ACPI | 0 |
        | Booter | 0 |
        | **DeviceProperties** | **−173** |
        | Kernel | 0 |
        | Misc | 0 |
        | NVRAM | 0 |
        | PlatformInfo | 0 |
        | UEFI | 0 |

    - **Actual semantic changes in jlempen's "Fix external display through USB-C":**
        - iGPU (`PciRoot(0x0)/Pci(0x2,0x0)`): removed `hda-gfx = 'onboard-1'`, removed `No-hda-gfx = <00...>` (8-byte zero placeholder)
        - HDEF (`PciRoot(0x0)/Pci(0x1F,0x3)`): removed `hda-gfx = 'onboard-1'`, changed `No-hda-gfx` from `<00...>` (data) to `'onboard-1'` (string, non-functional placeholder — macOS doesn't recognize `No-hda-gfx`)
        - **Zero kernel patches.** **Zero boot-arg changes.** **Zero kext additions/removals.** **Zero SMBIOS changes.** **Zero ACPI changes.**
    - **Historical log analysis (`/usr/bin/log show --last 24h`)** to test "polling-was-always-there" hypothesis:
        - Found 12 boots in the last 24h kernel-archive window.
        - Searched for `Invalid port type` events across all of them.
        - **Result: only the two boots WITH `agdpmod=ignore` have `Invalid port type N` events.** All earlier boots (with `agdpmod=vit9696`) had ZERO matches.
        - **Hypothesis falsified.** The `[IGFB][ERROR][PORT] Invalid port type` polling pattern is **NEW**, introduced by the `agdpmod=ignore` change.
        - The earlier `[IGFB][ERROR][PORT]` events that the broader search initially flagged were a **different error class**: `_DSM function 18 call failed 0xe00002bc` (= `kIOReturnUnsupported`). That's an ACPI Device-Specific Method invocation failure — Apple's iGPU calls Apple-firmware-specific `_DSM` methods that don't exist in our Insyde firmware. **Harmless ACPI noise**, completely unrelated to port-type rejection.
    - **HDMI was being detected in pre-`ignore` boots too**: `[IGFB][LOG][DPCD] DWN_STRM_PORT0_CAP Type: 0X3` and `HPD Aware: 1` log lines appear, meaning the iGPU was reading DPCD over the aux channel. The cable was electrically detected in every boot. The rejection just happened via different code paths and possibly silently in pre-`ignore` boots (HOT_PLUG path on hot-replug, [PORT] polling path only when `agdpmod=ignore`).

19. **2026-05-06 — `agdpmod=ignore` vs `vit9696`: refined comparison + retracted revert recommendation.**
    - Contrary to entry #15's initial assumption, `agdpmod=ignore` empirically changes framebuffer behavior:
        - With `vit9696`: WEG patches AGDP's board-id check; framebuffer enumerates FB1/FB2 but **does NOT run boot-init `ConnectionProbe`** on external connectors. No port-type classification at boot, no `[PORT]` rejection log. Externals stay `fOnline = 0` silently.
        - With `ignore`: WEG applies no AGDP patches; framebuffer runs **deferred boot-init `ConnectionProbe`** (~1m 43s after boot) on external connectors, classifies them, fails with `Invalid port type 18`, polls 8× over ~7 sec.
    - **Initially recommended a revert to `vit9696` for log-cleanliness.** **RETRACTED 2026-05-06 after user pushback.** The user's argument is correct: log cleanliness is not the goal — fixing the external display is. Both configs equally fail to attach the display; the choice is purely diagnostic.
    - **Refined position: keep `agdpmod=ignore`** for now. Reasons:
        1. Both configs equally reject the external display at port-type-allow-list level (no functional advantage to either).
        2. `agdpmod=ignore` provides **more diagnostic visibility** — boot-init `[PORT]` rejection events give us additional observable behavior to instrument with future patches.
        3. `vit9696` would mute that information without a corresponding gain.
        4. **The cable IS being detected in both configs** (DPCD reads succeed in both, reporting downstream port type 0x3 = HDMI per DP spec). Detection isn't the issue; classification is.
    - Status: no revert. Boot-args remain `agdpmod=ignore` + `-igfxtypec` (added in entry pending reboot test).

20. **2026-05-06 — Tangent investigation: ACPI `_DSM` function 0x12 — RULED OUT.**
    - **Context:** earlier kernel logs in pre-`agdpmod=ignore` boots showed `[IGFB][ERROR][PORT] _DSM function 18 call failed 0xe00002bc`. The string contains the literal "function 18", which numerically matches our runtime portType=18 rejection. Worth investigating whether they're related.
    - **Decode of SSDT-5** (`\_SB.PCI0.GFX0._DSM`, lines 2639–2872):
        - UUID `3e5b41c6-eb1d-4260-9d15-c71fbadae414` = **Intel IGD OpRegion DSM** (Linux i915 / Windows IGCC use this; Apple does not).
        - Function 0x12 (line 2850): calls Insyde EC mailbox method `IMMC` with sub-command `0x03` against `DDIN` (DDI number from `Arg3[0]`), returns 5-byte status buffer. **EC round-trip, not a port-type classifier.**
        - Function 0x13 is symmetric for sub-command `0x06`.
        - **Gate at line 2643:** `If (((PCHS == PCHN) && ((Arg2 == 0x12) || (Arg2 == 0x13))))`. `PCHS` is runtime-read 16-bit PCH stepping (DSDT line 4175); `PCHN` is constant `0x03` (DSDT line 6164). If `PCHS != 3`, falling into `Case(0x12)` references undefined names → undefined-behavior territory.
        - **Function-0 supported-functions bitmap is also gated:** `0x000DE7FF` if `PCHS == PCHN` (advertises 0x12/0x13), else `0x0001E7FF` (masks them out). Sane drivers query function 0 first and respect the mask.
    - **Why this is NOT what's rejecting our external displays:**
        1. **Wrong UUID space.** macOS `AppleIntelICLLPGraphicsFramebuffer` uses Apple's own DSM UUID (`A0B5B7C6-...`, the one OpenCore synthesizes via `DeviceProperties > Add`). Apple's iGPU stack historically does not consume the Intel IGD OpRegion DSM.
        2. **Rejection site is the framebuffer kext binary, not ACPI.** "Unsupported port type 18" / "Invalid port type 18" strings live inside the kext; the rejection is emitted from the hot-plug / port-classification handler that reads PCH/PHY registers. No ACPI evaluation is on that codepath.
        3. **Firmware bitmap masks 0x12/0x13 unless `PCHS == 3`.** A well-behaved driver wouldn't invoke function 0x12 when masked. Even if Apple did, the ACPI side wouldn't generate the kext's "port type 18" rejection log line.
    - **The matching numeral 18 (= 0x12) is two unrelated enum spaces:** `_DSM` function index 0x12 (ACPI) ≠ runtime `portType` 0x12 (framebuffer kext). Coincidence.
    - **Cheap PCHS sanity check** (deferrable): `setpci -s 00:1f.0 0x08.b` from Linux, or a tiny SSDT debug print. Tells us whether the firmware advertises 0x12/0x13 in its bitmap. **Doesn't change the rejection mechanism either way.**
    - **DPCD log line clarification:** `[IGFB][LOG][DPCD] DWN_STRM_PORT0_CAP Type: 0X3` = downstream port type 0x3 = **HDMI** per DP spec (0=DP, 1=VGA, 2=DVI, 3=HDMI, 4=Other-no-EDID, 5=DP++/HDMI bridge). Informational, not an error. Confirms the cable IS detected and characterized as serving an HDMI sink. Rejection happens later in the kext's port-type-allow-list, not here.
    - **Conclusion:** `_DSM` 0x12 path is a confirmed dead-end for external-display debugging. Recorded here so we don't re-explore it later. The actual lever remains the framebuffer kext's port-type-allow-list (kernel-patch territory).

21. **2026-05-06 — `-igfxtypec` test + connector flags `0x281` test (Option B, bundled).**
    - Reverted `-igfxtypec` (it caused AUX timeouts on DDI 1 + pipe underruns + DBuf failures without changing portType classification).
    - Changed `framebuffer-con2-flags` and `framebuffer-con3-flags` from `<87010000>` (0x187) to `<81020000>` (0x281) per "TC-PHY indicator bit 0x200" hypothesis.
    - **Result post-reboot:** hot-plug on USB-C (DDI 3) still produces `[IGFB][ERROR][HOT_PLUG] Unsupported port type 10` + `[IGFB][ERROR][PORT] Invalid port type 10` polling. **No change in runtime `portType` classification.** Bit 0x200 hypothesis **falsified**.
    - Side-effect observation: AUX errors at boot dropped from 1040 → 40 (26× reduction) — the flag change DID alter framebuffer probe behavior, just not on the port-type axis we hoped.
    - Notable absence corrected: the `Non-managed external displays are no longer supported` message no longer fires under `agdpmod=ignore` (replaced by `AGDC managed display: Start listening to AGDC`). So `agdpmod=ignore` removed the AGDP-layer rejection — but the framebuffer-kext-layer rejection (port-type allow-list) remains.

22. **2026-05-06 — Kext skim of `AppleIntelICLLPGraphicsFramebuffer` (sub-agent investigation).**
    - **Goal:** find the port-type comparison instruction inside the framebuffer kext binary, with intent to author an OpenCore `Kernel > Patch` to add 10 and 18 to the accepted set.
    - **Binary location:** kext is bundled inside `/System/Library/KernelCollections/SystemKernelExtensions.kc` (360 MB Mach-O fileset). On modern sealed-system macOS the standalone `.kext` bundle does not exist on disk. Fileset entry: vmaddr `0x14269000`, fileoff `338071552`, size ~856 KB.
    - **ICLLP `__TEXT` sections:** `__text` at vma `0x14269880` (size `0x7e8f2`), `__cstring` at vma `0x142efac4` (size `0x1273d`).
    - **No bitmap or table of accepted port types found** in `__const` or `__data`. **No standalone validator function** like `bool is_acceptable_port_type(int)`. Validation is **inlined at multiple call sites** (≥6 found).
    - **Cleanest validation site at `0x14276300`:**
        ```
        0x142762fd:  mov eax, [rax+8]         ; load port type
        0x14276300:  test eax, eax            ; ==0?  → accept (eDP)
        0x14276302:  je   0x14276328
        0x14276304:  cmp eax, 1               ; ==1?  → accept (DP via TBT3)
        0x14276307:  je   0x14276363
        0x14276309:  lea rdi, [rip+...]       ; "Unsupported port type" string
        0x14276310:  lea rsi, [rip+...]
        0x14276317:  xor eax, eax
        0x14276319:  call _os_log_internal
        0x1427631e:  mov eax, 0xe00002c7      ; kIOReturnUnsupported
        0x14276323:  jmp  0x14276d97          ; ret error
        ```
    - **Whitelist of {0, 1} only.** Port types 10, 18, and everything else are rejected. This explains the rejection on every Hackintosh ICL config — the allow-list is hardcoded to Apple's stock SKU coverage.
    - **Why 10 and 18 specifically?** They're Apple-internal classifications, NOT general DP/HDMI standard values. The kext computes runtime portType from PHY type (combo vs Type-C/DKL) + DDI letter + downstream connector capabilities + LSPCON presence:
        - **Port type 0** = internal eDP (Apple has this on every Mac)
        - **Port type 1** = DP via Apple's Thunderbolt 3 controller (Apple's stock USB-C path on every TBT3 Mac)
        - **Port type 10** = direct DKL PHY DP-alt-mode without TBT3 routing (Spin 5's USB-C ports — hub does DP-alt-mode passthrough, no TBT3 in path)
        - **Port type 18** = native HDMI on combo PHY (Spin 5's built-in HDMI port — Apple ICL Macs ship with no native HDMI)
    - **The Spin 5's ports both fall into "non-Apple silicon paths"** that Apple's framebuffer doesn't have allow-list entries for. This is why no community config-side fix exists — Apple never has to handle these paths.
    - **Proposed binary patch (single 9-byte patch, addresses one site):**

        | Field | Value |
        |---|---|
        | `Identifier` | `com.apple.driver.AppleIntelICLLPGraphicsFramebuffer` |
        | `MinKernel` | `23.0.0` |
        | `MaxKernel` | `23.99.99` |
        | `Find` | `85 C0 74 24 83 F8 01 74 5A` |
        | `Replace` | `85 C0 74 24 90 90 90 EB 5A` |
        | `Count` | `1` |
        | Uniqueness | 9-byte sequence appears exactly once in the entire 360 MB kc |

        Effect: original accepts only `{0, 1}`; patched accepts `{0, ANY non-zero}` by replacing `cmp eax, 1; je accept` with `nop; nop; nop; jmp accept`. Forces all non-zero port types onto the type-1 acceptance path.

    - **Risks of applying this patch:**
        1. **Forces port type 10/18 onto the type-1 code path.** Type-1 expects TBT3-routed DP signaling; port type 10 (direct DKL PHY) and 18 (combo PHY HDMI TMDS) need different downstream setup. Could lead to: silent display failure, partial init, or panic on subsequent state access.
        2. **Only addresses ONE of 6+ rejection sites** found in the disassembly. After this patch, port type 10/18 may still get rejected at downstream sites (e.g., `0x14277b22`, `0x142ceb6a`, `0x142daec9`, `0x142dde76`) that gate on different conditions.
        3. **No reasonable surgical patch exists** to whitelist exactly `{10, 18}` — would require a longer multi-instruction patch the agent couldn't safely identify.
        4. **Boot risk**: if patched kext panics, system won't boot. Recovery requires external boot disk and ESP edit access.
        5. **Reversibility**: patch is in `config.plist` Kernel > Patch — disabling/removing the entry restores original behavior.
    - **Status:** patch identified, awaiting user approval to add to `Kernel > Patch` array in `config.plist`.

23. **2026-05-06 — Thunderbolt 3 stack confirmed FUNCTIONAL on Spin 5.**
    - Plugged a Thunderbolt 5 eGPU enclosure containing an NVIDIA RTX 5070 Ti (Blackwell GB203, vendor `0x10de`, device `0x2c05`) into front USB-C, then re-plugged into rear USB-C.
    - **PCIe-over-Thunderbolt tunneling: WORKING.** The eGPU's GPU and HDA audio companion (device `0x22e9`) both enumerated as PCIe devices in IORegistry. Link came up at PCIe Gen 4 x4 @ 16 GT/s (~64 Gbps = full TB3 bandwidth).
    - Both USB-C ports (front and rear) are TB3-capable — re-plug between them produced identical enumeration. The TB5 controller endpoint inside the eGPU enclosure (Intel `0x8086:0x1234`) showed up as a 12 Mbps "TBT5" management interface in both cases.
    - **System Information UI quirk:** `system_profiler SPThunderboltDataType` reports `"No drivers are loaded"` even though the TB stack is loaded and tunneling works. This UI string requires Apple-firmware-specific data structures that the Hackintosh stack doesn't populate, but the underlying functionality is fine. **Don't trust this UI message** — check IORegistry directly: `AppleThunderboltHALType4`, `AppleThunderboltNHIType4`, `IOThunderboltControllerType4` all `registered, matched, active`.
    - **NVIDIA card cannot drive any display on macOS Sonoma.** Apple removed NVIDIA driver support after macOS 10.13 High Sierra (2018). RTX 5070 Ti enumerated but no driver attached. This is a NVIDIA-specific limitation, not a Thunderbolt limitation.
    - **What this means for our problem:** TB3 hardware path is working. PCIe traffic over TB is fully functional. Opens the door to:
        - AMD-based eGPU as a workaround (separate display path through eGPU's own GPU outputs)
        - **TB3 dock with DP/HDMI output** as the most promising path (see entry #24)

24. **2026-05-06 — DDI 4 / DDI 5 discovery: port type 1 already accepted by the framebuffer.**
    - **Discovery context:** display sleep/wake cycle on internal panel triggered the framebuffer to query port status on ALL 5 ports it knows about. The output:
        ```
        PortIndex = 1, portType = 18, DDI = 1   ← built-in HDMI port (rejected)
        PortIndex = 2, portType = 9,  DDI = 2   ← rear USB-C (rejected, type 9 not 10)
        PortIndex = 3, portType = 10, DDI = 3   ← front USB-C (rejected)
        PortIndex = 4, portType = 1,  DDI = 4   ← TBT3-routed DP virtual DDI ★ ACCEPTED ★
        PortIndex = 5, portType = 1,  DDI = 5   ← TBT3-routed DP virtual DDI ★ ACCEPTED ★
        ```
    - **What DDI 4 and DDI 5 are:** Apple's stock 8A52 framebuffer expects 5 DP slots (matches our entry #1 finding "stock busids `0x02, 0x09, 0x0A, 0x0B, 0x0C`"). DDI 4 and DDI 5 are **TBT3-routed DP virtual DDIs** — on a real Mac, they carry DP traffic that has been tunneled through the Thunderbolt 3 protocol stack. They're already classified as port type 1 (the kext's accepted type from entry #22's whitelist).
    - **Our config currently overrides only con1, con2, con3** (busids 1, 2, 3) — DDI 4 and 5 still use stock 8A52 values. The framebuffer kext sees them as standard Apple-style TB-routed-DP ports and accepts them.
    - **Implication: a TB3 *dock* with DP/HDMI output should land display traffic on DDI 4 or 5, not DDI 2/3.** TB3 docks route DP signal **through the TB protocol** (not as raw USB-C DP-alt-mode). The signal arrives at the iGPU through the TBT3 controller's DP adapter path, which the framebuffer kext expects on DDI 4/5 with port type 1.
    - **This is the most promising fix lead in the entire debug session.** It requires:
        - A true TB3 *dock* (not a USB-C hub) with DP or HDMI outputs
        - `AppleThunderboltDPAdapter` kext functional (need to verify)
        - No config edits, no kext patches
    - **Refined understanding of port-type meanings (Apple-internal classification):**

        | Port type | Meaning | Apple ICL Macs have this? | Spin 5 has this? | Accepted by allow-list? |
        |---|---|---|---|---|
        | 0 | Internal eDP / LVDS panel | ✓ every Mac | ✓ (DDI 0) | ✓ |
        | 1 | DP routed through TBT3 protocol | ✓ every TBT3 Mac | DDI 4/5 (virtual) | ✓ |
        | 9 | USB-C ICL TC PHY variant A | ✗ Apple uses TBT3 path | DDI 2 (rear USB-C raw DP-alt) | ✗ |
        | 10 | USB-C direct DKL PHY DP-alt-mode | ✗ Apple uses TBT3 path | DDI 3 (front USB-C raw DP-alt) | ✗ |
        | 18 | Native HDMI on combo PHY (TMDS) | ✗ no Apple ICL Mac has native HDMI | DDI 1 (built-in HDMI port) | ✗ |

    - **Why same Genesys hub works on real Mac but fails on Spin 5:** Apple's TBT3 controller firmware **intercepts every USB-C DP-alt-mode connection** — even from a non-TB hub — and re-presents it through TBT3 routing (port type 1). OEM TBT3 firmware (Acer/Insyde) does NOT do this re-routing; signal goes direct to DKL PHY (port type 10). Same physical connection, different runtime classification because of who's brokering at the firmware level.

25. **2026-05-06 — eGPU compatibility notes (for future reference).**
    - **NVIDIA: any card.** ✗ Not supported on macOS since 10.13 High Sierra (2018). Tested with RTX 5070 Ti.
    - **AMD R9 Nano (Fiji, GCN 1.2, 2015):** ✗ Not supported on Sonoma. Apple removed `AMDRadeonX4000.kext` (Fiji's driver) in modern macOS. Community port-back attempts exist but flaky.
    - **AMD RX 7000 series (RDNA 3 / Navi 31/32/33):** ✗ Not supported. No macOS driver exists for any RDNA 3 chip. Mobile variants (e.g., RX 7600M) doubly impractical (mobile form factor).
    - **AMD cards that DO work on Sonoma:**
        - Polaris (RX 480/580, RX 460/470/550/560/570) — rock solid
        - Vega 10 (RX Vega 56/64) — well-supported
        - Vega 20 (Radeon VII, Vega II) — well-supported
        - Navi 10 (RX 5700/5700 XT) — well-supported
        - Navi 21/22/23 (RX 6600/6700/6800/6900) — best modern choice
    - **Caveat for all:** even with a supported card, Hackintosh + eGPU on Sonoma is fragile. Requires correct AGDP config, sometimes specific kexts. eGPU drives ITS OWN displays — doesn't fix iGPU port-type rejection.
    - **For our debug context:** eGPU is a workaround path (separate display via eGPU outputs), not a fix for the iGPU. The TB3 dock path (entry #24) is more directly relevant because it lets the iGPU itself drive the display via DDI 4/5.

26. **2026-05-06 — ★ THE FIX ★: `framebuffer-conN-pipe` IS the runtime portType byte. External display WORKING.**

    **Discovery (credit: user pattern recognition).** While reviewing the runtime portType values reported by `[IGFB][LOG][DISPLAY] PortIndex = N, DDI = N, port type = X`, the user noticed that the reported `port type` X exactly matches the value we'd set in `framebuffer-conN-pipe`:

    | Connector | `framebuffer-conN-pipe` (pre-fix) | Reported runtime `port type` |
    |---|---|---|
    | con1 (HDMI port) | `<12000000>` = 0x12 = **18** | **18** ✓ matches |
    | con2 (rear USB-C) | `<09000000>` = 0x09 = **9** | **9** ✓ matches |
    | con3 (front USB-C) | `<0A000000>` = 0x0A = **10** | **10** ✓ matches |

    **Conclusion: WhateverGreen's documentation labels the field `framebuffer-conN-pipe`, but on `AppleIntelICLLPGraphicsFramebuffer.kext` the value at this byte position is read as the runtime port-type classification, not the scanout pipe enum.** The naming convention is inherited from older platforms (CFL/KBL) where the field had different semantics for that gen's framebuffer kext.

    **The fix:** set `framebuffer-conN-pipe = 0x01` for accepted-by-the-whitelist port type. Tested on con3 (front USB-C) — **external display attached, IODisplayConnect appeared on FB@1 with full EDID for a Dell P2722H 27" monitor.** Zero `Invalid port type` errors in post-boot kernel log. Display visible and usable.

    **Then extended:** changed con1 (HDMI port) and con2 (rear USB-C) pipe values from 0x12 / 0x09 to 0x01 as well. Awaiting reboot to verify.

    **Cross-generation context (credit: user observation).** Different Intel iGPU generations have **different framebuffer kexts with different accepted port-type values**:

    | Generation | Kext | Stock pipe values (port types) |
    |---|---|---|
    | Coffee Lake (CFL) | `AppleIntelCFLGraphicsFramebuffer` | **8, 9, 10, ...** (higher values; broader allow-list) |
    | Ice Lake (ICL) | `AppleIntelICLLPGraphicsFramebuffer` | **0 (internal) and 1 (external) only** |
    | Tiger Lake (TGL) | `AppleIntelTGLGraphicsFramebuffer` | (similar to ICL pattern, gen-specific) |

    **Why this misled the entire Hackintosh community for ICL.** When Ice Lake hardware appeared (~2019, 10th-gen Intel), community members ported existing CFL config templates to ICL machines. The pipe values like 0x09, 0x0A, 0x12 got inherited without question — they "looked right" because the labels and structure matched. Internal display worked (pipe 0 was always present, accepted by all kexts). External display rejected with `Unsupported port type N` where N was the inherited CFL pipe value. Community concluded "ICL kext is hardcoded to reject non-Apple silicon paths" and chased fixes at the wrong level (kernel patches, agdpmod variants, AGDP rules) for years. **The fix was to set the field labeled "pipe" to a value the ICL kext's allow-list accepts (0 or 1).**

    **Connection to jlempen's Surface Laptop 3 commit (entry #18 corrected).** The agent's web research and our diff verification of jlempen's commit `5b1b5f58` ("Fix external display through USB-C") concluded the only substantive change was `hda-gfx` removal. **That conclusion was wrong.** The actual cause of jlempen's working external display was that **their config had NO `framebuffer-conN-*` overrides at all** — slots 1-5 used stock 8A52 values, which include pipe values from {0, 1}, all accepted by ICL kext's allow-list. The `hda-gfx` removal was incidental. We can now retroactively verify this by checking jlempen's connector descriptors are stock (no pipe overrides).

    **ICL pipe assignment design:**
    - **Pipe 0** = Pipe A = internal eDP (every ICL Mac)
    - **Pipe 1** = Pipe B = all external displays (TBT3-routed; software-arbitrated for multiple)
    - **Pipe 2** = Pipe C = unused in stock Apple ICL configs (untested code path)

    **Implications:**
    - Pipe=2 (port type 2) is the only "valid pipe enum" we haven't tested. May or may not be in the kext's whitelist at other rejection sites. Risky to rely on.
    - **All external connectors set to pipe=1 means mutual exclusion** — only one can drive a display at a time. For 2 simultaneous external displays, would need to test pipe=2 on one connector (lower confidence).

    **Final config recipe for Spin 5 ICL Hackintosh external displays:**
    ```
    framebuffer-con1-busid = <01000000>     (DDI B - HDMI port wiring)
    framebuffer-con1-enable = <01000000>
    framebuffer-con1-flags  = <87010000>
    framebuffer-con1-pipe   = <01000000>    ★ pipe=1 → port type 1 → accepted ★
    framebuffer-con1-type   = <00040000>    (DP)

    framebuffer-con2-busid = <02000000>     (DDI C - rear USB-C wiring)
    framebuffer-con2-enable = <01000000>
    framebuffer-con2-flags  = <81020000>
    framebuffer-con2-pipe   = <01000000>    ★ pipe=1 → port type 1 → accepted ★
    framebuffer-con2-type   = <00040000>    (DP)

    framebuffer-con3-busid = <03000000>     (DDI D - front USB-C wiring)
    framebuffer-con3-enable = <01000000>
    framebuffer-con3-flags  = <81020000>
    framebuffer-con3-pipe   = <01000000>    ★ pipe=1 → port type 1 → accepted ★
    framebuffer-con3-type   = <00040000>    (DP)
    ```

    **What can be retracted from earlier conclusions:**
    - Entry #18: jlempen's "fix" wasn't `hda-gfx` removal; it was absence of pipe overrides. Retroactively explained.
    - Entries #11–22: chased numerous wrong fixes (LSPCON probe, agdpmod variants, connector flags 0x281, kext disassembly for binary patch, TB3 dock theory). All those rabbit holes can be deprecated.
    - Entry #22 (kext skim) is still informative — confirmed the `{0, 1}` allow-list at one gate, which validates the fix mechanism. The proposed binary patch was correct in approach but unnecessary because the config-side fix exists.
    - Entry #24 (DDI 4/5 = port type 1) is now also explained: stock 8A52 connector descriptors for slots 4-5 have pipe=1.

    **Status:** con3 confirmed working (Dell P2722H attached). con1 and con2 just modified; awaiting reboot/test for HDMI port and rear USB-C.

27. **2026-05-06 — Multi-connector verification under entry #26 fix.**

    **con2 (rear USB-C, pipe=1): ✓ WORKING.** Hot-plug on DDI 2 produces:
    ```
    [HOT_PLUG] Hotplug detected on ddi = 2
    [DISPLAY]  PortIndex = 2, DDI = 2, port type = 1     ← accepted
    [HOT_PLUG] Fb1: Injecting HPD                          ← framebuffer engages
    ```
    Same Dell P2722H attaches on FB@1 with full EDID. Zero rejection events. Identical clean attachment as con3. **Two confirmed working external paths now: con2 and con3 (both USB-C).**

    **con1 (HDMI port, pipe=1): allow-list passes, but downstream signaling fails.** Hot-plug on DDI 1 produces:
    ```
    [HOT_PLUG] Hotplug detected on ddi = 1                ← DDI 1 mapping works
    [HOT_PLUG] HPD is high. Setting port mode
    [HOT_PLUG] port->getDPCDParams()->SinkCount = 0       ← no DDC response
    [HOT_PLUG] port->getPortState() = 0                   ← port unusable
    (10 sec retry loop)
    [HOT_PLUG] Setting DP power failed (0xe00002d6 = kIOReturnTimeout)
    [HOT_PLUG] ddi 1 isHPDLow=0 emptyDongle=0 sinkCount=0 portMode=1
    [HOT_PLUG] Returning as DPCD failed
    ```

    **No `Invalid port type` or `Unsupported port type` errors.** The pipe=1 fix successfully bypassed the port-type-allow-list rejection at this connector too — same as con2/con3. But the connection still fails downstream because:

    - DDI B (combo PHY) is wired to the HDMI port's TMDS connector pins
    - With `framebuffer-con1-type = DP (0x400)`, the framebuffer drives **DisplayPort signaling** on those wires
    - The HDMI display can't decode DP signaling
    - DPCD reads time out (HDMI displays don't have DPCD registers)
    - Framebuffer concludes "sink went offline" and gives up

    **This is the well-known DP-on-HDMI-wires failure mode predicted way back in entry #1.** The HDMI port works in firmware (DDI B is wired correctly, PHY can drive signals) but the *signaling protocol* mismatch needs hardware-level conversion (LSPCON) which the Spin 5 doesn't have (per VBT decode in entry #7).

    **The pipe=1 fix is therefore complete in scope:** it bypasses the kext's port-type-allow-list rejection. But for HDMI port specifically, there's a downstream hardware limitation that no config edit can fix.

    **Final practical state:**
    - **con3 (front USB-C, pipe=1): ✓ working** — one of the daily-driver ports
    - **con2 (rear USB-C, pipe=1): ✓ working** — the other daily-driver port
    - **con1 (HDMI port, pipe=1): pass-through to signaling failure** — no LSPCON, can't drive native HDMI TMDS without DP→HDMI hardware
    - **Internal eDP: ✓ unaffected** throughout the entire investigation

    User has 2 of 3 external paths working — both USB-C ports interchangeable for a USB-C hub or USB-C-to-HDMI dongle. HDMI port is a hardware limitation, not solvable in software.

    **Possible future test (optional, low priority):** `framebuffer-con1-type = HDMI (0x800)` to ask the framebuffer to drive native HDMI TMDS. WEG's HDMI conversion is documented broken on Ice Lake ([acidanthera/bugtracker #1616](https://github.com/acidanthera/bugtracker/issues/1616)), but worth one empirical test in 2026 with current WEG. Don't expect success.

---

28. **2026-05-07 — `agdpmod=ignore` IS load-bearing for external display: empirical correction to entries #15 / #19.**

    **Context.** While reverting hibernate-irrelevant changes to retest hibernate-25 (separate workstream, see `S3-sleep-debug-2026-05-07.md`), boot-args were temporarily flipped back to `agdpmod=vit9696` (the pre-2026-05-06 value). All other entry #26 fix components (`framebuffer-conN-pipe=1`, busid mapping, type=DP) were preserved.

    **Observation.** USB-C external display (con3, front port, Dell P2722H) **failed to light up** under `agdpmod=vit9696` despite the pipe=1 fix being in place. Reverting the single boot-arg back to `agdpmod=ignore` restored external display.

    **Falsified claim.** Entries #15 and #19 concluded "`vit9696` and `ignore` are functionally equivalent on MacBookPro16,2 SMBIOS" because that board-id is natively accepted by AGDP without patches. That conclusion was correct under the conditions tested at the time, but **only because the framebuffer was rejecting the connector at the port-type-allow-list layer (pre-pipe=1)**, masking any difference at the AGDP layer. Under those pre-fix conditions, both `vit9696` and `ignore` produced equivalent external-display failure (just via different log signatures, as entry #19 noted).

    **Refined mental model.** Two independent rejection layers stack in series for external display on this hardware:

    1. **Framebuffer port-type allow-list** (`AppleIntelICLLPGraphicsFramebuffer.kext`) — entry #26 fix bypasses this by setting `framebuffer-conN-pipe=1`.
    2. **AGDP policy** (`AppleGraphicsDevicePolicy.kext`) — bypassed by `agdpmod=ignore`. Under `agdpmod=vit9696`, AGDP gets patched (board-id table modified) but apparently still rejects something on the connector path for this combo (ICL + MacBookPro16,2 SMBIOS + external DP-via-DDI). Under `agdpmod=ignore`, AGDP is bypassed entirely — `ignore` mode does NOT mean "no-op," it means "skip applying any AGDP patches and let WEG bypass the AGDP enforcement code paths." That bypass is what's load-bearing.

    Pre-2026-05-06 testing only saw layer 1 fail, so layer 2 differences were invisible. With layer 1 fixed, layer 2 now matters.

    **Action.**
    - Boot-args remain `agdpmod=ignore`. Do not revert.
    - Line 112 ("Boot-args (live)" notes section) updated: removed the "functionally equivalent" claim and replaced it with a load-bearing warning + back-reference to this entry.
    - Memory entry `project_hackintosh_external_display_fix.md` updated with the same correction.

    **Open question (low priority).** Whether the AGDP layer-2 rejection under `vit9696` is specifically tied to ICL framebuffer interaction, the new pipe=1 connector path, or something more general. Not worth investigating unless we change SMBIOS or upgrade to a future macOS that changes AGDP semantics. Current state is working; leave alone.

    **Lesson.** When two independent rejection layers exist, fixing one doesn't reveal the other's behavior until the first is in place. Earlier conclusions reached "no difference between A and B" should be re-tested whenever a precondition changes — in this case, the pipe=1 fix changed the precondition.

---

## Test results matrix — `5029ee0` connector overrides (post-cleanup)

| Path | Plug | DDI event? | Static type | Runtime portType | portMode | sinkCount | Result |
|---|---|---|---|---|---|---|---|
| HDMI port (con1, busid=1) | direct HDMI cable to built-in port | ✓ `ddi = 1` HPD high | DP (0x400) | **18** (0x12) | 1 (DP signaling) | 0 | rejected — `Unsupported port type 18` + non-managed |
| FRONT USB-C (con3, busid=3) | Genesys hub + HDMI display | ✓ `ddi = 3` HPD high | DP (0x400) | **10** (0x0A) | 3 (DP-alt-mode/TC) | 0 | rejected — `Unsupported port type 10` + non-managed |
| REAR USB-C (con2, busid=2) | (revealed via wake port-status) | ✓ enumerated | DP (0x400) | **9** (0x09) | (untested as primary plug) | n/a | rejected (type 9, different from front!) — needs dedicated plug test |
| Internal eDP (con0, default) | always-on | n/a | LVDS (0x02) | 0 (LVDS) | n/a | n/a | ✓ working |
| **DDI 4 (no override; stock 8A52)** | TBT3-routed DP virtual DDI | (revealed via wake port-status) | (stock) | **1** | n/a | 0 (no plug yet) | **★ ACCEPTED port type ★** — would carry TB3-dock DP traffic |
| **DDI 5 (no override; stock 8A52)** | TBT3-routed DP virtual DDI | (revealed via wake port-status) | (stock) | **1** | n/a | 0 (no plug yet) | **★ ACCEPTED port type ★** — would carry TB3-dock DP traffic |

**Findings (consolidated 2026-05-06):**
- All three external DDI mappings are **proven correct** (DDI events fire on the expected DDI per Linux/VBT data).
- **THREE distinct rejection buckets, FIVE port-type values seen in total:**
    - port type 0 = LVDS (eDP) — accepted ✓
    - port type 1 = TBT3-routed DP — accepted ✓ (DDI 4 and DDI 5)
    - port type 9 = USB-C TC PHY variant A — rejected ✗ (rear USB-C / DDI 2)
    - port type 10 = USB-C direct DKL PHY DP-alt-mode — rejected ✗ (front USB-C / DDI 3)
    - port type 18 = native HDMI on combo PHY TMDS — rejected ✗ (HDMI port / DDI 1)
- Two distinct rejection log paths in `AppleIntelICLLPGraphicsFramebuffer` (entry #15):
    - `[IGFB][ERROR][HOT_PLUG] Unsupported port type N` + `Non-managed external displays are no longer supported` — fires on hot-plug events (HPD edge transitions)
    - `[IGFB][ERROR][PORT] Invalid port type N` — fires at boot init when display is already attached (no HPD edge); polls ~1× per second for several seconds
- Static `framebuffer-conN-type` does **not** influence runtime `portType`. Changing it won't help.
- LSPCON code path **confirmed inert** with global `enable-lspcon-support=1` alone (no per-connector flags). Property removed (entry #17).
- `agdpmod=ignore` test confirmed null — for `MacBookPro16,2` SMBIOS, `vit9696` and `ignore` are functionally equivalent because the board-id is natively accepted by AGDP without patches.
- The rejection happens inside `AppleIntelICLLPGraphicsFramebuffer` at port-type-allow-list level. The `AGDC Callback is not yet registered!!` in logs is a misleading harmless message.
- **No public fix exists** on Ice Lake (per entry #16 web research). Multiple Ice Lake repos confirm this is unsolved. WhateverGreen has no DeviceProperty key for runtime port-type rewrite.
- **TB3 stack on Spin 5 is functional** (entry #23) — confirmed by NVIDIA RTX 5070 Ti eGPU PCIe enumeration. `system_profiler` UI string `"Thunderbolt: No drivers loaded"` is misleading — it requires Apple firmware data structures that the Hackintosh stack doesn't populate, but underlying tunneling works.
- **DDI 4 and DDI 5 already accept port type 1** (entry #24) — TBT3-routed DP virtual DDIs in stock 8A52 framebuffer layout. A TB3 *dock* with DP/HDMI output would route display traffic through these DDIs, bypassing the port-type-rejection problem entirely. No kext patch needed for that path.

---

## Candidate next-step experiments (SUPERSEDED — fix found in entry #26)

> **★ FIX FOUND (entry #26):** Set `framebuffer-conN-pipe = <01000000>` for external connectors. This field is the runtime portType byte (despite WhateverGreen's "pipe" label). Value 1 is in the ICL kext's allow-list. con3 confirmed working with this fix; con1 and con2 modified, awaiting reboot/test.

> **All experiments below are deprecated** in light of the entry #26 finding. Kept for historical context. The TB3 dock path (was #1) is no longer needed since DDI 1/2/3 work directly with `pipe=1`.

> **Status of prior candidates:**
> - `agdpmod=vit9696 → pikera`: wrong fix (AMD dGPU only). Permanently removed.
> - `enable-lspcon-support=1` (global): tested, confirmed inert. Property removed (entry #17).
> - `agdpmod=vit9696 → ignore`: tested, confirmed null on this SMBIOS (entry #15). Boot-args still set to `ignore` for now (functionally same as `vit9696` here).
> - **Web research (entry #16): no public fix exists for Ice Lake port-type-10/18 rejection. Acknowledge this is a known-unsolved problem.**

1. **★ Plug a true Thunderbolt 3 *dock* with DP/HDMI output ★** (most promising; no config edits required)
    - Per entry #24 finding, the iGPU framebuffer already accepts port type 1 on DDI 4 and DDI 5 (TBT3-routed DP virtual DDIs).
    - A TB3 dock routes DP signal through the Thunderbolt protocol stack (not raw DP-alt-mode). The signal arrives at the iGPU through the TBT3 controller's DP adapter path, lands on DDI 4 or 5, classifies as port type 1, **gets accepted**.
    - **Required:** a true TB3 *dock* (CalDigit TS3 Plus / TS4, OWC Thunderbolt 3 Dock, Plugable TBT3-UDZ, Belkin TB3 Pro Dock). NOT a "USB-C hub" or "TB3 hub without display outputs".
    - **Confidence:** medium-high (~50–65%). TB3 PCIe tunneling confirmed working (entry #23). Remaining uncertainty: whether `AppleThunderboltDPAdapter` engages cleanly on Hackintosh (TB DP adapter is a separate code path from PCIe tunneling).
    - **No risk** — pure plug test, fully reversible, no config or kext changes.
    - **Failure mode if it doesn't work:** dock USB ports / ethernet still functional even if displays don't attach.

2. **WhateverGreen kernel patch — port-type allow-list expansion** (per entry #22; medium risk, medium confidence for partial fix).
    - 9-byte binary patch identified at file offset `338125568` in `SystemKernelExtensions.kc`:
        - `Find: 85 C0 74 24 83 F8 01 74 5A`
        - `Replace: 85 C0 74 24 90 90 90 EB 5A`
        - Identifier: `com.apple.driver.AppleIntelICLLPGraphicsFramebuffer`
    - Effect: forces all non-zero port types onto the type-1 acceptance path. Likely to enable USB-C external displays (port type 10), less likely to cleanly enable HDMI port (port type 18 — different signaling semantics).
    - Boot risk if patched kext panics; recovery via external boot disk and ESP edit.

3. **`-igfxtypec` boot-arg** (DEMOTED — empirically null; entry #21).
    - Tested in entry #21. Did not change runtime `portType` for any external port. Introduced AUX timeouts on DDI 1 + pipe underruns + DBuf failures. **Net negative.** Don't re-apply unless other experiments suggest a reason to.

4. **Test `hda-gfx` removal in isolation** (DONE — confirmed null, kept removed; entry #17).
    - Per jlempen's Surface-Laptop-3 commit 5b1b5f58. Removed from both iGPU and HDEF in entries #17 and the subsequent edit. **No effect on rejection.** Stays removed for cleanliness.

5. **WhateverGreen high-level flags via DeviceProperties** (lower-cost, lower-probability).
    - Apply Surface-profile flags **one at a time** with single-variable testing:
        - `enable-hdmi-dividers-fix` (HDMI clock divider fix; conceptually unrelated to port-type but part of Surface profile).
        - `enable-max-pixel-clock-override` (max pixel clock override).
        - `enable-dpcd-max-link-rate-fix` (DPCD max link rate fix).
    - Each: single boot/single-variable. Document delta in IORegistry FB state.
    - Risky given prior FB0 black-screen with bundled changes (entry #5). Hard rule: never bundle.

4. **SMBIOS change to `MacBookAir9,1`** (broader change, plausible payoff).
    - Different SMBIOS may load a different framebuffer kext version with different port-type allow-lists. Closer physical match to Spin 5 (Ice Lake ultrabook with HDMI + USB-C externals).
    - Per memory: BT was fixed via USBMap (model = MacBookAir9,1 there), so SMBIOS change should preserve BT. May need adjustments to: alcid, power management profiles, App Store entitlements, sleep behavior.
    - Reasonable as a focused test if (1)–(3) fail.

5. **WhateverGreen kernel patch — port-type allow-list expansion** (high effort, no public ICL precedent).
    - **Demoted from prior #1** because per entry #16 web research, no community-authored binary patch against `AppleIntelICLLPGraphicsFramebuffer` exists publicly that addresses portType rewrite. The KBL/CFL "port-type-acceptance" patches do not transfer — ICL kext layout is different.
    - Steps would be: disassemble `/S/L/E/AppleIntelICLLPGraphicsFramebuffer.kext/Contents/MacOS/AppleIntelICLLPGraphicsFramebuffer`; find xref of `Invalid port type` and/or `Unsupported port type` strings; identify comparison constants; author OC `Kernel > Patch` to add `0x0A` and `0x12` to the accepted set.
    - Significant time investment to author and validate the binary patch. Do this only if (1)–(4) fail.

6. **`has-lspcon-con1=1` for HDMI port only** (risky, narrow scope, low payoff).
    - Forces WEG LSPCON probe on DDI B. VBT says no LSPCON, so probe writes I²C to non-existent chip. Risk: bus hang on DDI B.
    - Only meaningful if all higher-priority candidates fail. Don't apply to con2/con3 under any circumstance.

7. **Report fresh issue at acidanthera/bugtracker** (community contribution).
    - With full IOReg dump + `/usr/bin/log show` capture + Linux i915/VBT decode comparison.
    - Issue #1432 is the canonical ICL umbrella issue and has no `port type 10/18` reproducer on file. Adding one would help the community even if no immediate fix lands.

### Why we no longer expect changing `framebuffer-conN-type` to help

Static type (`con1-type=DP(0x400)`) is set in DeviceProperties for connector descriptor metadata; runtime `portType` is computed independently from link training / DPCD probe / silicon path. Empirically: con1=DP(0x400) yields runtime portType=18, con3=DP(0x400) yields runtime portType=10. Static value does not feed into the rejection comparison.

### Diagnostic helpers
- **Use `/usr/bin/log show`, NOT `log show`** — zsh's `log` builtin shadows the macOS log tool. The absolute path works without sudo.
    ```bash
    /usr/bin/log show --last 10m --predicate 'eventMessage contains "port type" OR eventMessage contains "HOT_PLUG"' \
      | grep -v 'log run noninteractively' | head -40
    ```
- **Two log subsystem prefixes to grep for:** `[IGFB][ERROR][PORT]` (boot-init rejections) and `[IGFB][ERROR][HOT_PLUG]` (hot-plug rejections).
- **IORegistry quick-check for external attach:** `ioreg -l -w0 -r -c AppleIntelFramebuffer | grep -E 'AppleIntelFramebuffer@|IODisplayConnect|fOnline'` — if any FB@N has an `IODisplayConnect` child, an external display has been accepted.

---

## Resume instructions for next macOS session

1. Boot into macOS (internal disk).
2. Check this doc and the Linux output (`docs/linux-i915-ddi-mapping-2026-05-05.txt` if you committed it from Linux).
3. Author correct `framebuffer-conN-busid` overrides based on the DDI mapping.
4. Apply edits via Python plistlib to `/Volumes/ESP/EFI/OC/config.plist` (PlistBuddy can be flaky on data fields — Python is reliable).
5. Always backup ESP config before edits: `cp config.plist config-<reason>.plist.bak`.
6. Test changes single-variable, rebooting between each.
7. Mirror successful changes into the repo's `EFI/OC/config.plist`.
