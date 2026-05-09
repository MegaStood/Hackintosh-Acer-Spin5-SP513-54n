# Hibernate-25 (S4) debug — session 2026-05-07 → 2026-05-09

Goal: get hibernate-25 (suspend-to-disk, mode 25) working reliably on Acer Spin 5 SP513-54N (Ice Lake i7-1065G7, MacBookPro16,2 SMBIOS, Sonoma 14.8.5). Real S3 wake is firmware-broken (see `S3-sleep-debug-2026-05-07.md`); hibernate-25 is the alternative because it bypasses the firmware S3 wake bug entirely (full power-off → cold-boot resume from sleepimage via `boot.efi`).

---

## RESOLVED 2026-05-09 — actual root cause was pmset persistence

Two consecutive successful clamshell-on-battery hibernate-25 resumes:

```
2026-05-09 08:27:29  Wake from Standby [CDNVA] : due to /UserActivity, BATT 88%
                     HibernateStats hibmode=25 standbydelaylow=10800 standbydelayhigh=86400 rd=95 ms
                     WakeTime: 0.850 sec
2026-05-09 08:30:50  Wake from Standby [CDNVA] : due to /UserActivity, BATT 85%
                     HibernateStats hibmode=25 standbydelaylow=10800 standbydelayhigh=86400 rd=111 ms
                     WakeTime: 0.703 sec
```

OC log signatures (both 08:27 and 08:30 boots):
```
OCVAR: Locate emulated NVRAM protocol - Not Found
OCB: boot-image is 70 bytes - Success
OCB: NVRAM hibernation is 1 / Success / 44
OC: Hibernation activation - Success, hibernation wake - yes
AAPL: #[EB|H:IS] 1
```

### Final working stack (validated)

