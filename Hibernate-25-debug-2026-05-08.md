# Hibernate-25 (S4) debug — session 2026-05-07 → 2026-05-08

Goal: get hibernate-25 (suspend-to-disk, mode 25) working reliably on Acer Spin 5 SP513-54N (Ice Lake i7-1065G7, MacBookPro16,2 SMBIOS, Sonoma 14.8.5). Real S3 wake is firmware-broken (see `S3-sleep-debug-2026-05-07.md`); hibernate-25 is the alternative because it bypasses the firmware S3 wake bug entirely (full power-off → cold-boot resume from sleepimage via `boot.efi`).

Prior context: hibernate-25 has only succeeded **twice ever** on this machine — 2026-05-02 23:59:06 (`rd=86 ms`) and 2026-05-03 00:33:04 (`rd=95 ms`). Every other attempt either silently downgraded to S3 or failed without writing a sleepimage. This session's goal was to understand *why*.

---

## TL;DR — three pmset knobs are load-bearing

For hibernate-25 to actually fire, **all three** must be set this way before sleep:

```bash
sudo pmset -a hibernatemode 25     # config full hibernate
sudo pmset -a standby       0     # disable Standby framework — no S3 first
sudo pmset -a tcpkeepalive  0     # no DarkWake hold preventing power-off
```

Missing any one silently downgrades the sleep to S3, hits the firmware S3 wake bug (`0x002a001f`), and ends in cold-reboot recovery. Most past failures were `standby=1` overriding `hibernatemode=25` to mean "S3 first, transition to hibernate after `standbydelay=10800` sec" — but the firmware S3 wake bug kills any wake-from-S3, including the kernel's internal "wake briefly to write sleepimage" step at standby transition.

**Status as of 2026-05-08 14:30**: config is finally clean (above 3 settings + 8 GB sleepimage pre-allocated). End-to-end hibernate-25 test still pending.

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

### 2. `TCPKeepAlive = 1` holds the kernel awake

With TCP-keep-alive on, the kernel maintains DarkWake to keep network sockets alive. Hibernate is incompatible with this — full power-off can't keep TCP sessions. Kernel falls back to S3 even when mode=25 is set.

**Fix: `pmset -a tcpkeepalive 0`** — releases the DarkWake hold.

### 3. `pmset` prefs file `rm` ordering trap

`/Library/Preferences/com.apple.PowerManagement.plist` and `/Library/Preferences/com.apple.PowerManagement.<UUID>.plist` (per-host) both store hibernate config. The `*` glob `rm /Library/Preferences/com.apple.PowerManagement*` wipes both.

If the user runs `pmset -a hibernatemode 25` and **then** `rm /Library/Preferences/com.apple.PowerManagement*`, hibernatemode reverts to whatever the kernel default is (observed: `0` on this hardware = pure S3, no hibernate). Subsequent `pmset hibernatefile /var/vm/sleepimage` only writes the hibernatefile path field — does NOT restore hibernatemode.

**Rule: any `rm` of the prefs files must come BEFORE the final `pmset -a hibernatemode 25`, not after.** Got bitten by this twice in this session (2026-05-07 23:57 cycle and 2026-05-08 00:09 cycle).

### Correct sequence

```bash
sudo pkill -9 caffeinate 2>/dev/null
sudo rm -f /Library/Preferences/com.apple.PowerManagement*    # wipe FIRST
sudo rm -f /var/vm/sleepimage
sudo pmset hibernatefile /var/vm/sleepimage
sudo pmset -a hibernatemode 25
sudo pmset -a standby       0     # the missing piece for years
sudo pmset -a tcpkeepalive  0     # the other missing piece
pmset -g | grep -E "hibernatemode|hibernatefile|standby|tcpkeep"
# expect: hibernatemode 25, standby 0, tcpkeepalive 0
sudo pmset sleepnow
```

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

1. **End-to-end hibernate-25 test on the corrected config** — pending. With `standby=0` + `tcpkeepalive=0` + `hibernatemode=25`, does the dump finalize? Does resume work? Does the firmware survive the post-resume NVRAM state?

2. **Power-button LED state on this hardware** — unconfirmed. Convention: pulsing/breathing white = S3, fully off = S4 / power-off. Worth observing during the next hibernate-25 test to add an external verification signal.

3. **Whether the BootOrder-wipe is hibernate-related at all**, vs. the LegacyOverwrite-emulation artifact above. Once we've run hibernate-25 successfully without LegacyOverwrite, we'll know.

---

## Reference

- `S3-sleep-debug-2026-05-07.md` — companion doc; firmware S3 wake bug context.
- `opencore-2026-05-02-155856.txt` (in ESP / `~/Desktop/MAY3/logs/`) — known-good hibernate-25 resume log. Use as the ground-truth shape of "actually hibernated".
- `opencore-2026-05-08-005323.txt` — known cold boot after failed sleep. Compare side-by-side with the May 2 log.
- `/Library/Logs/DiagnosticReports/Sleep Wake Failure_*.diag` — macOS's own autopsy of failed cycles.
- `pmset -g log | grep HibernateStats` — the `rd=NN ms` column is the only field that tells you a real hibernate-25 resume happened.
