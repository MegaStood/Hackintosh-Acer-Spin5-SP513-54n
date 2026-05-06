// SSDT-EXT4-iGPU-Wake.asl
//
// Speculative post-_WAK iGPU re-notification hook.
//
// Slots into the SSDT-PTSWAKTTS-iGPU.aml wrapper framework as the EXT4
// method, which fires AFTER the firmware's renamed _WAK (ZWAK) returns,
// on every wake event. Only the Darwin path is enabled.
//
// Background:
//   - The firmware's _WAK(3) handler at DSDT line 19867 does extensive
//     platform-side restoration on S3 wake (EC notify, PCIe rescan,
//     Thunderbolt wake, lid state) but DOES NOT send a Notify to
//     \_SB.PCI0.GFX0 to prompt the OS iGPU driver to re-evaluate
//     display state. It only writes the CLID lid-status register and
//     fires IUEH for volume buttons.
//
//   - On the Acer Spin 5 SP513-54N, S3 wake fails with a black screen
//     on the BOE NE135FBM-N41 panel. The macOS iGPU driver
//     (AppleIntelICLLPGraphicsFramebuffer) does not re-init the eDP
//     link cleanly on resume without a hint.
//
// Hypothesis (low confidence):
//   Sending Notify(\_SB.PCI0.GFX0, 0) on S3 wake nudges the OS to
//   re-evaluate the iGPU device, which may trigger _DSM/_DOD/_DOS
//   re-evaluation and a fresh display engine bring-up.
//
// Risk: low. Notifies are advisory — if macOS doesn't act on it,
//       nothing changes. If it acts wrongly, worst case is the same
//       black-screen wake we already have. No NVRAM writes, no
//       firmware state change.
//
// Test prerequisite: pmset hibernate must be disabled at the macOS
//   layer before testing, so a failed wake can't fall through to
//   emergency hibernate + Insyde NVRAM wipe:
//     sudo pmset -a hibernatemode 0
//     sudo pmset -a autopoweroff 0
//     sudo pmset -a standby 0
//     sudo pmset -a lowbatteryhibernate 0
//

DefinitionBlock ("", "SSDT", 2, "OCLT", "EXT4WAK", 0x00000000)
{
    External (\_SB.PCI0.GFX0, DeviceObj)

    Method (EXT4, 1, NotSerialized)
    {
        If (_OSI ("Darwin"))
        {
            // Fire only on S3 wake (Arg0 == 3). Avoid any side effect
            // on shallower or deeper sleep transitions.
            If ((Arg0 == 0x03))
            {
                If (CondRefOf (\_SB.PCI0.GFX0))
                {
                    // Notify code 0 = Bus check.
                    // Asks the OS to re-evaluate this device's state.
                    Notify (\_SB.PCI0.GFX0, Zero)
                }
            }
        }
    }
}