**OC config.plist:**
- `UEFI:Drivers:OpenVariableRuntimeDxe.efi` Enabled = **false** (real firmware NVRAM, no emulation)
- `UEFI:Drivers:OpenRuntime.efi` Enabled = true (required by OC, kept)
- `NVRAM:LegacyOverwrite` = **false** (don't wipe firmware Boot vars)
- `Misc:Boot:HibernateMode` = NVRAM
- `Booter:Quirks:DiscardHibernateMap` = true
- `UEFI:ReservedMemory` entry: Address=569344, Size=4096, Type=RuntimeCode
- `Kernel:Add:HibernationFixup.kext` Enabled = true (v1.5.4)

**pmset:**
```bash
sudo pmset restoredefaults     # standby=1, tcpkeepalive=1, etc — defaults are right
sudo pmset -a hibernatemode 25
ls -la /Library/Preferences/com.apple.PowerManagement*.plist  # verify mtime updated to NOW
```

The `ls` is **not** optional — see "What was actually wrong" below.

### What was actually wrong (corrects every earlier section of this doc)

The TWO regressions hypothesis (NVRAM emulation + four ACPI items) was wrong on the second half. Re-enabling `_PTS to ZPTS` patch, `_WAK to ZWAK` patch, and `SSDT-PTSWAKTTS-iGPU.aml` made no difference — the 2026-05-09 successes had `SSDT-EXT4-iGPU-Wake.aml` still disabled. Those four items were noise, not signal.

**The actual silent killer: pmset persistence.** Yesterday evening I ran
```
sudo rm /Library/Preferences/com.apple.PowerManagement*    # zsh: no matches found
sudo pmset hibernatefile /var/vm/sleepimage
sudo pmset -a hibernatemode 25
pmset -g | grep hibernatemode    # showed 25
```
The `rm` reported "no matches found" — the plists were already missing. Subsequent `pmset` commands updated IOKit runtime state (so `pmset -g` correctly showed 25) but **never persisted to disk**, because pmset's create-if-missing path apparently doesn't take when the daemon holds stale state. Every subsequent reboot — including the 5+ failed sleep cycles between then and 2026-05-09 morning — loaded factory defaults (`hibernatemode=3`) from the regenerated plist. Mode 25 was never in effect at any sleep entry. All "hibernate-25 failed" data points were actually "S3 attempted (because mode=3) → firmware bug → cold reboot."

### How to detect this state

Before any sleep test, run:
```
ls -la /Library/Preferences/com.apple.PowerManagement*.plist
```
If the files don't exist, or their mtime is older than the most recent `pmset -a` command, **the kernel will not use the settings you think you set**. Reboot to let powerd recreate the plists with defaults, then re-run your `pmset -a` commands so they write to existing files. Verify mtime updates after the command. (Captured as `feedback_pmset_plist_reset_ordering.md` in memory so future debug sessions surface it automatically.)

### Forensic discriminators (still correct)

- Real S4 resume: `OCB: boot-image is N bytes - Success` (N>0) + `OC: Hibernation activation - Success, hibernation wake - yes` + pmset `Wake from Standby [CDNVA]` + `HibernateStats hibmode=25 ... rd=NN ms`. **`rd=NN ms` is the unforgeable userspace signal.**
- Cold boot (no resume): `boot-image is 0 bytes - Not Found` + `Hibernation activation - Not Found, hibernation wake - no`.
- **`/var/vm/sleepimage` size is NOT diagnostic.** File stays at exactly 1,073,741,824 bytes (1 GiB pre-allocation) even when hibernate fires successfully — the kernel writes the compressed working set within the pre-allocated file without growing it. I claimed otherwise mid-session; that was wrong.

### Caveats — DO NOT touch on follow-up

- Do not revert `agdpmod=ignore` → `vit9696`. Required for external display (`project_hackintosh_external_display_fix.md`); orthogonal to hibernate.
- Do not restore `-noDC9`. Phase 6 in `S3-sleep-debug-2026-05-07.md` falsified it.
- Do not remove `org.acidanthera.nvramhook.{daemon,agent}` LaunchDaemons while emulated NVRAM is OFF — they're vestigial but harmless. Only remove if you also keep `OpenVariableRuntimeDxe.efi=false`.

---

Prior context: hibernate-25 had only succeeded **twice ever** on this machine before this session — 2026-05-02 23:59:06 (`rd=86 ms`) and 2026-05-03 00:33:04 (`rd=95 ms`). Every other attempt either silently downgraded to S3 or failed without writing a sleepimage. This session's goal was to understand *why*. Sections below capture the falsified hypotheses and forensic work along the way; the resolution above supersedes them.

---

## TL;DR (corrected 2026-05-08 23:30) — TWO coupled regressions, not one

A line-by-line side-by-side of `opencore-2026-05-02-155856.txt` (working resume) vs `opencore-2026-05-08-143529.txt` (failed cold-boot) revealed **two** independent load-bearing differences. The earlier "NVRAM emulation is the smoking gun" framing in commit `b92040a` was incomplete.

| Layer | May-2 success | 2026-05-08 failures |
|---|---|---|
| **A. ACPI patches** `_PTS to ZPTS` (idx 4), `_WAK to ZWAK` (idx 5) | Enabled, applied | **Removed** — log skips from idx 3 to idx 6 |
| **A. ACPI SSDTs** `SSDT-PTSWAKTTS-iGPU.aml`, `SSDT-EXT4-iGPU-Wake.aml` | Loaded | **Disabled** — explicit `Skipping add ACPI ...` lines |
| **B. UEFI driver** `OpenVariableRuntimeDxe.efi` | NOT loaded → real firmware NVRAM | Loaded → emulated NVRAM |
| **B. NVRAM:LegacyOverwrite** | (no wiping) | `true` → wipes firmware Boot vars |
| **B. HibernationFixup.kext** | Enabled | Disabled (re-enabled late 2026-05-08) |
| Working `boot-image` NVRAM | Persists across hibernate | Lost across hibernate |
| `OCAK` count (kext injection) | 0 (resume path skips) | 79 (cold-boot path) |

The two regressions are **independent and likely both required**:

### Regression A — coupled `_PTS/_WAK` rename + custom SSDT pair

The patches rename Insyde's native `_PTS`/`_WAK` ACPI methods to `ZPTS`/`ZWAK`. The `SSDT-PTSWAKTTS-iGPU.aml` SSDT provides Hackintosh-friendly replacements for `_PTS`/`_WAK` that chain-call the renamed native methods. They're inseparable:

- Without the rename: a custom-SSDT `_PTS`/`_WAK` collides with the native, OC drops one
- Without the custom SSDT: the rename strips Insyde's native `_PTS`/`_WAK` from the namespace, leaving sleep entry/exit with no power-state handler

The native Insyde `_PTS`/`_WAK` on this firmware are also where the `0x002A001F` EFI/BootROM fault originates (per `S3-sleep-debug-2026-05-07.md`). Stripping them via the rename, then *not* providing replacements, is the worst of both worlds: hibernate-25 has no clean power-state handoff at sleep entry → kernel falls back to mode 0 → S3 only → firmware bug at wake.

`SSDT-EXT4-iGPU-Wake.aml` is a smaller iGPU wake-handler shim from the same family; pair it with the rest.

### Regression B — emulated NVRAM (already documented above)

`OpenVariableRuntimeDxe.efi` redirects all NVRAM I/O through OC's RAM-resident store. Hibernate-25 dumps RAM and powers off without firing `launchd`'s shutdown signal, so the `org.acidanthera.nvramhook.daemon` LaunchDaemon (the only mechanism that persists the emulated store to `nvram.plist`) **never runs during hibernate** — `boot-image` written by `boot.efi` lives only in volatile memory and dies with the power. Architectural limitation of userspace persistence, not a daemon bug. Both 2026-05-02 successes ran with this driver NOT loaded (real firmware NVRAM, `OCVAR: Locate emulated NVRAM protocol - Not Found`).

Worse: with emulation active, every cold boot logs `OCVAR: Restoring FW NVRAM...` followed by `Deleting NVRAM ...:boot-args - Success` and re-creating the variable — the emulated store actively rewrites firmware NVRAM on each boot. Even if `boot-image` somehow survived to `nvram.plist`, it would be overwritten or absent on the restore pass.

### Working stack (mirrors May-2 byte-for-byte where it matters)

```
ACPI:
  Patch (_PTS to ZPTS)             → Enabled = true     ← MUST be re-enabled
  Patch (_WAK to ZWAK)             → Enabled = true     ← MUST be re-enabled
  SSDT-PTSWAKTTS-iGPU.aml          → Enabled = true     ← MUST be re-enabled
  SSDT-EXT4-iGPU-Wake.aml          → Enabled = true     ← MUST be re-enabled

UEFI:
  HfsPlus.efi                      → Enabled = true
  OpenCanopy.efi                   → Enabled = true (May-2 had it loaded; current is false — re-enable if you want the picker UI, otherwise harmless either way for hibernate)
  OpenVariableRuntimeDxe.efi       → Enabled = false    ← drop emulated NVRAM
  OpenRuntime.efi                  → Enabled = true     ← keep (required by OC runtime, independent of emulation)

NVRAM:
  LegacyOverwrite                  → false              ← don't wipe firmware Boot vars

Kernel:Add:
  HibernationFixup.kext            → Enabled = true     ← prevents mode 25→0 downgrade

Misc:Boot:
  HibernateMode                    = NVRAM
Booter:Quirks:
  DiscardHibernateMap              = true
UEFI:ReservedMemory:
  Address=569344, Size=4096, Type=RuntimeCode
```

```bash
sudo pmset restoredefaults
sudo pmset -a hibernatemode 25
```

### What I deliberately do NOT recommend changing

A side-analysis suggested reverting `agdpmod=ignore → agdpmod=vit9696`. **DO NOT make that change** — `project_hackintosh_external_display_fix.md` (RESOLVED 2026-05-06) documents `agdpmod=ignore` as load-bearing for external display; `vit9696` re-breaks it. If hibernate-25 truly requires `vit9696`, there's a genuine trade-off, but the May-2 success was on the pre-2026-05-06 config — at that point `vit9696` happened to be set because the external-display fix didn't exist yet, not because hibernate needs it. Treat agdpmod as orthogonal until proven otherwise.

The same side-analysis suggested restoring `-noDC9`. `S3-sleep-debug-2026-05-07.md` Phase 6 explicitly **falsifies** `-noDC9` as useful on this hardware. Skip.

`darkwake=0` and `forceRenderStandby=0` were on the May-2 boot-args and are not in any "do not enable" memory. Optional — try them only after the four ACPI items + NVRAM revert are confirmed insufficient on their own.

### Status as of 2026-05-08 23:30

- Config edits already applied: `OpenVariableRuntimeDxe.efi=false`, `LegacyOverwrite=false`, `HibernationFixup.kext=true` ✅
- Config edits **still pending**: re-enable `_PTS to ZPTS` patch, `_WAK to ZWAK` patch, `SSDT-PTSWAKTTS-iGPU.aml`, `SSDT-EXT4-iGPU-Wake.aml`
- End-to-end verification still pending. **Test only after the four ACPI items are re-enabled** — testing the current half-fix (NVRAM revert without ACPI revert) wastes a cycle and produces an ambiguous result.

---

## Earlier TL;DR — FALSIFIED 2026-05-08 evening

The mid-day TL;DR recommended `standby=0` + `tcpkeepalive=1` + `hibernatemode=25`. Sleep tests at 16:09→16:41 and 20:22→20:58 with that exact config both failed identically — `pmset` log recorded `hibmode=0 standbydelaylow=0 standbydelayhigh=0` and `Failure: 0x002A001F`. Cold-boot OC log showed `boot-image is 0 bytes - Not Found`. The pmset knobs were never the load-bearing variable. Kept here as audit trail; superseded by the NVRAM-emulation finding above.

```bash
# DO NOT USE (falsified)
sudo pmset -a hibernatemode 25
sudo pmset -a standby       0
```

`standby=0` is not harmful, but it's not the fix either. May-2 successes ran with `standby=1` (verified from HibernateStats lines: `standbydelaylow=10800 standbydelayhigh=86400`).

---

## Side-by-side log comparison tables (added 2026-05-09 00:30)

Two pairwise log comparisons isolate the regressions cleanly. Both tables share most of the same `OC:` initialization phase since OC's early-init flow is identical for cold-boot and resume — divergence only happens at the hibernate-detection step.

### Table 1 — Cold-boot vs cold-boot (May-2 14:51 vs May-8 22:35)

Controls for "what was the long-running config" delta. Both boots are pre-hibernate, no resume artifacts.

| Check | May-2 14:51 (`145105`) | May-8 22:35 (`143529`) |
|---|---|---|
| Boot type | Cold boot | Cold boot |
| Config size | 97,424 bytes | 101,072 bytes |
| Driver count | **3** | **4** (+ `OpenVariableRuntimeDxe.efi`) |
| NVRAM probe | `Locate emulated NVRAM protocol - Not Found` | `Found FW NVRAM, forcing redirect 1` |
| NVRAM init | (no extra steps) | `Loading NVRAM from storage...` + `Restoring FW NVRAM...` |
| NVRAM ops | `ignored, exists` (no-op, vars match) | `Deleting boot-args - Success` then `Setting - Success` (rewrite) |
| ACPI patches applied | **0,1,2,3,6,7,9,10,11** (9 patches) | **0,1,2,3,6,7,9,10,11** (9 patches — IDENTICAL) |
| `_PTS to ZPTS` (idx 4) | NOT applied | NOT applied |
| `_WAK to ZWAK` (idx 5) | NOT applied | NOT applied |
| `SSDT-PTSWAKTTS-iGPU.aml` | Skipped (disabled) | Skipped (disabled) |
| `SSDT-EXT4-iGPU-Wake.aml` | Not in config (entry didn't exist on May-2) | Skipped (disabled) |
| `boot-image` | 0 bytes - Not Found | 0 bytes - Not Found |

**What this isolates:** the only meaningful long-running-config delta between May-2 morning and May-8 evening is the **NVRAM emulation** (rows for drivers, NVRAM probe, NVRAM init, NVRAM ops). The four ACPI items look identical in both — and both don't have them. So they were *not* part of May-2's baseline config.

### Table 2 — Resume vs cold-boot (May-2 23:58 vs May-8 22:35)

Compares the only known-good hibernate resume against the failure. Captures the *actual* working hibernate config.

| Check | May-2 23:58 (`155856` — working resume) | May-8 22:35 (`143529` — failed → cold) |
|---|---|---|
| Boot type | Hibernate resume | Cold-boot recovery |
| Config size | 97,421 bytes | 101,072 bytes |
| Driver count | 3 | **4** (+ `OpenVariableRuntimeDxe.efi`) |
| NVRAM probe | `Locate emulated NVRAM protocol - Not Found` | `Found FW NVRAM, forcing redirect 1` |
| NVRAM init | (no extra steps) | `Loading NVRAM from storage...` + `Restoring FW NVRAM...` |
| NVRAM ops on boot-args | `ignored, exists` (no-op) | `Deleting - Success` then `Setting - Success` (rewrite) |
| ACPI patches applied | **0,1,2,3,4,5,6,7,9,10,11** (11 patches) | **0,1,2,3,6,7,9,10,11** (9 patches) |
| `_PTS to ZPTS` (idx 4) | ✅ Applied | ❌ Missing |
| `_WAK to ZWAK` (idx 5) | ✅ Applied | ❌ Missing |
| `SSDT-PTSWAKTTS-iGPU.aml` | ✅ Loaded (no skip line) | ❌ Skipping add (disabled) |
| `SSDT-EXT4-iGPU-Wake.aml` | (entry not in May-2 config) | ❌ Skipping add (disabled) |
| `SSDT-NameS3-disable.aml` | Skipped (correctly — re-exposes _S3) | Skipped (same) |
| `OCB: boot-image is N bytes` | **70 bytes - Success** | **0 bytes - Not Found** |
| `OCB: NVRAM hibernation is` | **1 / Success / 44** | **0 / Not Found / 0** |
| `OC: Hibernation activation` | **Success, hibernation wake - yes** | **Not Found, hibernation wake - no** |
| `#[EB\|H:IS]` (boot.efi flag) | **1** (is hibernation) | **0** (not hibernation) |
| boot.efi RT.GV lookups | (succeed silently) | `Err(0xE) <- RT.GV boot-signature` + `boot-image-key` |
| BootOrder/BootNext | `Found BootNext 0082` (Apple set it for resume) | `BootOrder/BootNext are not present or unsupported` |

**What this isolates:** the May-2 resume log shows **both** the four ACPI items active AND no NVRAM emulation. May-8 has neither. The result rows (boot-image, hibernation activation, EB|H:IS, RT.GV lookups, BootOrder) all reflect the downstream effect — hibernate set up correctly on May-2, lost on May-8.

### Reading the two tables together — the hidden timeline

Combining: Table 1 says ACPI items were NOT in the long-running May-2 config. Table 2 says they WERE active during the May-2 evening hibernate. Conclusion: **the user enabled the four ACPI items between 14:51 and the 23:28 sleep**, then hibernate worked. They got disabled again at some later point and weren't re-enabled before the May-8 attempts.

This is actually stronger evidence for those four ACPI items being load-bearing than a static "May-2 always had them" claim would be — it's a deliberate intervention with a measured outcome.

The two regressions stack independently:
- **Regression A (ACPI):** four coupled items (`_PTS to ZPTS`, `_WAK to ZWAK`, `SSDT-PTSWAKTTS-iGPU.aml`, `SSDT-EXT4-iGPU-Wake.aml`) — needed for clean sleep-entry/wake handoff
- **Regression B (NVRAM):** emulated NVRAM driver loaded — loses `boot-image` across hibernate power-off

Both must be reverted for the May-2 working stack to reproduce.

---

## The "instantly back to Windows" mystery — debunked

Symptom: after a failed sleep, power-on lands on the Windows login screen "instantly". Looked uncannily like hibernation crossing OS boundaries.

It isn't. Hibernation files are OS-specific (`/var/vm/sleepimage` for macOS, `hiberfil.sys` for Windows) — neither OS can resume the other's image. The actual cascade:

1. macOS sleep started, configured for hibernate-25, but downgraded to S3 (config trap, see TL;DR).
2. S3 wake failed at firmware (`0x002a001f` — same bug as Phase 6 yesterday).
3. System went down (battery drained, or user power-cut).
4. Cold boot. `BootOrder` in firmware NVRAM is empty (separate issue, see below). OpenCore's blessed-scan registers Windows (T:32) as menu position 1 and macOS (T:2) as position 2.
5. OC picker shows for ~8 sec. Either user clicks Windows or picker timeout selects entry 1. Either way → Windows.
6. **Windows resumed instantly from its own `hiberfil.sys`** (Fast Startup / hybrid shutdown). That's the "instant Windows" feel — it's Windows's own resume path, not anything macOS did.

Reproduced twice on 2026-05-08 (08:53/08:56 morning, 14:07/14:10 afternoon). Identical signature each time.

---

## Forensic toolkit — distinguishing "actually hibernated" from "thought it would"

### OC boot-log markers — the unforgeable signal

`boot-image` and `AppleHibernateState` NVRAM variables are written **by `boot.efi` during the hibernate dump itself**, not from userspace. Their presence on the next boot is unforgeable evidence that boot.efi actually executed the hibernate write path.

| Line in `/Volumes/ESP/opencore-*.txt` | Real hibernate-25 resume | Cold boot (incl. failed-S3 recovery) |
|---|---|---|
| `OCB: boot-image is N bytes` | **70 bytes - Success** | **0 bytes - Not Found** |
| `OCB: NVRAM hibernation is` | **1 / Success / 44** | **0 / Not Found / 0** |
| `OC: Hibernation activation` | **Success, hibernation wake - yes** | **Not Found, hibernation wake - no** |
| `AAPL: #[EB\|H:IS] N` | **1** (×3) | **0** (×3) |
| `AAPL: #[EB\|H:WCK]` | present | absent |
| `AAPL: #[EB\|H:SIG] 0x73696D65` | present (`"sime"` little-endian) | absent |
| `AAPL: #[EB\|H:RDST]` → `RDEND` | present (~7 sec read) | absent |

Reference logs in repo: `opencore-2026-05-02-155856.txt` (known-good resume, `rd=86 ms`) vs `opencore-2026-05-08-005323.txt` (known cold boot after failed sleep).

### Misleading signals — DO NOT use these to distinguish resume from cold boot

These appear in **both** real-resume and cold-boot logs and tell you nothing about whether hibernate fired:

| Marker | Why misleading |
|---|---|
| `OCRTC: Wake log is 0x00 0x00 0 0x00` | All-zero in both cases. Hibernate-25 = full power-off, so resume comes from button press, not RTC alarm — wake log is naturally empty, same as a cold boot. |
| `OC: Translated HibernateMode NVRAM to 2` | Just config readout. NVRAM `IOHibernateMode` persists across reboots after `pmset -a hibernatemode 25` — a stale value the kernel wrote some prior boot. Means hibernate-25 was **configured**, not that it **happened**. |
| `OC: RequestBootVarRouting 1` | Boot-var routing is on for every boot; not resume-specific. |

I burned cycles on this earlier in the session. The `OCRTC` wake-log misreading sent me down a "cold boot, never tried to hibernate" path that was wrong. Use the `boot-image is N bytes - Success` line as the load-bearing test, nothing else.

### macOS `.diag` files — secondary confirmation

`/Library/Logs/DiagnosticReports/Sleep Wake Failure_*.diag` are auto-emitted ~30 sec after boot when `swd` detects the previous cycle was a failed sleep-wake. The relevant line:

```
Failure code:: 0x00000000 0x002a001f
"Sleep Wake failure in EFI"
```

`0x002a001f` = "EFI/Bootrom Failure after last point of entry to sleep" = the same firmware S3 wake bug documented in `S3-sleep-debug-2026-05-07.md`. macOS itself classifies these as **EFI failures**, not kernel/hibernate failures — confirming sleep was executed as S3 (not S4) and the firmware wake failed, not the kernel hibernate path.

### `pmset -g log | grep HibernateStats`

Format: `hibmode=N standbydelaylow=L standbydelayhigh=H rd=NN ms`

- `hibmode=25` and **`rd=NN ms`** present → real hibernate-25 RESUME succeeded, dump was `NN`-millisecond read.
- `hibmode=25` with **empty** `rd=` column → mode was configured but no sleepimage was written/read this cycle.
- `hibmode=0` with empty `rd=` → cycle was pure S3 (no hibernate write attempted).

The full HibernateStats history on this machine shows the only `rd=NN` entries ever are the 2026-05-02 `rd=86 ms` and 2026-05-03 `rd=95 ms` rows.

### `/var/vm/sleepimage` size — partially diagnostic

- **Exactly 1073741824 bytes (1 GiB) = pre-allocation only**. macOS sets the file to `Hibernate File Min` whenever pmset settings change. Doesn't prove hibernate ran.
- **Approximately wired-RAM size (≈ 8 GiB on this machine)** = looks like a real dump. Still not conclusive on its own — combine with the OC log markers above.

---

## Why hibernate-25 silently downgrades to S3

Three independent causes, any one of which kills the dump:

### 1. `Standby Enabled = 1` overrides `hibernatemode = 25`

On modern macOS (post-Catalina), the Standby framework wraps the legacy `hibernatemode`:

```
Standby = 1 + hibernatemode = 25
  ↓
sleepnow → S3 immediately (NO sleepimage write yet)
  ↓ wait standbydelay (10800 sec = 3 hours)
Standby Transition → kernel wakes briefly → write sleepimage → power off
```

But on this hardware **every wake-from-S3 fails at firmware** (`0x002a001f`), including the kernel's internal "wake briefly" step. So the standby transition never happens; the dump never gets written; battery eventually drains; cold-reboot recovery.

**Fix: `pmset -a standby 0`** — bypasses the Standby framework, sleep goes straight to "write dump, power off" without ever touching S3.

### 2. ~~`TCPKeepAlive = 1` holds the kernel awake~~ — **FALSIFIED 2026-05-08**

Earlier hypothesis: TCPKeepAlive forces DarkWake, blocking hibernate-25 even when configured. **This is wrong on Spin 5.**

`tcpkeepalive=1` is what *protects* AC sleep by keeping it in DarkWake (DC6, kernel alive, ~3-8 W) — the working AC-sleep state documented in `S3-sleep-debug-2026-05-07.md`. Setting it to 0 drops AC idle sleep into real S3 (DC9, kernel suspended) and hits the firmware `0x002a001f` wake bug. **Keep `tcpkeepalive=1`.**

The hibernate-25 path is **not** reached by tweaking TCPKeepAlive on AC — idle sleep on AC at 100% charge will always go to DarkWake by Apple's policy, regardless of `hibernatemode`. Hibernate-25 is reached by explicit `pmset sleepnow` on **battery**, where DarkWakeBackgroundTasks=No takes the DarkWake-hold off the table.

### 3. `pmset` prefs file `rm` ordering trap

`/Library/Preferences/com.apple.PowerManagement.plist` and `/Library/Preferences/com.apple.PowerManagement.<UUID>.plist` (per-host) both store hibernate config. The `*` glob `rm /Library/Preferences/com.apple.PowerManagement*` wipes both.

If the user runs `pmset -a hibernatemode 25` and **then** `rm /Library/Preferences/com.apple.PowerManagement*`, hibernatemode reverts to whatever the kernel default is (observed: `0` on this hardware = pure S3, no hibernate). Subsequent `pmset hibernatefile /var/vm/sleepimage` only writes the hibernatefile path field — does NOT restore hibernatemode.

**Rule: any `rm` of the prefs files must come BEFORE the final `pmset -a hibernatemode 25`, not after.** Got bitten by this twice in this session (2026-05-07 23:57 cycle and 2026-05-08 00:09 cycle).

### Correct sequence (corrected 2026-05-08 15:40)

```bash
sudo pkill -9 caffeinate 2>/dev/null
sudo rm -f /Library/Preferences/com.apple.PowerManagement*    # wipe FIRST
sudo rm -f /var/vm/sleepimage
sudo pmset hibernatefile /var/vm/sleepimage
sudo pmset -a hibernatemode 25
sudo pmset -a standby       0     # the missing piece for years
# DO NOT set tcpkeepalive=0 — it breaks AC DarkWake (see Falsification)
pmset -g | grep -E "hibernatemode|hibernatefile|standby|tcpkeep"
# expect: hibernatemode 25, standby 0, tcpkeepalive 1
# Then unplug AC (idle sleep on AC always DarkWakes — never hibernates)
sudo pmset sleepnow
```

---

## Falsification — `tcpkeepalive=0` advice was WRONG (2026-05-08 15:16)

After committing the original 3-knob recipe earlier today, I applied the config (`standby=0`, `tcpkeepalive=0`, `hibernatemode=25`) and observed:

- **15:16:01** — idle sleep entered. pmset log: `'Idle Sleep':TCPKeepAlive=disabled Using AC (Charge:100%)`. So `tcpkeepalive=0` *did* take effect — kernel did not hold DarkWake.
- **15:32:30** — cold-recovery boot (uptime resets). User had to power-cycle.
- **15:32:46** — pmset log: `Failure during sleep: 0x002A001F : EFI/Bootrom Failure after last point of entry to sleep`. **Same firmware S3 wake bug as Phase 4/6.**
- HibernateStats for the cycle: `hibmode=0 standbydelaylow=0 standbydelayhigh=0  0` — empty `rd=`, no sleepimage written.

**Mechanism:**
- With `tcpkeepalive=1`: AC idle sleep is held in DarkWake (kernel alive, safe wake). Working state.
- With `tcpkeepalive=0`: no DarkWake hold → AC idle sleep falls through to real S3 → firmware wake bug → cold reboot.
- The hibernate-25 path was **never reached**, because idle sleep on AC at 100% charge does not trigger hibernate at all. The kernel chooses DarkWake by Apple's policy, regardless of `hibernatemode`.

**Lesson:** `tcpkeepalive` is not a hibernate-25 enabler — it's a DarkWake-stability lever. Setting it to 0 on Spin 5 *worsens* AC sleep (drops a documented-working DarkWake state into the broken-S3 path) without doing anything for hibernate. Hibernate-25 testing requires explicit `pmset sleepnow` on **battery**, with `tcpkeepalive` left at default 1 and `standby=0`.

The TL;DR and "Correct sequence" sections above have been corrected.

---

## ACPI patches + SSDTs that are missing (added 2026-05-08 23:30)

Direct evidence from the side-by-side log comparison:

```
May-2 ACPI patches applied (12 patches at indices 0,1,2,3,4,5,6,7,9,10,11):
  00:687  OC: Applying 5 byte ACPI patch (_PTS to ZPTS) at 4
  00:741  OC: Applying 5 byte ACPI patch (_WAK to ZWAK) at 5

May-8 ACPI patches applied (9 patches — indices 4 and 5 absent):
  01:024  ... patch (change _OSI to XOSI) at 3
  01:136  ... patch (GPRW to XPRW) at 6      ← jumps from 3 to 6
```

```
May-8 explicit SSDT skips (file present in EFI/OC/ACPI/, but Enabled=false):
  01:676  OC: Skipping add ACPI SSDT-EXT4-iGPU-Wake.aml (0)
  01:711  OC: Skipping add ACPI SSDT-PTSWAKTTS-iGPU.aml (0)
```

These four ACPI items were disabled earlier in the 2026-05-07 → 2026-05-08 sessions (likely as part of the S3-vs-DarkWake work, but the rationale wasn't documented in the running notes). I missed re-evaluating them when scoping the hibernate-25 regression. The side-by-side analysis credit goes to a fresh look-back at the diff that I should have done myself instead of trusting the diff's text summary.

## The actual smoking gun — NVRAM emulation (added 2026-05-08 23:00)

Side-by-side OC boot log comparison:

```
May-2 23:58 (rd=86 ms — known-good resume):
  00:036  OCVAR: Locate emulated NVRAM protocol - Not Found    ← real firmware NVRAM
  01:549  OCB: boot-image is 70 bytes - Success
  01:551  OCB: NVRAM hibernation is 1 / Success / 44
  01:554  OC: Hibernation activation - Success, hibernation wake - yes
  01:576  OCB: Found BootNext 0082 of type 2

May-8 22:35 (today's failure with full pmset/kext recipe in place):
  02:539  OC: Translated HibernateMode NVRAM to 2
  02:543  OCB: boot-image is 0 bytes - Not Found
  02:546  OCB: NVRAM hibernation is 0 / Not Found / 0
  02:550  OC: Hibernation activation - Not Found, hibernation wake - no
  02:596  OCB: BootOrder/BootNext are not present or unsupported 0 0
```

Same code path, same boot args, same SMBIOS. The only difference: May-2 used real firmware NVRAM; May-8 had emulated NVRAM loaded. The `OCVAR: Locate emulated NVRAM protocol - Not Found` line on May-2 is the canary — when present, OC reads/writes the firmware's actual NVRAM, which survives S5/hibernate naturally. When absent (i.e. emulated NVRAM driver is loaded), all writes go through OC's RAM-resident store, and the launchd persistence path fails to capture hibernate-time writes.

### Why the launchd daemon doesn't help

`/var/log/org.acidanthera.nvramhook.launchd/launchd.log` shows the daemon's actual lifecycle:
- At each boot: daemon starts, mounts ESP, "touches" `nvram.plist` (mtime stamp), unmounts, waits.
- On shutdown signal: daemon mounts ESP, dumps current NVRAM state to `nvram.plist`, unmounts.
- During hibernate: **no log activity at all.** Between the previous boot's "Running…" line and the next boot's "Daemon Starting", the entire sleep-and-failed-wake cycle produced zero daemon entries.

This isn't broken code — it's the design. `launchd` sends `SIGTERM` on graceful shutdown (`shutdown -h now`, halt). Hibernate-25 freezes userspace via `SIGSTOP` and the kernel handles the dump directly; no `SIGTERM`, no daemon flush. A fix would require hooking IOPMrootDomain's *will-sleep* notification (which fires before processes freeze), but that's an upstream feature request, not a config change.

### Other secondary fallout from emulated NVRAM

- `boot-image` is in Apple's `7C436110-AB2A-4BBB-A880-FE41995C9F82` GUID. With `LegacySchema` not covering it, OC's emulated store doesn't even attempt to persist it on shutdown — and even if it did, the daemon-doesn't-fire-on-hibernate problem above would still kill it.
- `BootOrder` / `BootNext` (which `boot.efi` *also* sets during hibernate) live in `8BE4DF61-93CA-11D2-AA0D-00E098032B8C`. With `LegacyOverwrite=true`, OC actively wipes any firmware Boot vars not in `nvram.plist` on every boot — so even if the daemon *had* persisted them, OC would clear them on the next boot. (`LegacyOverwrite=false` fixes this half independently.)

### Reverting cleanly

```
UEFI:Drivers:OpenVariableRuntimeDxe.efi  Enabled = false
NVRAM:LegacyOverwrite                    false
# Keep OpenRuntime.efi enabled — required for OC runtime services, independent of NVRAM emulation.
```

Validation (post-edit, before reboot):
```
plutil -lint /Volumes/ESP/EFI/OC/config.plist
# expect: OK

PlistBuddy -c 'Print :UEFI:Drivers' /Volumes/ESP/EFI/OC/config.plist | grep -E "Path|Enabled"
# expect: OpenVariableRuntimeDxe.efi  Enabled = false
#         OpenRuntime.efi             Enabled = true

PlistBuddy -c 'Print :NVRAM:LegacyOverwrite' /Volumes/ESP/EFI/OC/config.plist
# expect: false
```

Validation (post-reboot, in new OC log):
```
grep -a 'OCVAR: Locate emulated NVRAM' /Volumes/ESP/opencore-*.txt | tail -1
# expect: Locate emulated NVRAM protocol - Not Found
```

The launchd plists can stay where they are; once emulated NVRAM is off they're inert. Removing them is a 5-line cleanup task with `launchctl bootout` + `rm` of `/Library/LaunchDaemons/org.acidanthera.nvramhook.*` — defer if you want.

**DO NOT remove the launchd hook while emulated NVRAM is still on.** That combination is strictly worse than current state — it leaves OC with an emulated store that nobody persists, so every reboot wipes everything.

---

## BootOrder corruption — new theory: our own fix is the cause

**The "macOS hibernate write corrupts firmware NVRAM and wipes BIOS boot entries" complaint** that motivated setting up emulated NVRAM may not actually be a hibernate-write bug at all.

Current observation: every cold boot (including ones with no preceding hibernate cycle) shows:

```
OCB: BootOrder/BootNext are not present or unsupported 0 0
```

i.e. `BootOrder` in firmware NVRAM is empty. OpenCore's blessed-scan then registers Windows + macOS, picker shows, Windows is at menu position 1.

Inspecting `/Volumes/ESP/NVRAM/nvram.plist` — the file emulated NVRAM saves on shutdown — the EFI Global Variable container (`8BE4DF61-93CA-11D2-AA0D-00E098032B8C`) is **empty**. With `LegacyOverwrite=true` and `LegacySchema` covering Boot variables, OpenCore wipes any firmware Boot vars not present in `nvram.plist` on every boot. Since `nvram.plist` has no Boot entries, OpenCore wipes BootOrder cleanly each cold boot.

**So our emulation-based "fix" for the original BootOrder-wipe issue is itself wiping BootOrder.** Different mechanism, same outcome.

Two possible remediations (untested):

- **Option A**: flip `LegacyOverwrite` to `false`. Lets OpenCore *merge* nvram.plist into firmware NVRAM instead of overwriting absent variables to empty. Tradeoff: less defensive against actual NVRAM corruption.
- **Option B**: pre-populate `nvram.plist` with a stable BootOrder (just the OpenCore entry, e.g. `0001 → \EFI\OC\OpenCore.efi`). Deterministic on every boot.

Defer until hibernate-25 is reliably working. If that reliably works without firmware-write side effects, the original "macOS-hibernate-corrupts-NVRAM" theory may be falsified entirely, and emulated NVRAM may be unneeded.

---

## Diagnostic gold — `nvram` direct GUID query

Apple's `nvram` userland tool defaults to showing only the Apple GUID (`7C436110...`). To inspect EFI Global Variables (where `BootOrder`, `Boot####` live) requires explicit GUID:

```bash
nvram 8BE4DF61-93CA-11D2-AA0D-00E098032B8C:BootOrder
nvram 8BE4DF61-93CA-11D2-AA0D-00E098032B8C:BootCurrent
nvram 8BE4DF61-93CA-11D2-AA0D-00E098032B8C:Boot0000
```

Returns "iokit/common: data was not found" if the variable is absent — useful to confirm the BootOrder-empty state.

---

## Outstanding questions

1. **End-to-end verification of the full May-2 stack** — pending. Required edits (the four ACPI items + the NVRAM revert + HibernationFixup + jlempen pmset defaults). Test only after **all four** ACPI items are re-enabled — testing partial reverts produces ambiguous results that can't distinguish the two regression classes.

2. **Power-button LED state on this hardware** — unconfirmed. Convention: pulsing/breathing white = S3, fully off = S4 / power-off. Worth observing during the next hibernate-25 test to add an external verification signal.

3. **Whether the BootOrder-wipe issue resolves automatically once emulated NVRAM is off.** If firmware-NVRAM `BootOrder` survives across reboots/hibernates with the new config, the original "macOS hibernate corrupts NVRAM" theory that motivated emulated NVRAM in the first place is fully falsified.

4. **launchd hook cleanup** — optional, deferred. Vestigial once emulated NVRAM is off. Uninstall sequence documented in "Reverting cleanly" above.

---

## Reference

- `S3-sleep-debug-2026-05-07.md` — companion doc; firmware S3 wake bug context.
- `opencore-2026-05-02-155856.txt` (in ESP / `~/Desktop/MAY3/logs/`) — known-good hibernate-25 resume log. Use as the ground-truth shape of "actually hibernated".
- `opencore-2026-05-08-005323.txt` — known cold boot after failed sleep. Compare side-by-side with the May 2 log.
- `/Library/Logs/DiagnosticReports/Sleep Wake Failure_*.diag` — macOS's own autopsy of failed cycles.
- `pmset -g log | grep HibernateStats` — the `rd=NN ms` column is the only field that tells you a real hibernate-25 resume happened.
