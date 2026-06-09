# NVRAM reset clears Spin 5 symptom triad — empirical recipe (2026-06-09)

A single BIOS-side NVRAM reset on 2026-06-09 simultaneously cleared three apparently-unrelated symptoms that had been escalating on this Spin 5 (SP513-54N, Insyde firmware, MacBookPro16,2 SMBIOS, Sonoma 14.8.5). Mechanism is not proven; the recipe is the value. This note is the forensic trail and a hypothesis worth carrying forward.

---

## TL;DR

| Symptom | Pre-reset state | Post-reset state |
|---|---|---|
| 1. BIOS Setup inaccessible | F2 at POST ignored, machine boots straight through to OC | F2 enters Setup normally |
| 2. USB-C external display fails | `ITE BillBoard 0x048D:5212` appears in USB tree; zero DDI hotplug events on replug; FB@1/FB@2 have no `IOFBCurrentPixelClock` | DP alt-mode enters cleanly; Billboard gone; Dell external lights at 148.5 MHz pclk on FB@1 (`AppleDisplay-10ac-4241`) |
| 3. Hibernation "already started, failed" stuck flag | Reported on wake from valid hibernate image | Two consecutive clean hibernate→wake cycles, no stuck flag |

The pipe=1 + `agdpmod=ignore` external-display fix (see `HDMI-USBC-debug-2026-05-05.md`, commit `e070c09`) was already in place and verified intact on disk — the regression was NOT a kext-config drift. WEG injection, boot-args, and ESP layout were all unchanged.

---

## The three symptoms in detail

### 1. BIOS Setup inaccessible

F2 (and Del, Esc, F12) at POST were being silently swallowed. The machine reached the OC picker without ever entering Insyde Setup. This had been the case for at least several days before 2026-06-09 — not a sudden break.

### 2. USB-C external display fails (regression of the May-6 fix)

Symptom pattern, observed via `ioreg`, `log show`, and visual inspection of the USB tree:

- Dell external on rear USB-C: no signal, no backlight
- USB-C dock USB function (Genesys hub + ASIX ethernet) enumerated and worked normally — so the cable, port, and PD power negotiation for the USB function were OK
- `ioreg -l -w0 -p IOUSB | grep -i Billboard` showed `ITE BillBoard 0x048D:0x5212` device present. The USB-IF Billboard Device class is the standardized "I tried to enter alt-mode and it failed; here's why" reporter — its presence is the diagnostic signal
- Live `log show --predicate 'eventMessage CONTAINS "DDI"'` during cable replug: zero hotplug events on con2/con3
- `framebuffer-conN-pipe/type/busid/enable` properties in `IOService:/IOResources/WhateverGreen` matched the May-6 working config exactly — pipe=1, type=DP, busid set per DDI, enable=1
- `boot-args` at runtime included `agdpmod=ignore` (verified via `nvram boot-args`)

So: the WEG configuration is correct; the failure is upstream of macOS — at the USB-C PD silicon level, before alt-mode entry ever signals to the GPU. The display is never offered DP capability.

### 3. Hibernation "already started, failed" stuck flag

Wake from a valid hibernate image (`/var/vm/sleepimage` size and mtime confirming a real S4 entry) reported the hibernation as `already started, failed` and proceeded with cold-boot path, losing session state. This had been intermittent for several days, growing more frequent.

---

## The reset

Reset method:

1. Boot into BIOS Setup. **Pre-reset: F2 was being ignored.** Workaround: hold F2 from cold power-on (not from OS reboot), persisted across several attempts until Setup accepted the keypress on one of them — exact reason for the intermittency unknown.
2. In Setup: `Exit → Load Setup Defaults → Y → F10 Save and Exit`.
3. Power cycled.

After reset:
- F2 enters Setup normally on first try
- `efibootmgr -v` (from Linux, separate boot) shows the previously-existing standalone OC `Boot####` entry **gone** — Insyde's "Load Defaults" wipes `Boot####` variables along with the rest. OC was hijacking `\EFI\Microsoft\Boot\bootmgfw.efi` as its install path, so it still boots; the standalone entry was redundant
- `boot-args` preserved across the reset because OC repopulates them from `NVRAM:Add` in config.plist (this is the expected OC behavior, not a quirk)

Plug Dell external into rear USB-C: lights immediately, `ioreg` shows `AppleDisplay-10ac-4241` at FB@1, 148.5 MHz pixel clock, Billboard gone from USB tree.

