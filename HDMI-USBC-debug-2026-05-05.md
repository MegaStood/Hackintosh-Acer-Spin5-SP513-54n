# HDMI / USB-C external display debug — session 2026-05-05

Goal: get **at least one** external display path working on the Spin 5 SP513-54N (Ice Lake i7-1065G7, 8A52 framebuffer, MacBookPro16,2 SMBIOS, Sonoma 14.8.5).

Three candidate paths:
- **Path A**: built-in HDMI port
- **Path B**: USB-C #1 (front) DP alt-mode via hub
- **Path C**: USB-C #2 (rear) DP alt-mode via hub

This document captures what was tried, what was learned, and the **next-step Linux diagnostic** to run from the dual-boot Linux install. Read this on the Linux side before running the commands at the bottom.

---

## TL;DR — current state

- **Internal display**: working ✓
- **HDMI port (Path A)**: when patched with `con1` overrides, DDI 1 hot-plugs on cable plug, but `portMode=1` (DP signaling on a port that goes to an HDMI-only sink) and `sinkCount=0` (no DDC response). Display stays black. Likely no LSPCON in this hardware.
- **USB-C ports (Paths B/C)**: USB function works (mouse/Ethernet/SD-reader through hub all enumerate fine in macOS). DP alt-mode handshake to the iGPU **never engages** — zero `Hotplug detected on ddi` events, zero TBT topology change events.
- **Root cause we're now investigating**: stock 8A52 framebuffer's connector descriptors (busids `0x02, 0x09, 0x0A, 0x0B, 0x0C` for the 5 DP slots) target the MacBook Pro 16,2 reference platform layout, which has 4× Type-C and 0× HDMI — wrong shape for our Acer (1× HDMI + 2× Type-C). Without correct `framebuffer-conN-busid` overrides, DP alt-mode events have nowhere to land in the framebuffer driver.

