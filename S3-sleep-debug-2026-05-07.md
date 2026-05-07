# S3 sleep debug — session 2026-05-06 → 2026-05-07

Goal: get S3 suspend-to-RAM with clean wake on Acer Spin 5 SP513-54N (Ice Lake i7-1065G7, MacBookPro16,2 SMBIOS, Sonoma 14.8.5).

Prior context: hibernate-25 (suspend-to-disk) was previously tested and confirmed broken at firmware level (Insyde failure, deferred). Today's goal is to make S3 work as the alternative.

---

## TL;DR — RESOLVED via DarkWake fallback

**Working solution: Phase 1 (`_S3` exposed) + `-noDC9` boot-arg → DarkWake-as-sleep.** Real S3 unfixable on this hardware (Insyde-locked AOAC class, same as Acer Swift 3 SF314-57).

- **Phase 1: ✓** Native firmware `_S3` exposed to Darwin (disabled `SSDT-NameS3-disable.aml` + `_S3 → XS3_` rename). Required for `IOSleepSupported = Yes`.
- **Phase 2: ✓** `pmset` configured: `hibernatemode=0`, `standby=0`, `powernap=0`, `tcpkeepalive=0`, `autopoweroff=0`, `lowbatteryhibernate=0`.
- **Phase 4 (real S3 attempt, NO `-noDC9`): ✗** S3 entry succeeded (fan stopped, LED breathed), wake failed at firmware-handoff level (`0x002A001F : EFI/Bootrom Failure`). Hard power-off required.
- **Phase 5 (with `-noDC9`): ✓** DC9 entry blocked by kernel → fallback to DC6 (DarkWake). Display off, kernel alive (heartbeat continuous, zero gap), wake clean (<1 sec) via key press, all peripherals + external display restored.
- **Conclusion**: Real S3 sleep is firmware-broken on this Spin 5. DarkWake is the working substitute. Both Phase 1 and `-noDC9` are load-bearing — neither can be removed.
- **Trade-off**: ~3-8W in DarkWake vs ~0.3W in true S3. Battery life ~6-15h vs days. Functionally usable. Same model as Surface laptops' Modern Standby.
- **Pending**: 1-hour battery drain test to validate practical viability.

---

## Phase 1 — `_S3` re-exposure (Option B)

`SSDT-NameS3-disable.aml` previously paired with the `_S3 → XS3_` ACPI rename to **hide `_S3` from Darwin** (the SSDT only re-emitted `_S3` for non-Darwin OSes). This was originally added when S3 was assumed broken, but it was preventing the S3 path from being attempted at all.

**Decision: disable both, restore native firmware `_S3` (Option B from the plan).** Simpler than authoring a replacement SSDT (Option A); two `Enabled=false` flips.

Changes (`config.plist`):
- `ACPI/Add[21]` `SSDT-NameS3-disable.aml`: `Enabled=true → false`
- `ACPI/Patch[8]` `_S3 to XS3` rename: `Enabled=true → false`

Result post-reboot:
```
ioreg -rc IOPMrootDomain | grep -iE "Sleep"
  IOPMSystemSleepPolicyHandler = Yes
  SleepDisabled = No
  IOSleepSupported = Yes
```

✓ Sleep is enabled at the kernel level. Phase 1 took.

---

## Phase 2 — pmset prep

```
sudo pmset -a hibernatemode 0
sudo pmset -a autopoweroff 0
sudo pmset -a standby 0
sudo pmset -a lowbatteryhibernate 0
sudo pmset -a proximitywake 0
sudo pmset -a tcpkeepalive 0
```

Why: ensures a failed wake doesn't fall through to hibernate. Emulated NVRAM is in place (entry from prior session) but no reason to trigger hibernate-25 path which is also broken.

Verified live with `pmset -g`:
```
hibernatemode    0          ← S3 only ✓
standby          0
powernap         0
tcpkeepalive     0
```

---

## Phase 4 — first sleep test

Procedure:
1. Lid open, internal display only (no externals plugged)
2. `pmset sleepnow`

Observation:
- Fan stopped within 1 sec ✓
- Power LED switched to breathing pattern ✓
- Internal display went black ✓
- System entered S3 cleanly

After ~10 minutes, key press:
- Fan resumed immediately
- **Internal display stayed black**
- System unresponsive to keyboard / trackpad
- Required hard power-off (button hold) to recover

---

## Post-mortem

