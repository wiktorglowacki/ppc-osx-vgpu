# MorphOS Radeon launcher for Linux x86_64

Download the artifact named `ppcosx-qemu-linux-x86_64` from a successful [Linux QEMU workflow run](https://github.com/wiktorglowacki/ppc-osx-vgpu/actions/workflows/qemu-linux.yml). Unzip the Actions download, then extract and check the archive.

```sh
tar -xJf ppcosx-qemu-linux-x86_64.tar.xz
cd ppcosx-qemu
sha256sum -c SHA256SUMS
./tools/morphos-run.sh --check
```

Use an x86_64 Linux desktop with SDL/OpenGL, a working Vulkan driver, Bash and Python 3. CI builds the archive on Ubuntu 24.04. On Ubuntu the runtime libraries include `libglib2.0-0t64 libpixman-1-0 libslirp0 libgtk-3-0t64 libsdl2-2.0-0 libepoxy0 libpulse0 libvulkan1 libshaderc1 libspirv-cross-c-shared0`.

On Fedora 44 the archive ran with additional shared libraries. From the extracted directory, run `ldd bin/qemu-system-ppc` to see which libraries your installation needs. If your distribution does not provide the required versions, follow the [native build instructions](https://github.com/wiktorglowacki/ppc-osx-vgpu/blob/release/morphos-radeon/docs/MORPHOS-QUICKSTART.md).

Supply your MorphOS ISO, the checked 2018 OpenBIOS and the patched Radeon NDRV. The [firmware instructions](https://github.com/wiktorglowacki/ppc-osx-vgpu/blob/release/morphos-radeon/firmware/PROVENANCE.md) identify both firmware inputs and their checksums.

```sh
MORPHOS_ISO=/path/to/morphos.iso \
MORPHOS_FIRMWARE=/path/to/openbios-qemu.elf \
MORPHOS_NDRV=/path/to/qemu_vga_hwc.ndrv \
./tools/morphos-run.sh
```

This starts a mac99 G4 with the Vulkan Radeon 9700 and 256 MB VRAM. The launcher writes its patched firmware and logs under `vm/morphos` in the extracted directory. Set `MORPHOS_RUNTIME_DIR` to use another writable directory.
