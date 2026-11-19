# MorphOS on mac99

This experimental path uses mac99, a G4 CPU, Radeon 9700, 256 MB VRAM and `Vulkan`. Use your own MorphOS ISO and disks. The upstream `ppcosx` installer below the README's MorphOS section installs the Mac OS X project; it does not select this fork.

On Linux x86_64, the [Actions archive](LINUX-ARCHIVE.md) includes the emulator and launcher.

To build from a checkout of `release/morphos-radeon`, initialize its pinned QEMU source and build
on a host with SDL/OpenGL and a working Vulkan driver.

```sh
git submodule update --init --recursive
./ppcosx setup --build
```

Download the checked 2018 `OpenBIOS` input using the [primary download and extraction instructions](../firmware/PROVENANCE.md). Its SHA-256 is `df3d53348721b9c4f85504b44e230e8a9097f393ec2a31e418e44ccf84dc69a7`. The launcher checks that hash before patching a runtime copy; another build needs a separate reviewed patch.

```sh
MORPHOS_ISO=/absolute/path/morphos.iso \
MORPHOS_FIRMWARE="$HOME/.local/share/morphos-qemu/openbios-qemu.elf" \
PPCOSX_QEMU="$PWD/qemu/build/qemu-system-ppc" \
MORPHOS_MACHINE=mac99 tools/morphos-run.sh
```

Set `MORPHOS_NDRV` to select a user-supplied NDRV; otherwise the launcher uses the inherited `firmware/radeon/qemu_vga_hwc.ndrv`. See [firmware provenance](../firmware/README.md). Guest debugging is optional: `MORPHOS_BOOT_ARGS='EDEBUGFLAGS=init ramdebug'`.

For an installed system, set `MORPHOS_HD` and `MORPHOS_BOOT=hd`. Test on a disposable disk clone. A clean public checkout, portable build and visible guest boot still need qualification; see [release preparation](RELEASING.md).
