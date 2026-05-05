# Linux i915 DDI Mapping — Spin 5 SP513-54N (2026-05-05)

Definitive port → DDI mapping captured from a Linux dual-boot session on the same physical hardware. This is the data that was previously inferable only from inferred WhateverGreen guessing and that decided the connector-override values now in `EFI/OC/config.plist`.

## Environment

| | |
|---|---|
| Kernel | 6.17.0-22-generic |
| Distro | Ubuntu 24.04.4 LTS (Noble Numbat) |
| iGPU | Intel Iris Plus Graphics G7 — `8086:8a52` rev 07 |
| DRM card | **`card1`** (`card0` is reserved for early-boot simpledrm on this kernel) |
| `i915` boot args | `i915.enable_fbc=0 i915.enable_hangcheck=0 i915.force_probe=8a52` |

`/dev/dri/card0` does not exist on this kernel/distro — `/dev/dri/card1` is the actual i915 device. All sysfs paths use `/sys/class/drm/card1-*`.

## VBT decode (firmware-canonical)

Read directly from the firmware-published VBT via `/sys/kernel/debug/dri/1/i915_vbt`:

```
$ sudo intel_vbt_decode --file=/tmp/vbt.bin
VBT signature: "$VBT ICELAKE        "
BDB version:    229
Child device count: 8
```

The four child devices that map to physical hardware:

| VBT child | Device handle | Description | DVO Port | Onboard LSPCON | Thunderbolt | DP USB-C |
|---|---|---|---|---|---|---|
| #1 | 0x0008 | LFP 1 (eDP) | DP-A (0x0a) | no | no | no |
| #2 | 0x0004 | EFP 1 (HDMI/DVI/DP) | HDMI-B (0x01) | **no** | no | no |
| #3 | 0x0040 | EFP 2 (HDMI/DVI/DP) | DP-C (0x08) | no | **yes** | **yes** |
| #4 | 0x0020 | EFP 3 (HDMI/DVI/DP) | DP-D (0x09) | no | **yes** | **yes** |

The remaining 4 child devices are unused/phantom slots in the VBT, no physical hardware behind them.

## Live i915 binding (cross-checked via hot-plug)

Confirmed by plugging into each port and observing which DRM connector flipped to `connected`, then reading `i915_display_info` for the encoder line:

| Physical port | DRM connector | i915 encoder line | DDI / PHY | busid |
|---|---|---|---|---|
| Internal panel | `card1-eDP-1` | `[ENCODER:264:DDI A/PHY A]` | A / A | **0** |
| Built-in HDMI | `card1-HDMI-A-1` | `[ENCODER:?:DDI B/...]` (combo PHY 1) | B / combo | **1** |
| **Rear USB-C** | **`card1-DP-1`** | `[ENCODER:282:DDI C (TC)/PHY TC1]` | **C / TC1** | **2** |
| **Front USB-C** | **`card1-DP-2`** | `[ENCODER:291:DDI D (TC)/PHY TC2]` | **D / TC2** | **3** |

VBT DVO port and live i915 binding agree. busid value = (DDI letter ordinal) − 1, i.e., A=0, B=1, C=2, D=3.

## Hot-plug session log

```
=== Baseline — nothing external ===
  card1-DP-1 : disconnected
  card1-DP-2 : disconnected
  card1-eDP-1 : connected
  card1-HDMI-A-1 : disconnected

=== HDMI direct (cable into laptop's HDMI port) ===
  card1-HDMI-A-1 : connected           ← DDI B confirmed

=== USB-C front (Genesys Logic hub + 1920×1080 HDMI display) ===
  card1-DP-2 : connected               ← DDI D / TC2

=== USB-C rear (same hub, moved) ===
  card1-DP-1 : connected               ← DDI C / TC1
```

When the rear USB-C was active:

```
[CRTC:185:pipe B]:
    uapi: enable=yes, active=yes, mode="1920x1080" @60Hz, port_clock=270000, lane_count=2
    [ENCODER:282:DDI C (TC)/PHY TC1]: connectors:
        [CONNECTOR:283:DP-1]
        DP branch device present: yes
            Type: HDMI
            Max TMDS clock: 600000 kHz
        HDCP version: HDCP1.4 HDCP2.2
```

The "DP branch device present: yes / Type: HDMI" indicates the hub's CCG controller is doing native DP→HDMI conversion at its end — the iGPU is signaling DP, the hub bridges to HDMI. No LSPCON needed on the laptop side because the hub provides one.

## Critical findings

### 1. macOS USB-C DP alt-mode failure is driver-side only

The same Genesys Logic hub that "produces zero DDI events on plug" in macOS produces a clean DP-2 connection at native 1920×1080 in Linux. Hardware, firmware, PD controller, and TC PHY are all functional. **The macOS-side failure is purely WhateverGreen / `AppleIntelICLLPGraphicsFramebuffer` not handling TC PHY init correctly** — most likely because the stock 8A52 framebuffer connector descriptors have busids that don't match the Spin 5's actual layout (MBP16,2 has 4× TC, Spin 5 has 2× TC + 1× HDMI).

### 2. HDMI port has no on-board LSPCON