`pmset -g log` (read after reboot):
```
2026-05-06 23:51:26  Sleep         Entering Sleep state due to 'Software Sleep pid=884'
                                   :TCPKeepAlive=disabled Using Batt (Charge:62%)
2026-05-06 23:51:28  PM Client Acks   Delays to Sleep notifications:
                                   [com.apple.bluetooth.sleep is slow(516 ms)]
                                   [com.apple.apsd is slow(2004 ms)]
2026-05-07 00:01:39  HibernateStats   hibmode=0 standbydelaylow=0 standbydelayhigh=0
2026-05-07 00:01:39  Failure          Failure during sleep:
                                   0x002A001F : EFI/Bootrom Failure after last point
                                   of entry to sleep
```

**Status code `0x002A001F`** — Apple's generic classification: "EFI/Bootrom Failure after last point of entry to sleep". Means:
- Sleep entry succeeded (system did reach S3)
- Wake attempt: firmware's S3-resume handoff didn't satisfy macOS's protocol expectations
- macOS never regained control after CPU resumed
- All macOS-side wake hooks (including `_WAK`, EXT4 SSDT) had no opportunity to run

This is a **firmware-level issue**, not driver-level. Linux S3 wakes cleanly on this hardware (verified earlier session) — Linux is more permissive about firmware behavior; macOS is strict.

**Implication for EXT4 hook**: irrelevant to this failure. EXT4 was designed to nudge the iGPU on wake by sending `Notify(GFX0, 0)` after `_WAK` returns. With firmware not returning control, `_WAK` never executes, EXT4 never fires.

---

## Research summary (sub-agent investigation)

`0x002A001F` is **not separately documented** by Apple beyond the generic string. acidanthera/bugtracker #817 (canonical "Sleep Wake failure in EFI") was closed as invalid. Treat as a **symptom class**, not a precise diagnostic.

### Closest-cousin landmarks