We need the **physical port → DDI letter mapping** for this firmware to author correct connector overrides. That's what the Linux test plan below produces.

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
enable-lspcon-support          = <01000000>      (added 2026-05-06; pending reboot/test)
hda-gfx                        = 'onboard-1'    (HDMI audio routing)
igfxfw                         = <02000000>     (firmware load mode 2)
model                          = 'Intel Iris Plus Graphics G7'
```

**Differences from pre-rework backup (`config-pre-surface-rework.plist.bak`):**
- **Removed** (still missing): all 18 `framebuffer-con1/2/3-*` overrides + `enable-cfl-backlight-fix`
- **Boot-args**: `igfxlspcon=1` removed, `igfxonln=1` re-appended (different position than backup but functionally equivalent)
- Everything else identical

### Boot-args (live)
```
keepsyms=1 debug=0x100 -btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck agdpmod=vit9696 alcid=13 -v -no_compat_check -igfxdbg -liludbgall igfxonln=1
```

`-igfxdbg -liludbgall` is on for diagnostic verbosity. Keep until done debugging.

### Available backups in ESP
- `/Volumes/ESP/EFI/OC/config-pre-lspcon-test.plist.bak` — pre-LSPCON edit (2026-05-06; has stolenmem 38 / fbmem 19, no enable-lspcon-support).
- `/Volumes/ESP/EFI/OC/config-pre-stolenmem-bump.plist.bak` — pre-memory-bump (2026-05-06; has stolenmem 19 / fbmem 9, no enable-lspcon-support).
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

---

## Test results matrix — `5029ee0` connector overrides + LSPCON flag

| Path | Plug | DDI event? | Static type | Runtime portType | portMode | sinkCount | Result |
|---|---|---|---|---|---|---|---|
| HDMI port (con1, busid=1) | direct HDMI cable to built-in port | ✓ `ddi = 1` HPD high | DP (0x400) | **18** (0x12) | 1 (DP signaling) | 0 | rejected — `Unsupported port type 18` + non-managed |
| FRONT USB-C (con3, busid=3) | Genesys hub + HDMI display | ✓ `ddi = 3` HPD high | DP (0x400) | **10** (0x0A) | 3 (DP-alt-mode/TC) | 0 | rejected — `Unsupported port type 10` + non-managed |
| REAR USB-C (con2, busid=2) | Not yet tested as primary plug | ✓ glitched once during front-plug transient | DP (0x400) | (10 expected) | (3 expected) | n/a | inconclusive — need dedicated test |
| Internal eDP (con0, default) | always-on | n/a | LVDS (0x02) | LVDS | n/a | n/a | ✓ working |

**Findings:**
- All three external DDI mappings are **proven correct** (DDI events fire on the expected DDI per Linux/VBT data).
- Two distinct runtime `portType` rejection buckets: `18` for native DDI (HDMI port), `10` for TC PHY (USB-C ports). Same downstream `Non-managed external displays are no longer supported` rejection in both.
- Static `framebuffer-conN-type` does **not** influence runtime `portType`. Changing it won't help.
- LSPCON code path **confirmed inert** with global `enable-lspcon-support=1` alone (no per-connector flags).
- The rejection happens inside `AppleIntelICLLPGraphicsFramebuffer` at port-type-allow-list level, **upstream of AGDC**. The `AGDC Callback is not yet registered!!` in logs is a misleading harmless message.

---

## Candidate next-step experiments (revised 2026-05-06 after Path A test + LSPCON null result)

> **Notes:**
> - Previous suggestion `agdpmod=vit9696 → agdpmod=pikera` was wrong — `pikera` is for AMD Navi/Vega dGPU board-id mismatch, not relevant for Intel iGPU. Permanently removed.
> - LSPCON probe (entry #12) tested and **confirmed inert** without per-connector `has-lspcon-conN=1`. Property left in config (harmless) for now.
> - Path A (HDMI port) tested (entry #13) — produces `Unsupported port type 18` + `portMode=1` + `sinkCount=0`. Not solved by any current config edit.

1. **WhateverGreen kernel patch — port-type allow-list expansion** (most-targeted, requires deep work).
    - Goal: patch `AppleIntelICLLPGraphicsFramebuffer::handleHotPlug` (or whichever method emits `Unsupported port type N`) to **accept** runtime `portType = 10` and `portType = 18`, OR to rewrite them to `portType = 2` (DP) at the comparison site.
    - Steps:
        1. Disassemble `/System/Library/Extensions/AppleIntelICLLPGraphicsFramebuffer.kext/Contents/MacOS/AppleIntelICLLPGraphicsFramebuffer` (binary `Mach-O 64-bit x86_64`).
        2. Find the string "Unsupported port type" — locate its xref to identify the comparison instruction.
        3. Identify the constant being compared (e.g. `cmp eax, 2` or `cmp eax, 4` for accepted values; or a switch table).
        4. Author OpenCore `Kernel > Patch` entry: replace pattern with one that accepts `0x0A` and `0x12`.
    - High effort (requires disassembly skill). Highest probability of being a real fix.

2. **WhateverGreen high-level port-type rewrite via DeviceProperties** (lower effort, lower probability).
    - Investigate WEG flags that affect runtime port-type classification:
        - `force-online=1` per-connector (instead of via `igfxonln=1` boot-arg) — different code path.
        - Surface-profile aggressive `enable-*` flags (`enable-dpcd-max-link-rate-fix`, `enable-max-pixel-clock-override`, `enable-hdmi-dividers-fix`, etc.) applied **one at a time** with single-variable testing.
        - `disable-typec-framebuffer-unload` — if it exists in our WEG version.
    - Risky given prior FB0 black-screen with bundled changes. Single-variable approach mandatory.

3. **SMBIOS change** (different framebuffer kext + AGDP rules).
    - From `MacBookPro16,2` → e.g. `MacBookAir9,1` (closer match physically — Ice Lake ultraportable with internal panel + HDMI/USB-C externals). Different SMBIOS may load a different framebuffer kext version or have different port-type allow-lists.
    - Cascading effects: BT (per memory: fixed via USBMap, probably safe), audio (alcid may need adjusting), power management, App Store services, sleep behavior, hibernate.
    - Reasonable as a one-shot test if (1) and (2) fail or are too involved.

4. **`agdpmod=ignore`** (cheap test, predicted not to help).
    - Single boot-arg edit, easy revert.
    - Predicted not to help because rejection is at framebuffer level (port-type allow-list), not AGDP level. But cheap enough to try once for empirical confirmation. If it does clear the rejection, we learn that AGDP is involved after all.

5. **`has-lspcon-con1=1` for HDMI port only** (risky, narrow scope).
    - Forces WEG LSPCON probe on DDI B specifically. VBT says no LSPCON on con1, so probe writes I²C registers to a non-existent chip. Risk: bus hang on DDI B, possibly black-screening the HDMI port.
    - Only worth trying if (1)–(3) fail and we're willing to accept HDMI port instability. Don't apply to con2/con3.

### Why we no longer expect changing `framebuffer-conN-type` to help

Static type (`con1-type=DP(0x400)`) is set in DeviceProperties for connector descriptor metadata; runtime `portType` is computed independently from link training / DPCD probe / silicon path. Empirically: con1=DP(0x400) yields runtime portType=18, con3=DP(0x400) yields runtime portType=10. Static value does not feed into the rejection comparison.

---

## Resume instructions for next macOS session

1. Boot into macOS (internal disk).
2. Check this doc and the Linux output (`docs/linux-i915-ddi-mapping-2026-05-05.txt` if you committed it from Linux).
3. Author correct `framebuffer-conN-busid` overrides based on the DDI mapping.
4. Apply edits via Python plistlib to `/Volumes/ESP/EFI/OC/config.plist` (PlistBuddy can be flaky on data fields — Python is reliable).
5. Always backup ESP config before edits: `cp config.plist config-<reason>.plist.bak`.
6. Test changes single-variable, rebooting between each.
7. Mirror successful changes into the repo's `EFI/OC/config.plist`.
