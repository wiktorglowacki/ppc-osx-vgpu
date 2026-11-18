# MorphOS Radeon 9700 on mac99

MorphOS uses its ATIRadeon driver with the emulated Radeon 9700. The AGP
bridge provides the GART and DMA command ring. The R300 Vulkan backend handles
the guest's 3D draws, and the emulated Radeon supplies 2D, scanout, video
overlay and the hardware cursor. The MorphOS color compatibility setting
accounts for the pixel formats used by Ambient.

For an ISO boot or installed disk, see the [mac99 setup guide](MORPHOS-QUICKSTART.md).
The [development guide](DEVELOPING.md) covers profiling and offline tests.

## Monitor profiles

The [60 Hz profile](../tools/monitor/QEMU%20Monitor%2060Hz) came from a mac99
installation of MorphOS 3.21. On 24 October 2026, the 2048×1152 24-bit mode
was set to 60 Hz in Monitors preferences and saved as
`SYS:Prefs/Presets/Monitors/QEMU Monitor`. The saved mode runs at 60.06 Hz.
The 8- and 16-bit modes at the same resolution still run at 75 Hz.

The [retiming script](../tools/monitor/mkmon.py) generated the
[120 Hz profile](../tools/monitor/QEMU%20Monitor) from that saved file, setting
all 27 mode entries to 120 Hz.
