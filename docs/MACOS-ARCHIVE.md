# MorphOS Radeon launcher for Apple Silicon

Download the [Apple Silicon ZIP](https://nightly.link/wiktorglowacki/ppc-osx-vgpu/workflows/qemu-macos/release%2Fmorphos-radeon/ppcosx-qemu-macos-arm64.zip). The archive contains a native arm64 QEMU built on macOS 15. Use a native terminal and [Homebrew](https://brew.sh) at `/opt/homebrew` to install its runtime libraries.

```sh
unzip ppcosx-qemu-macos-arm64.zip
tar -xzf ppcosx-qemu-macos-arm64.tar.gz
cd ppcosx-qemu
shasum -a 256 -c SHA256SUMS
xargs brew install < runtime-formulae.txt
./tools/morphos-run.sh --check
```

Download the [MorphOS Live CD](https://www.morphos-team.net/downloads), the [Radeon display driver](https://raw.githubusercontent.com/wiktorglowacki/ppc-osx-vgpu/release/morphos-radeon/firmware/radeon/qemu_vga_hwc.ndrv) and the [checked OpenBIOS firmware](https://github.com/wiktorglowacki/ppc-osx-vgpu/blob/release/morphos-radeon/firmware/PROVENANCE.md#morphos-2018-openbios). Set these paths to your files.

```sh
MORPHOS_ISO="$HOME/Downloads/morphos.iso" \
MORPHOS_FIRMWARE="$HOME/.local/share/morphos-qemu/openbios-qemu.elf" \
MORPHOS_NDRV="$HOME/Downloads/qemu_vga_hwc.ndrv" \
./tools/morphos-run.sh
```

The launcher uses Cocoa, Vulkan through MoltenVK and CoreAudio. `SOURCE.txt` records the exact revisions of the [bundle](https://github.com/wiktorglowacki/ppc-osx-vgpu) and [QEMU](https://github.com/wiktorglowacki/poweremu-qemu). The Apple Silicon workflow in the bundle contains the build and packaging commands.
