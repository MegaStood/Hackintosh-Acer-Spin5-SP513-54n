# SecureBoot enablement plan — Spin 5 SP513-54N

**Status:** PLAN, not applied. Forward-looking sequence for moving from `SecureBootModel = Disabled` (current) to `SecureBootModel = Default` (T2 simulation matching MacBookPro16,2 → j223).

**Created:** 2026-05-12
**Last updated:** 2026-05-12

---

## Goal

Reach a stable Hackintosh state where OpenCore's T2 SecureBoot simulation is enabled. Effect: macOS treats this machine as having an Apple T2 chip, which can improve compatibility with Apple services (iMessage, FaceTime, Apple Pay, Continuity) and OS update flows that check `IsSecureBootEnabled()`.

## Why this is a plan, not an immediate change

The current state works. Daily-driver hibernate, AC sleep, external display, BT (with reboot-to-recover quirk), and macOS Apple services all functional with `SecureBootModel = Disabled`. The risk/reward of enabling SecureBoot is asymmetric:

- **Reward**: hypothetical improvements to Apple services + better "real Mac" verisimilitude
- **Risk**: kernel collection personalization can break boot in multiple ways; recovery requires recovery USB or alternate boot path

So this is a "do it when the rest is genuinely boring" task, not a "do it next session" task.

## Common misconception to clear up

**DEBUG vs RELEASE kexts are NOT signed differently by Apple.** Both are unsigned by Apple — only the maintainer's signing applies, which doesn't enter into SecureBoot validation. So switching from DEBUG to RELEASE doesn't *directly* enable SecureBoot.

**What SecureBoot actually does with third-party kexts:** when enabled, macOS performs **personalization** — it builds a personalized kernel collection containing the exact hashes of YOUR current kext set. Subsequent boots validate against that collection. Third-party kexts pass validation as long as they remain stable (same hashes) post-personalization.

The DEBUG → RELEASE swap helps *indirectly* by reducing the rate of kext updates (each update = re-personalization needed).

---

## Phased sequence

### Phase 1 — Stabilize the daily-driver baseline (current)

Already done or in progress:
- [x] Hibernate-25 working on AC (rd=82-95 ms across many cycles)
- [x] Short-cycle battery hibernate works (rd=84 ms confirmed)
- [x] Long-cycle battery sleep firmware-bug acknowledged; workaround = workflow change
- [x] ESP filesystem expanded to 228.2 MB usable
- [x] OC logs confirmed writing (earlier "missing logs" claim retracted)
- [x] BT post-resume regression catalogued (workaround: reboot to recover)

Pending:
- [ ] Picker cleanup: remove `fbx64.efi`, `mmx64.efi` (rm or .bak) — see open items in `Hibernate-25-debug-2026-05-08.md`
- [ ] Replace `/EFI/Boot/BOOTx64.efi` with current `OpenCore.efi` for NVRAM-wipe resilience
- [ ] Default-boot pinning (Option A: Ctrl+Enter at picker / Option C: `Misc:Entries` if Ctrl+Enter fails on PS/2 keyboard)
- [ ] One full week of "I forgot the Hackintosh was a Hackintosh" daily-driver experience

**Don't proceed to Phase 2 until Phase 1 is genuinely boring.**

### Phase 2 — DEBUG → RELEASE swap

Goal: replace DEBUG OC + DEBUG kexts with their RELEASE equivalents. This is independent of SecureBoot and worth doing on its own merit (faster boot, less log clutter, smaller ESP footprint).

Steps:
1. Identify current kext versions via `kextstat`. Already known list:
   - `as.vit9696.Lilu (1.7.2)`
   - `com.zxystd.IntelBTPatcher (2.4.0)`
   - `as.acidanthera.BlueToolFixup (2.7.2)`
   - `com.zxystd.IntelBluetoothFirmware (2.4.0)`
   - `com.zxystd.AirportItlwm (2.3.0)`
   - `as.vit9696.VirtualSMC (1.3.7)`
   - (verify full list before swap day)
2. Download matching RELEASE builds of OC + each kext (same version numbers — DEBUG ↔ RELEASE only)
3. Back up entire current `/EFI/OC/` to `~/Desktop/EFI-OC-debug-backup-YYYY-MM-DD/`
4. Swap binaries in place
5. `plutil -lint /Volumes/EFI/EFI/OC/config.plist` (just sanity)
6. Reboot, verify:
   - Boot succeeds
   - All kexts still loading (`kextstat | grep <each>`)
   - Hibernate-25 still works
   - BT, WiFi, external display, audio all still functional
7. Run daily-driver workflow for ~1 week
8. If anything regresses, revert from the backup

**Note:** RELEASE OpenCore produces a smaller log buffer per boot. If anything goes wrong, less detail to debug from. Keep one DEBUG OC build accessible (backup) for diagnostic sessions if needed.

### Phase 3 — SecureBootModel enable

Goal: flip `Misc:Security:SecureBootModel` from `Disabled` to `Default` (auto-resolves to `j223` for MacBookPro16,2 SMBIOS).

**Prerequisites checklist** (all must pass):
- [ ] Phase 2 complete and stable for ~1 week
- [ ] No kext updates planned for the next 4-6 weeks
- [ ] No pending macOS update (let any update install on `Disabled` first, then proceed)
- [ ] Recovery USB built and tested (boots into macOS recovery successfully)
- [ ] Full config.plist backup stored OFF the ESP (e.g., `~/Documents/EFI-backup-pre-securebootcommit/`)
- [ ] One identified Apple service that demonstrably benefits — otherwise reconsider whether to proceed

Steps:

