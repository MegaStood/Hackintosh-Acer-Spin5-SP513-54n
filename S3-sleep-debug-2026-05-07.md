# S3 sleep debug — session 2026-05-06 → 2026-05-07

Goal: get S3 suspend-to-RAM with clean wake on Acer Spin 5 SP513-54N (Ice Lake i7-1065G7, MacBookPro16,2 SMBIOS, Sonoma 14.8.5).

Prior context: hibernate-25 (suspend-to-disk) was previously tested and confirmed broken at firmware level (Insyde failure, deferred). Today's goal is to make S3 work as the alternative.

---

## TL;DR — current state

- **Phase 1: ✓** Native firmware `_S3` restored to Darwin by disabling `SSDT-NameS3-disable.aml` + the `_S3 → XS3_` ACPI rename. macOS now believes S3 is supported.
- **Phase 2: ✓** `pmset` configured: `hibernatemode=0`, `standby=0`, `powernap=0`, `tcpkeepalive=0`, `autopoweroff=0`, `lowbatteryhibernate=0`.
- **Phase 4 (first sleep test): ✗** S3 entry succeeded (fan stopped, LED breathed), but **wake failed at firmware-handoff level**. Display stayed black; system unresponsive. Required hard power-off.
- **Post-mortem**: `pmset -g log` shows `Failure: 0x002A001F : EFI/Bootrom Failure after last point of entry to sleep` — Insyde firmware doesn't return control to macOS cleanly on S3 wake.
- **Significance**: NOT an iGPU/display problem. EXT4 hook (designed to nudge iGPU on wake) never gets a chance to fire because macOS never resumes control.
- **Next experiment**: add `-noDC9` boot-arg (canonical Ice Lake S3 wake fix per acidanthera/bugtracker #1207).

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

## Next experiment

**Add `-noDC9` boot-arg.** Single token addition to existing boot-args, single-variable test. Reboot, retry `pmset sleepnow`, observe wake.

Predictions:
1. Wake succeeds → `-noDC9` was the missing piece. Done.
2. Same `0x002A001F` → next try is `-hbfx-disable-patch-pci` or `igfxfw=2`.
3. Different failure code → useful diagnostic; informs next experiment.
4. Boot doesn't complete → revert via plistlib edit, easy.

Recovery if anything breaks: emulated NVRAM in place protects against Insyde NVRAM wipe. Any boot issue can be fixed by editing `config.plist` from external boot.

---

## Resume instructions for next session

1. Boot, verify boot-args show `-noDC9` via `sysctl kern.bootargs`
2. Verify pmset still has `hibernatemode=0` etc. (these may not survive reboot if NVRAM doesn't persist them — re-set if needed)
3. `pmset sleepnow`
4. Wait 30 sec, press key
5. Capture: `pmset -g log | tail -50` and `/usr/bin/log show --last 5m --predicate 'subsystem == "com.apple.iokit.IOPMrootDomain"' --info`
6. If wake works: try lid-close/open, then with external display attached
7. If wake fails: report the failure code; layer next intervention from the table above