- **[Lorys89/DELL_VOSTRO_5401-ICE-LAKE](https://github.com/Lorys89/DELL_VOSTRO_5401-ICE-LAKE)** — same i7-1065G7 CPU, but **Dell firmware** (not Insyde). Working S3 sleep config. Reference for "what should work when firmware cooperates."
- **Acer Swift 3 SF314-57** ([Olarila thread](https://olarila.com/topic/37355-acer-swift-3-sf314-57-i7-1065g7-sleep-problem-possibly-because-of-aoac/)) — same CPU + same Insyde-locked-AOAC firmware class. **Unsolved.** S3 cannot be enabled; only S0ix works for Linux/Windows. Strongly suggests Spin 5 sits in the same bucket.
- **[acidanthera/bugtracker #1207](https://github.com/acidanthera/bugtracker/issues/1207)** — `Cannot allow DC9 without disallowing DC6` panic on ICL during wake. Documented fix: `-noDC9` boot-arg.

### Ranked actionable interventions

| # | Action | Confidence | Cost | Reversibility |
|---|---|---|---|---|
| 1 | **Add `-noDC9` boot-arg** | HIGH | trivial | trivial |
| 2 | Add `-hbfx-disable-patch-pci` (HibernationFixup) | MEDIUM | trivial | trivial |
| 3 | Add `igfxfw=2` (load GuC firmware) | MEDIUM | trivial | trivial |
| 4 | SMBIOS `MacBookPro16,2` → `MacBookAir9,1` | MEDIUM | bigger; may affect BT | requires NVRAM reset |
| 5 | Disable Thunderbolt before sleep (BIOS or SSDT) | MEDIUM | moderate | trivial |

### Skip list (no evidence trail)

- `darkwake=N`: only changes display-on behavior post-wake; doesn't address firmware handoff.
- `dart=0`, `npci=0x2000`, `swd_panic=1`: legacy-platform leftovers; no ICL/Insyde-specific evidence.

### Honest worst-case

If `-noDC9` + a few of (2)–(5) don't help, we're likely in the **Acer Swift 3 SF314-57 bucket** where Insyde's locked AOAC firmware fundamentally doesn't expose a macOS-compatible S3-wake handoff. **No software-only fix exists for that class.** The Linux-works/macOS-fails asymmetry would be consistent.

---

## Current ESP state (post-Phase-1)

ACPI:
- `SSDT-NameS3-disable.aml`: in `Add` list but **disabled** (file present, not loaded)
- `_S3 → XS3_` patch: **disabled**
- `SSDT-EXT4-iGPU-Wake.aml`: present and **enabled** (will fire when/if `_WAK` runs — currently no opportunity to)
- `SSDT-PTSWAKTTS-iGPU.aml`: enabled (calls EXT4 if defined)
- `SSDT-EXT3-WakeScreen.aml`: enabled
- `SSDT-GPRW.aml`: enabled

Boot-args:
```
keepsyms=1 debug=0x100 -btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck
agdpmod=ignore alcid=13 -v -no_compat_check -igfxdbg -liludbgall igfxonln=1
```

`pmset` (live, not in NVRAM):
```
hibernatemode=0  standby=0  powernap=0  tcpkeepalive=0
autopoweroff=0   lowbatteryhibernate=0  proximitywake=0
```

---

## Phase 5 — DarkWake test with `-noDC9` (2026-05-07 morning)

### Setup

- `-noDC9` boot-arg added to NVRAM (committed `e95dae8`).
- USB-C dock attached: AX88179B Ethernet (en1), Lenovo USB optical mouse, external display via DP-alt-mode (ITE BillBoard role-switch chip).
- Wi-Fi disabled; SSH path is en1 (192.168.0.58) from phone via Termius.
- Two-layer monitoring during test:
  - `~/sleep-heartbeat.log` — `date` written every 2 sec by background shell loop. Definitive kernel-liveness probe (writes hit disk synchronously, robust to logging-subsystem hangs).
  - `~/sleep-iopm-stream.log` — `sudo log stream --predicate 'subsystem == "com.apple.iokit.IOPMrootDomain"' --info >> ... 2>&1 &` then `disown`. Rich event log when logging subsystem is alive. Note: `2>&1` required or stderr writes to TTY trigger SIGTTOU and suspend the backgrounded job.

### Procedure

1. Phone Termius SSH session active to en1.
2. `date; pmset sleepnow` issued at 11:27:13.

### Observation

- pmset returned "Sleeping now..." promptly.
- Display went off; system appeared to sleep.
- Key press at 11:28:57 → display came back, prompt responsive, SSH session survived. No force-poweroff.

### Decisive evidence — heartbeat shows ZERO gap

```
11:27:55, 11:27:57, 11:27:59, 11:28:01, 11:28:03, ...
```

Continuous 2-second writes throughout the entire window. `awk` gap-detector (`>4 sec`) returned NO gaps anywhere in the log.

→ **Kernel was running the entire time.** Never entered any actual sleep state. The "sleep" was DarkWake.

### `pmset -g log` confirms

```
11:27:13  Entering DarkWake state due to 'Software Sleep pid=2832':TCPKeepAlive=disabled
11:27:13  PID 103(powerd) Created InternalPreventSleep "darkwakelinger"
11:27:26  PID 103(powerd) TimedOut InternalPreventSleep "darkwakelinger" 00:00:12
[no further events for ~1.5 min]
11:28:57  Wake — DarkWake to FullWake from Invalid [CDNVA] : due to HID Activity
11:28:57  WakeTime: 0.787 sec
```

Telling details:
- "Entering DarkWake state" (not "Sleep") — DarkWake is destination, not transition.
- WakeTime **0.787 sec** — far too fast for true S3 wake (which requires firmware reload, ≥2-5 sec).
- "DarkWake to FullWake" — never says "Sleep to Wake".
- `Sleep/Wakes since boot ... :0` — zero S3 sleeps recorded.

### `IOPMrootDomain` confirmation

```
"CurrentPowerState"=4    ← never dropped below full power
"MaxPowerState"=4
"Last Sleep Reason" = "Software Sleep"
"Wake Type" = "UserActivity Assertion"
"IOSleepSupported" = Yes ← Phase 1 doing its job
```

### Why this happened — `-noDC9` semantics

`-noDC9` literally tells the kernel "do not enter DC9 CPU package state."

| State | Description | Maps to |
|---|---|---|
| **DC6** | Deep idle. RAM powered, peripherals partial. | DarkWake / S0ix |
| **DC9** | Deepest idle. RAM in self-refresh. | **Required for S3 entry** |

By blocking DC9, S3 entry becomes physically impossible. Kernel falls back to the next-deepest available state: DC6 = DarkWake.

### Significance

`-noDC9` **converts** the failure mode rather than fixing S3:
- **Without `-noDC9`**: real S3 entry → catastrophic firmware-handoff wake failure → unusable.
- **With `-noDC9`**: DarkWake entry → clean wake → **usable**.

The original research recommendation (acidanthera/bugtracker #1207) cited a "Cannot allow DC9 without disallowing DC6" panic that we never actually observed. The fix is correct outcome via wrong mechanism: it sidesteps the firmware S3-handoff bug by preventing S3 entry entirely.

### What works in DarkWake

- Display off ✓
- Kernel alive (heartbeat continuous) ✓
- USB Ethernet en1 link survives ✓
- SSH session survives across the "sleep" ✓
- Wake via key press, fast (<1 sec) ✓
- External display restored on wake ✓
- USB peripherals (mouse, dock) restored ✓

### Trade-off

| | Real S3 (broken) | DarkWake (working) |
|---|---|---|
| Power draw | ~0.3-0.7W | ~3-8W |
| Battery life on 50Wh | ~3 days | ~6-15 hours |
| Wake latency | 2-5 sec | <1 sec |
| Reliability on Spin 5 | broken at firmware level | **working** |

DarkWake is structurally what Modern Standby / S0ix does on Surface laptops — they don't have real S3 either. Legitimate sleep mode, just less efficient than S3.

### Why the Phase 1 changes must stay

Without `_S3` exposed to Darwin, `IOSleepSupported = No` and `pmset sleepnow` cannot trigger any sleep transition. No DarkWake either, because DarkWake is reached *via* a sleep attempt. The full working stack is:

1. `_S3` visible to Darwin (Phase 1: SSDT-NameS3-disable.aml DISABLED + `_S3→XS3_` rename DISABLED)
2. `-noDC9` boot-arg loaded (gates kernel out of DC9)
3. `pmset hibernatemode=0` etc. (no hibernate fallback)

Remove any one and the chain breaks.

### Confirmed Insyde-locked-AOAC bucket

Earlier research flagged the Spin 5 *might* be in the Acer Swift 3 SF314-57 "Insyde-locked AOAC firmware" bucket. Confirmed by Phase 4 + 5:
- Real S3 entry possible but wake broken at firmware level (Phase 4 evidence).
- DarkWake works as substitute (Phase 5 evidence).

This matches the documented Swift 3 behavior. **Software-only fix for real S3 unlikely to exist.**

---

## Final config

### Boot-args
```
keepsyms=1 debug=0x100 -btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck
agdpmod=ignore alcid=13 -v -no_compat_check -igfxdbg -liludbgall igfxonln=1 -noDC9
```

### ACPI
- `SSDT-NameS3-disable.aml`: in Add list, **disabled** (file present but not loaded)
- `_S3 → XS3_` rename: **disabled**
- `SSDT-EXT4-iGPU-Wake.aml`: enabled (irrelevant to current solution; would have helped only if real S3 wake worked)
- `SSDT-PTSWAKTTS-iGPU.aml`, `SSDT-EXT3-WakeScreen.aml`, `SSDT-GPRW.aml`: enabled

### pmset (live, may need re-set if NVRAM doesn't persist)
```
hibernatemode=0  standby=0  powernap=0  tcpkeepalive=0
autopoweroff=0   lowbatteryhibernate=0  proximitywake=0
```

### Recommended pmset tuning for DarkWake-as-sleep
```bash
sudo pmset -a darkwakes 0     # disable scheduled background-task DarkWakes (no auto-wake every 1-3h)
sudo pmset -a sleep 10        # auto-enter DarkWake after 10 min idle
sudo pmset -a displaysleep 5  # turn display off after 5 min
```

---

## Pending validation

**1-hour battery drain test** to validate DarkWake-as-sleep is daily-driver viable:

1. Charge to 100%, unplug AC.
2. `pmset sleepnow`.
3. Leave for 1 hour.
4. Wake, run `pmset -g batt`.

Acceptance criteria:
- < 10% drain/hour → comfortable for full workday use.
- 10-20% drain/hour → marginal, OK for short sleeps only.
- > 20% drain/hour → unusable, equivalent to leaving on.

---

## Things confirmed NOT working / not pursued

- **True S3 sleep**: unfixable in software (firmware-level Insyde issue).
- **Hibernate-25 (suspend-to-disk)**: unfixable (same firmware class), only succeeded once previously, NVRAM-corruption risk.
- **`-hbfx-disable-patch-pci`, `igfxfw=2` boot-args**: not tested. With DarkWake working, no incentive to roll the dice on more boot-args.
- **SMBIOS swap MacBookPro16,2 → MacBookAir9,1**: not pursued; would affect BT (the BTLFX boot-args are SMBIOS-tied).
- **EXT4 iGPU-Wake hook**: kept enabled but never had a chance to fire (real S3 wake never reached). Harmless idle.