1. **Generate ApECID** (unique 64-bit non-zero device identity):
   ```bash
   # Generate a random ApECID. Store the value somewhere durable — it's permanent for this machine.
   APECID=$(openssl rand -hex 8)
   echo "Generated ApECID = $APECID  ($((16#$APECID)) in decimal)"
   # Save the decimal value — that's what goes in config.plist as Misc:Security:ApECID
   ```

2. **Edit config.plist** (via PlistBuddy or ProperTree, NOT manual XML — see "No unsolicited config edits" rule):
   ```bash
   # Replace <decimal-value> with the decimal form of the generated ApECID
   sudo /usr/libexec/PlistBuddy -c "Set :Misc:Security:ApECID <decimal-value>" /Volumes/EFI/EFI/OC/config.plist
   sudo /usr/libexec/PlistBuddy -c "Set :Misc:Security:SecureBootModel Default" /Volumes/EFI/EFI/OC/config.plist
   plutil -lint /Volumes/EFI/EFI/OC/config.plist
   ```

3. **Reboot.** macOS will personalize the kernel collection on first boot. Expect:
   - Boot may take 1-5 minutes longer than usual (collection build)
   - Possible single "personalization" reboot triggered automatically
   - Then normal boot

4. **Verify post-boot:**
   ```bash
   nvram -p | grep -iE "apecid|securebootmodel|personalized"
   csrutil status
   # Apple service checks:
   # - Open Messages, sign in, verify it stays signed in
   # - Try Continuity / Handoff with another Apple device
   # - Check System Settings → Apple ID for any re-auth prompts
   ```

5. **If boot fails:**
   - Power off
   - Boot from recovery USB
   - Mount ESP, revert config.plist from backup
   - Reboot to verify recovery

### Phase 4 (optional, deferred) — Vault for tamper detection

Goal: enable `Misc:Security:Vault = Secure` with cryptographic hashes of OC binaries + kexts.

Effect: any unauthorized modification of OC/kexts after vault creation is detected at boot and refused.

Trade-off: every kext update or OC update requires regenerating vault hashes. High-friction for ongoing maintenance. Not strongly recommended unless you have an active threat model.

Defer indefinitely unless specific need arises.

---

## Critical gotchas summary

| Gotcha | Phase | Impact | Mitigation |
|---|---|---|---|
| `ApECID = 0` → SecureBoot won't enable properly | 3 | Boot fails or personalization fails | Generate non-zero 64-bit value; record permanently |
| Changing ApECID later | Post-3 | Effectively becomes "new machine" — iCloud / iMessage tokens re-enroll | Don't change. Treat as permanent. |
| Kext updates after personalization | Post-3 | Personalization invalidated → boot may fail | Boot recovery, revert SecureBoot, update kexts, re-enable |
| OS update during SecureBoot enabled | Post-3 | Update may fail or re-personalize unexpectedly | Update on `Disabled` first, then re-enable |
| DEBUG kexts re-introduced after Phase 3 | Post-3 | Different hashes than personalized → re-personalization | Match what was personalized, or accept re-personalization step |
| Wrong T2 model auto-picked | 3 | Subtle Apple service mismatch | Explicitly set `SecureBootModel = j223` instead of `Default` to be unambiguous |
| Forgetting recovery USB | 3 | Stuck — can't easily revert if boot breaks | Build + test recovery USB BEFORE Phase 3 |

---

## Recovery plan (always available)

If anything goes wrong at any phase:

1. **Boot recovery USB or external macOS installer**
2. **Mount the internal ESP** via Disk Utility or `diskutil mount /dev/disk0s2`
3. **Revert config.plist** from your backup:
   ```bash
   cp ~/Documents/EFI-backup-pre-securebootcommit/config.plist /Volumes/EFI/EFI/OC/config.plist
   ```
4. **Reboot** — should be back to the pre-change state
5. Diagnose what failed from the recovery environment if needed

The recovery USB is your safety net for the entire plan. **Build and test it before starting Phase 2.**

---

## Open questions to revisit

1. **Do we have an actual Apple-service need for T2 simulation?** Currently no documented motivation. If iMessage / FaceTime / Apple Pay / Continuity start failing under `Disabled`, that's the signal to proceed. Until then, this plan is "available but not urgent."

2. **j223 vs Default vs explicit T2 model.** `Default` auto-resolves to the T2 model matching SMBIOS. For MacBookPro16,2 that should be `j223`. Worth setting explicitly (`j223`) rather than relying on auto-detection — removes one variable.

3. **Vault interaction.** If Vault is enabled at Phase 4, every kext or OC binary update requires regenerating hashes via `ocvalidate` + manual signing. Decide if maintenance burden is worth tamper detection.

4. **`csr-active-config` interaction.** Currently `csr-active-config = 00 00 00 00` (full SIP relaxation). When enabling SecureBoot, evaluate whether to tighten SIP (e.g., to `EF 0F 00 00` or stricter). Stricter SIP increases security but may break some current behaviors. Test separately from SecureBoot enable.

5. **AMFI boot-arg.** Current boot-args include `-no_compat_check` but no AMFI-related args. With SecureBoot enabled, some AMFI checks may behave differently. Worth checking `amfi=` / `amfi_get_out_of_my_way=` interactions.

---

## Reference

- `Hibernate-25-debug-2026-05-08.md` — current daily-driver baseline; Phase 1 status
- OpenCore Configuration.pdf — official authoritative reference for `SecureBootModel`, `ApECID`, `Vault` semantics
- Apple's T2 model identifiers: j137, j680, j132, j174, j140k, j780, j213, j140a, j152f, j160, j230k, j214k, j223 (MacBookPro16,2), j215, j185, j185f, j160 (Mac mini), x86legacy (legacy/no-T2 fallback)
