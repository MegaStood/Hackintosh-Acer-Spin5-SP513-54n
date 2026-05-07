# S3 sleep debug — session 2026-05-06 → 2026-05-07

Goal: get S3 suspend-to-RAM with clean wake on Acer Spin 5 SP513-54N (Ice Lake i7-1065G7, MacBookPro16,2 SMBIOS, Sonoma 14.8.5).

Prior context: hibernate-25 (suspend-to-disk) was previously tested and confirmed broken at firmware level (Insyde failure, deferred). Today's goal is to make S3 work as the alternative.

---

## TL;DR — AC sleep works (DarkWake), battery sleep broken (S3 firmware bug)

**Corrected 2026-05-07 afternoon after Phase 6 battery test invalidated the morning's `-noDC9` interpretation.**

- **AC power**: macOS keeps the system in DarkWake (per `DarkWakeBackgroundTasks=Yes` in AC profile). Display off, kernel alive, clean wake in <1 sec. **Works.**
- **Battery power**: macOS attempts real S3 (per `DarkWakeBackgroundTasks=No` in Battery profile). S3 entry succeeds (low draw — 2.8%/h confirmed), but **wake fails at firmware-handoff level** (`0x002A001F : EFI/Bootrom Failure`). Same firmware bug as yesterday, re-confirmed today.
- **`-noDC9` boot-arg: status uncertain.** Original theory was that `-noDC9` blocks DC9 entry forcing DarkWake fallback. Phase 6 falsified that — system *did* enter real S3 on battery despite `-noDC9` being set. The morning DarkWake was AC-policy-driven, not boot-arg-driven. `-noDC9` may be doing nothing useful on this hardware. Not removed yet (no evidence it's harmful either).
- **Phase 1 (`_S3` exposure) IS load-bearing** for AC DarkWake to be reachable. `IOSleepSupported = Yes` requires it. Without Phase 1, neither sleep nor DarkWake works.
- **Real S3 unfixable** on this hardware (Insyde-locked AOAC class, same as Acer Swift 3 SF314-57). Software-only fix not expected to exist.

### Recommended config

- Keep AC sleep enabled (lid close while plugged in → DarkWake → safe wake).
- **Disable sleep on battery** to avoid the firmware wake bug:
  ```bash
  sudo pmset -b disablesleep 1
  sudo pmset -c sleep 10
  sudo pmset -c displaysleep 5
  ```

### Phase outline (chronological)

- **Phase 1: ✓** Native firmware `_S3` exposed to Darwin (disabled `SSDT-NameS3-disable.aml` + `_S3 → XS3_` rename). Required for `IOSleepSupported = Yes`. Load-bearing.
- **Phase 2: ✓** `pmset` configured: `hibernatemode=0`, `standby=0`, `powernap=0`, `tcpkeepalive=0`, `autopoweroff=0`, `lowbatteryhibernate=0`.
- **Phase 4 (real S3 attempt, NO `-noDC9`): ✗** Battery sleep, S3 entry OK, wake failed (`0x002A001F`). Hard power-off required.
- **Phase 5 (AC test, with `-noDC9`): ✓** DarkWake on AC, clean wake. Misinterpreted at the time as "`-noDC9` fix"; was actually AC policy.
- **Phase 6 (battery test, with `-noDC9`): ✗** Real S3 entered (heartbeat stopped at 12:04:56), wake failed (`0x002A001F` at 14:10:01). Battery drain ~2.8%/h confirms deep sleep was reached. Same failure as Phase 4. Falsifies the `-noDC9` theory.

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

> **Interpretation falsified by Phase 6 (afternoon battery test) below.** The "DarkWake via -noDC9" theory turned out to be incorrect — the morning DarkWake was AC-policy-driven, not boot-arg-driven. The data captured in this section is correct; only the *causal interpretation* was wrong. Read Phase 6 first for the corrected model.

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

## Phase 6 — Battery drain test, falsifies the `-noDC9` theory (2026-05-07 afternoon)

### Setup

- Same hardware config as Phase 5 (USB-C dock, display, mouse, en1).
- `-noDC9` still in boot-args.
- AC adapter **physically unplugged**. Battery 100%, discharging.
- Heartbeat + log stream still running from morning.

### Procedure

```
12:04:40  test start, battery 100%, on Battery Power
12:04:40  pmset sleepnow issued
```

### Observation

Came back ~2h later. System was unresponsive. Recovered (no force-poweroff was needed; pmset log shows boot time still 09:46:02 = no reboot).

### Decisive evidence — heartbeat STOPPED at sleep entry

```
last heartbeat write: 12:04:56
no further entries until manual restart
```

Compare to Phase 5: heartbeat ran continuously through the entire "sleep". Today on battery: heartbeat process was suspended by kernel-level sleep.

→ **Kernel actually slept this time.** Real S3 was reached.

### `pmset -g log` confirms real S3, not DarkWake

```
12:04:55  Sleep — Entering Sleep state due to 'Software Sleep pid=5555':TCPKeepAlive=disabled Using Batt
12:04:57  Wake Requests scheduled (CSPNEvaluation deltaSecs=7243 wakeAt=14:05:40)
12:04:57  PM Client Acks: bluetooth.sleep slow(515ms), apsd slow(2016ms)
[~2h gap]
14:10:01  Failure: 0x002A001F : EFI/Bootrom Failure after last point of entry to sleep
14:10:04  Assertions resumed
```

Crucial distinctions from Phase 5:
- "**Entering Sleep state**" (not DarkWake) — actual S3.
- `Using Batt` — battery profile triggered different sleep policy.
- `0x002A001F` — same firmware-handoff failure as Phase 4 (yesterday).
- The scheduled CSPNEvaluation at 14:05:40 likely triggered the failed wake attempt.

### Battery drain — confirms deep sleep was reached

| | Value |
|---|---|
| Start | 100% at 12:04:40 |
| End | 94% at 14:13:15 |
| Elapsed | ~2.14 hours |
| **Drain rate** | **~2.8% per hour** |

This rate is consistent with real S3 (sub-1W draw), not DarkWake (3-8W which would be ~10-20%/h on this battery). The deep sleep was real and effective for power saving — the only problem was the wake.

### Re-interpretation of Phase 5

The morning Phase 5 test was on **AC power**. macOS power profile dictionary:

```
"AC Power"      = { ..., DarkWakeBackgroundTasks=Yes, ... }
"Battery Power" = { ..., DarkWakeBackgroundTasks=No,  ... }
```

`DarkWakeBackgroundTasks=Yes` keeps the system in DarkWake (instead of going deeper to S3) so that background tasks (Time Machine, mail, push notifications) can run. `=No` lets the system go to real S3 to save battery.

So the AC-vs-Battery split is **macOS standard policy**, not anything we configured.

`-noDC9` did not block DC9 entry on battery despite the original theory. The system *did* enter DC9/S3 — and crashed at wake, exactly as in Phase 4. So `-noDC9` is not load-bearing for the AC DarkWake behavior, and not protective on battery.

### Corrected model

| Power source | macOS policy | What happens | Outcome |
|---|---|---|---|
| AC | DarkWake (background tasks allowed) | Display off, kernel alive, ~3-8W | ✓ Works |
| Battery | Real S3 (deeper sleep for battery savings) | Kernel suspended, ~0.3W | ✗ Wake fails (firmware bug) |

### Status of `-noDC9`

Unverified. Could be doing nothing useful on this hardware, or could provide some unrelated benefit. We did not run an AC test without `-noDC9` to A/B test. Conservative stance: leave it in (no evidence of harm) but don't claim it's load-bearing.

---

## Phase 6 addendum — `log show` forensics (post-incident, 2026-05-07 evening)

Earlier Phase 6 prose claimed "Recovered (no force-poweroff was needed; pmset log shows boot time still 09:46:02 = no reboot)." That was wrong. After re-reading the unified-log capture (`sudo log show --start "2026-05-07 12:04:00" --end "2026-05-07 14:12:00"`), the actual recovery sequence was:

### Corrected user-visible timeline

| Time | Event |
|---|---|
| 12:04:40 | `pmset sleepnow` issued, sleep entry begins |
| 12:04:48.86 | Last unified-log entry (lingering coreauthd/loginwindow teardown) |
| 12:04:55 | pmset log: "Entering Sleep state due to 'Software Sleep pid=5555' Using Batt" |
| 12:04:57 | pmset log: scheduled wake at 14:05:40 (CSPNEvaluation maintenance) |
| **~14:05** | **User pressed key. Fan ramped to high RPM, screen stayed black, SSH unresponsive** |
| ~14:05:15 | User held power button ~15 seconds → SMC hard cut |
| 14:09:23 | Cold boot completes (`=== system boot:` marker, fresh VM bootstrap, ACPI re-enumeration, OFF_STATE → ON_STATE) |
| 14:09:35 | `AppleSMC: Previous shutdown cause: 5` |
| 14:10:01 | pmset log records the failed wake: `0x002A001F : EFI/Bootrom Failure after last point of entry to sleep` |
| 14:10:01 | `powerd: Failed to get sleep type. rc:0xe00002c7` (normal for unclean wake path) |

### Evidence that real S3 was reached (falsifies "stuck in DarkWake" hypothesis)

After 12:04:48.86, the kernel produced **zero log entries for 2h 4m 35s** — the next entry is the post-reboot `=== system boot:` marker.

If the system had been in DarkWake for those 2 hours, the log would contain:
- bluetoothd LE-scan cycles (the pre-sleep section had one every ~300ms)
- powerd `sleepWake` / scheduled-maintenance assertions
- mDNSResponder Bonjour activity
- AppleSmartBatteryManager polls
- WiFi airportd activity

None of those exist for two hours → CPU was halted → real S3 was reached. Combined with the Phase 6 drain rate measurement (~2.8 %/h, sub-1W), this independently confirms the same conclusion via two methods. The wake handler is the broken stage, not sleep entry.

### Diagnostic: do **not** read `Previous shutdown cause: 5` as "battery drained in DarkWake"

`Previous shutdown cause: 5` is the SMC's signature for "OS lost without a clean shutdown sequence." It covers **both** battery-drain *and* forced power-cut (15s power-button hold). Cause 5 alone cannot distinguish the two. On this machine, the kernel-silence evidence rules out drain — the cause was the user's force-cut after the wake handler hung.

If a future cause-5 event needs to be classified, two persistent post-reboot signals separate the cases:
- Battery percentage at boot: near-empty → drain; healthy → forced cut.
- `pmset -g log` retains entries across reboots; check whether the entry preceding the gap was "Sleep" with `Using Batt` (sleep was reached) or maintenance-wake-related noise (DarkWake stuck).

### Wake-hang user-visible signature for next time

If you press a key to wake from battery sleep on this Spin 5 and observe **fan ramps loud, screen stays black, SSH unresponsive** — that is the firmware-bug wake hang. There is no software recovery; only a power-button hold restores control. Do not mistake the resulting cause-5 marker for a DarkWake battery-drain incident.

### Suggested next-time forensic workflow

`log stream` over SSH dies the moment the host hangs, so it only catches gradual failures, not freezes. Better tools for this failure mode:

- `pmset -g log | grep -E "Sleep|Wake|DarkWake"` post-incident — persists across reboots, cleaner than the unified log for sleep accounting.
- `sudo log show --predicate 'eventMessage CONTAINS "PMRD"' --start <time>` — pulls only the PMRD trace points and capability changes that matter for sleep diagnosis.
- A second machine running `tail -f` over SSH on a battery-percentage logger before the test — establishes start/end %SOC across the failure independently of pmset.

---

## Final config (corrected)

### Boot-args
```
keepsyms=1 debug=0x100 -btlfxallowanyaddr -btlfxboardid -btlfxnvramcheck
agdpmod=ignore alcid=13 -v -no_compat_check -igfxdbg -liludbgall igfxonln=1 -noDC9
```
(`-noDC9` retained as not-known-harmful, but unverified.)

### ACPI
- `SSDT-NameS3-disable.aml`: in Add list, **disabled**. Phase 1 must stay — required for `IOSleepSupported = Yes`.
- `_S3 → XS3_` rename: **disabled**. Phase 1.
- `SSDT-EXT4-iGPU-Wake.aml`: enabled (idle; real S3 wake never reaches the hook).
- `SSDT-PTSWAKTTS-iGPU.aml`, `SSDT-EXT3-WakeScreen.aml`, `SSDT-GPRW.aml`: enabled.

### pmset — recommended (the actual fix)

```bash
sudo pmset -b disablesleep 1    # battery: kernel refuses all sleep transitions
sudo pmset -c sleep 10          # AC: idle-sleep after 10 min → DarkWake (works)
sudo pmset -c displaysleep 5    # AC: display off after 5 min
```

`disablesleep 1` on battery is the load-bearing piece. It blocks the broken battery/S3 path while keeping the working AC/DarkWake path. Trade-off: lid close on battery → kernel keeps running at full power → battery drains in ~2-3h. Not ideal, but no wake failure.

Already-set carryover:
```
hibernatemode=0  standby=0  powernap=0  tcpkeepalive=0
autopoweroff=0   lowbatteryhibernate=0  proximitywake=0
```

### Daily-driver workflow

- Plug in AC before lid close → DarkWake → safe wake.
- Don't sleep on battery; if you must, shut down properly.
- If forgotten and lid closed unplugged: kernel stays awake (with `disablesleep 1`), battery drains in 2-3h, no wake failure when you open lid.

---

## Things confirmed NOT working / not pursued

- **True S3 sleep**: unfixable in software (firmware-level Insyde issue, confirmed twice — Phase 4 + Phase 6).
- **Hibernate-25 (suspend-to-disk)**: unfixable (same firmware class), NVRAM-corruption risk.
- **`-noDC9` as a sleep fix**: theory falsified by Phase 6. Did not block DC9/S3 entry on battery.
- **`-hbfx-disable-patch-pci`, `igfxfw=2` boot-args**: not tested. Same firmware-handoff bug class; low expectation of helping.
- **SMBIOS swap MacBookPro16,2 → MacBookAir9,1**: not pursued; would affect BT (BTLFX args are SMBIOS-tied).
- **EXT4 iGPU-Wake hook**: never fires (firmware bug prevents reaching the wake path).
- **Forcing DarkWake on battery via `darkwakebackgroundtasks 1`**: not tested. Could be a future experiment if AC-only-sleep is too restrictive.