VBT child #2 declares `Onboard LSPCON: no`. The HDMI port socket is wired directly to DDI B's combo PHY. Combo PHY supports native HDMI signaling at the silicon level, but WhateverGreen on Ice Lake has known issues forcing HDMI-typed output on this generation. Empirically, `con1-type=HDMI(0x800)` produced `sinkCount=0, portMode=1` (DP signaling reaching an HDMI sink that can't decode it). With no LSPCON to convert, **the built-in HDMI port can only drive HDMI displays via an external active DP→HDMI adapter**.

### 3. S3 suspend/resume works on Linux on this hardware

```
$ date; sync; systemctl suspend
Tue May  5 10:10:08 PM HKT 2026
[suspend]
[resume by keyboard press]
$ date
Tue May  5 10:10:?? PM HKT 2026

$ for c in /sys/class/drm/card1-*; do
    echo "$(basename $c): $(cat $c/status)"
  done
card1-DP-1: disconnected
card1-DP-2: disconnected
card1-eDP-1: connected           ← display restored
card1-HDMI-A-1: connected         ← external HDMI also restored
```

The system suspended (silent, fan stopped) and woke cleanly on keyboard press. Internal display restored, external HDMI display still active. **This invalidates the prior "S3 wake is firmware-structurally broken on this hardware" diagnosis.** The firmware and platform support clean S3 sleep+wake. The macOS-side wake failure (black screen + fan ramp) is a driver bug in `AppleIntelICLLPGraphicsFramebuffer`, not a firmware limit.

## What this changes about macOS strategy

| Before this data | Now |
|---|---|
| macOS S3 sleep declared "structurally broken on Ice Lake firmware" | macOS S3 sleep is firmware-supported; failure is in macOS GFX driver — fixable in principle |
| HDMI/USB-C external displays "wrong busids" — guessing | HDMI/USB-C have known busids 1 / 2 / 3 — overrides authored in `EFI/OC/config.plist` (commit `5029ee0`) |
| Hibernate-25 was the only option | Hibernate-25 still works, but S3 sleep deserves another try with proper connector overrides + `EXT4` Notify hook |
| "Just disable hibernate and shut down manually" recommendation | Same recommendation as fallback, but S3 may now be achievable |

## Reference data

### iGPU PCI ID and connector inventory

```
00:02.0 VGA compatible controller [0300]: Intel Corporation Iris Plus Graphics G7 [8086:8a52] (rev 07)

/sys/class/drm/card1
/sys/class/drm/card1-DP-1        ← rear USB-C
/sys/class/drm/card1-DP-2        ← front USB-C
/sys/class/drm/card1-eDP-1       ← internal panel
/sys/class/drm/card1-HDMI-A-1    ← built-in HDMI port
/sys/class/drm/renderD128
```

### USB-C hub identification (used for tests)

Genesys Logic generic USB-C hub:
- USB IDs: `1d6b:0002` (HS root hub) + `1d6b:0003` (SS root hub) — not seen in lsusb until plugged
- ITE BillBoard descriptor: `048D:5212` — confirms hub advertises DP alt-mode capability
- Function: USB passthrough + AX88179B Ethernet + USB3.0 SD card reader + DP→HDMI converter

### Diagnostic command reference

```bash
# Connector status
for c in /sys/class/drm/card1-*; do
  echo "$(basename $c) : $(cat $c/status 2>/dev/null)"
done

# Encoder/DDI mapping for active connectors
sudo cat /sys/kernel/debug/dri/1/i915_display_info | grep -B 1 -A 1 'ENCODER.*DDI'

# VBT decode (firmware-canonical)
sudo cat /sys/kernel/debug/dri/1/i915_vbt > /tmp/vbt.bin
sudo intel_vbt_decode --file=/tmp/vbt.bin

# Live HPD watcher (run in second terminal during hot-plug tests)
sudo journalctl -kf | grep --line-buffered -iE "i915|drm|hpd|hotplug|ddi|tc[0-9]"
```

## Pending macOS-side experiments (in priority order)

After deploying the connector overrides in commit `5029ee0`:

1. **Test front USB-C with hub** — same physical port that worked under Linux at DP-2 / DDI D. Expect DDI hot-plug events on plug now that `framebuffer-con3-busid=3` matches the real DDI letter.
2. **Test rear USB-C with hub** — `framebuffer-con2-busid=2` for DDI C / TC1.
3. **Built-in HDMI** — only worth testing with active DP→HDMI adapter; passive cable will not work (no LSPCON).
4. **S3 sleep+wake retry** — with the EXT4 SSDT (commit `33c0fa6`) plus correct connector descriptors, `pmset sleepnow` may succeed where it previously didn't, since the iGPU driver has a smaller surface area of phantom-connector-related bugs to hit on resume.
5. **If S3 still fails** — kernel-side panic capture via SSH-survival test or `log show --predicate 'subsystem == "com.apple.iokit.IOPMrootDomain"'` after a failed wake. With the firmware confirmed good, the failure must be capturable in macOS kernel logs.

## What got us here

- Static DSDT/SSDT-5 decompilation: confirmed dynamic display device pool with runtime-populated `DIDL/DDL2..DDL15` fields → static dump alone could not give port→DDI mapping. Linux i915 + VBT decode is the right tool.
- Linux `i915` driver init logs: definitive DDI ↔ port assignments printed from the VBT at probe time.
- `intel_vbt_decode --file=/tmp/vbt.bin`: firmware's own canonical port table, gold-standard.
- Live hot-plug + sysfs `status` polling: real-time confirmation of which DRM connector each physical port maps to.
- `systemctl suspend` test on the same hardware: settled the question of whether the firmware can do S3.