Hibernate: clean image write, clean wake on first try.

---

## Same-day follow-up: hibernation under Windows-hijack-only

The reset happened mid-afternoon 2026-06-09. Same-day session after the reset — current OC boot configuration is **Windows-hijack-only** (no standalone OC `Boot####` entry exists in firmware NVRAM; OC boots via `\EFI\Microsoft\Boot\bootmgfw.efi` hijack alone).

Hibernation observations (`log show --predicate 'eventMessage CONTAINS "hibernat"'`, kernel boot 17:25:32):

| # | Sleep start | Image setup | Wake | Duration | OC error? |
|---|---|---|---|---|---|
| 1 | 17:27:02 (PMRD phase 0 → phase 2) | preflight 465179 pages, `hibernate_setup(0) took 2395 ms` | 17:27:56 `Pipe A active on wake from sleep, system waking from hibernation` | ~54s (likely re-woken immediately) | none |
| 2 | 18:19:49 | preflight 490758 pages, 118263 xpmapped skipped (normal), `hibernate_setup(0) took 7239 ms` | 20:01:16 (same `Pipe A active` signature) | ~1h 41m | none |

No `already started` flag, no `hibernate read failed`, no `hibernate image not found`. Both wakes show `(AppleACPIPlatform) Wake reason: (User)` then clean ICL framebuffer resume.

---

## Hypothesis: standalone OC `Boot####` entry as a hibernation precondition

n=2 cycles in one session is suggestive, not proof. But the change introduced by the NVRAM reset that correlates with the hibernation fix is the **disappearance of the standalone OC firmware boot entry**. Before the reset, OC had both:

- a Linux-generated `Boot####` entry pointing at `\EFI\OC\OpenCore.efi` (one entry, generated once via `efibootmgr`, surviving across boots — not accumulation)
- the Windows-bootmgfw hijack path at `\EFI\Microsoft\Boot\bootmgfw.efi`

After the reset, only the hijack path remains. Hypothesis: coexistence of a standalone OC `Boot####` entry with macOS's `boot-image` NVRAM variable creates a window where the hibernation resume path resolves the wrong boot device — firmware reads the `boot-image` set by macOS pre-hibernate, but the next-boot decision picks the standalone OC entry rather than the `bootmgfw.efi` path macOS expected to find its hibernate image via, producing the "already started" stuck flag.

This is **not verified** — it's a correlation observed in one session. The way to falsify it would be to re-create a standalone OC `Boot####` entry via efibootmgr and watch for the "already started" flag to return.

**Recommendation:** don't re-add the standalone OC boot entry. Keep OC on the Windows-bootmgfw hijack path. If the hibernation flag returns despite this setup, the hypothesis is dead and the root cause is elsewhere.

---

## What the NVRAM reset doesn't explain

The three symptoms were apparently independent (PD silicon, firmware Setup-key handling, macOS hibernation flag), yet all three cleared from one variable reset. The candidates for "which NVRAM variable went stale":

- PD-related Insyde private variables (no documented schema)
- Setup-key buffer / hotkey timer state (Insyde firmware bug — wouldn't be the first such)
- An EFI variable corruption affecting reads/writes broadly (would explain all three but is hard to verify post-hoc)

The 2026-06-09 reset cleared all variables non-selectively, so we can't attribute the fix to any specific one. If the symptom triad recurs, dump `efivar -l` before resetting and diff against a known-good state — that's the only path to narrowing this down.

---

## How to apply

If any of the three symptoms appear on this hardware in the future — **especially if more than one appears together** — try the NVRAM reset **before** investigating kext config, ESP layout, or hardware. Tier this above suspecting WEG/`agdpmod` regression.

Procedure:
1. BIOS Setup → Load Setup Defaults → Save → Exit
2. Boot back into macOS — boot-args persist via OC, no manual restoration needed
3. **Do NOT re-add a standalone OC `Boot####` entry** via Linux `efibootmgr` or any other tool. The Windows-bootmgfw hijack path is sufficient and (per the hypothesis above) may be load-bearing for clean hibernation

---

## References

- `HDMI-USBC-debug-2026-05-05.md` — the original pipe=1 + agdpmod=ignore external display fix that this regression was masquerading against
- `Hibernate-25-debug-2026-05-08.md` — the hibernate-25 / mode-25 mechanics this builds on
- Commit `e070c09` — `THE FIX: framebuffer-conN-pipe is the runtime portType byte for ICL kext`
