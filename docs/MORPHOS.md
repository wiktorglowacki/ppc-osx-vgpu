# MorphOS Radeon 9700 on mac99

MorphOS uses its ATIRadeon driver with the emulated Radeon 9700. The AGP
bridge provides the GART and DMA command ring. The R300 Vulkan backend handles
the guest's 3D draws, and the emulated Radeon supplies 2D, scanout, video
overlay and the hardware cursor. The MorphOS color compatibility setting
accounts for the pixel formats used by Ambient.

For an ISO boot or installed disk, see the [mac99 setup guide](MORPHOS-QUICKSTART.md).
The [development guide](DEVELOPING.md) covers profiling and offline tests.
