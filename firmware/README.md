# Firmware

For MorphOS, use the [2018 OpenBIOS download](PROVENANCE.md#morphos-2018-openbios) and follow the [mac99 quickstart](../docs/MORPHOS-QUICKSTART.md).

## Restore the Mac OS X firmware

From the bundle root, download the VGA OpenBIOS and loader from the pinned UTM sources. These inputs match UTM 4.7.5. The commands verify them before generating either Radeon image.

```sh
(
  set -e
  cd firmware
  mkdir -p vga radeon jaguar
  curl -fL https://raw.githubusercontent.com/utmapp/qemu/39858ec7347616d5f513f72185873fa2e352d7de/pc-bios/openbios-ppc -o vga/openbios-ppc
  curl -fL https://raw.githubusercontent.com/utmapp/UTM/048ca7498ea3a374439149d51739d94c5300bcda/patches/data/qemu-10.0.2-utm/pc-bios/ppc-ndrvloader -o vga/ppc-ndrvloader
  grep '  vga/' SHA256SUMS | shasum -a 256 -c -
  cp vga/ppc-ndrvloader radeon/ppc-ndrvloader
  python3 src/patch-openbios.py vga/openbios-ppc radeon/openbios-ppc
  python3 src/patch-openbios.py vga/openbios-ppc jaguar/openbios-ppc 4966
  shasum -a 256 -c SHA256SUMS
)
```

The Radeon 9700 and Jaguar patches change the PCI display ID and widen the mac99 PCI memory window. The loader also matches `ndrv/ndrvloader` in [Elliot Nunn’s classicvirtio release](https://github.com/elliotnunn/classicvirtio/releases/tag/latest). The hardware-cursor display driver is a separate file. Its [source and regeneration instructions](PROVENANCE.md#ndrv) apply to both launchers.

## What is *not* here, and why

* **The ATI Radeon 9700 PRO ROM** is ATI/AMD copyrighted, so it can't be
  shipped. It's optional; see [docs/ROM.md](../docs/ROM.md).
* **Mac OS X** and its drivers (`ATIRadeon9700.kext` and the OpenGL bundles)
  come from your own install disc.
